#!/usr/bin/env python3
"""Phase 9: demonstrate the limitations named in the Review II deck.

  spof        kill the controller mid-run, fail-secure vs fail-standalone
  scale       raise the new-flow rate, measure PacketIn load and setup delay
  ctrl_delay  add netem delay to the control channel (tcp/6633 on lo)
  table       cap OVS table 0 (flow_limit) and overflow it
  security    new-flow flood with and without the PacketIn rate limiter

Results: experiments/results/challenges/<name>.json (+ summary via make_plots
is not needed; docs/report.md quotes these files).
Usage: sudo python3 experiments/challenges.py [all|spof|scale|ctrl_delay|table|security]
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mininet.log import setLogLevel  # noqa: E402

from topology.topo_qos import build_net  # noqa: E402
from traffic import gen_voip  # noqa: E402
from traffic.ryu_proc import start_ryu, stop_ryu  # noqa: E402

OUT = os.path.join(ROOT, "experiments", "results", "challenges")
BURST = os.path.join(ROOT, "traffic", "flow_burst.py")


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def rest(path):
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=5) as r:
        return json.loads(r.read().decode())


def cpu_seconds(pid):
    """utime+stime of the controller process (and children) in seconds."""
    f = open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")


def ryu_pid(proc):
    out = sh("pgrep -P %d" % proc.pid).split()
    return int(out[0]) if out else proc.pid


def up(name, mode="static", extra=None):
    sh("mn -c >/dev/null 2>&1")
    logdir = os.path.join(OUT, "logs", name)
    sh("rm -rf %s" % logdir)
    ryu = start_ryu(mode, logdir, extra_env=extra or {})
    net = build_net(10, 5, qos_mode="qos")
    time.sleep(2)
    net.pingAll(timeout=1)
    return ryu, net


def down(ryu, net):
    net.stop()
    stop_ryu(ryu)


def clear_be_flows():
    """Remove best-effort (cookie 4) entries so the next ping is a new flow again."""
    for sw in ("s1", "s2"):
        sh("ovs-ofctl -O OpenFlow13 del-flows %s 'cookie=0x4/-1'" % sw)


def first_ping_ms(src, dst_ip):
    """RTT of the first packet of a brand-new flow (includes flow setup)."""
    clear_be_flows()
    out = src.cmd("ping -c 2 -i 0.3 -W 2 %s" % dst_ip)
    rtts = [float(x) for x in re.findall(r"time=([\d.]+) ms", out)]
    return rtts


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name + ".json"), "w") as fh:
        json.dump(data, fh, indent=1)
    print(name, json.dumps(data)[:400])


def spof():
    """Kill the controller while VoIP is running; then try to open a new flow."""
    res = {}
    for fail_mode in ("secure", "standalone"):
        ryu, net = up("spof_" + fail_mode)
        try:
            for sw in net.switches:
                sw.cmd("ovs-vsctl set bridge %s fail-mode=%s" % (sw.name, fail_mode))
            h1, h2, h4, h5 = net.get("h1", "h2", "h4", "h5")
            d = os.path.join(OUT, "logs", "spof_" + fail_mode)
            recv = h4.popen(gen_voip.args("recv", duration=45, out=d + "/voip_recv.csv"))
            t0 = time.time() + 1
            send = h1.popen(gen_voip.args("send", dst="10.0.0.4", start_at=t0, duration=40,
                                          out=d + "/voip_send.json"))
            time.sleep(6)
            clear_be_flows()                      # h2->h5 must be set up after the kill
            t_kill = time.time()
            stop_ryu(ryu)
            ping = h2.cmd("ping -D -i 0.5 -c 60 -W 1 10.0.0.5")
            ok = [float(x) for x in re.findall(r"\[([\d.]+)\] \d+ bytes from", ping)]
            send.wait()
            recv.wait()
            rows = [l.split(",") for l in open(d + "/voip_recv.csv").read().split()[1:]]
            after = [r for r in rows if float(r[1]) > t_kill + 1]
            planned_after = int((t0 + 40 - (t_kill + 1)) * 50)
            res[fail_mode] = {
                "existing_voip_delivered_after_kill_pct":
                    round(100.0 * len(after) / planned_after, 1),
                "new_flow_ping_replies": len(ok), "new_flow_ping_sent": 60,
                "new_flow_first_success_s_after_kill":
                    round(ok[0] - t_kill, 2) if ok else None,
                "flows_left_s1": int(sh("ovs-ofctl -O OpenFlow13 dump-flows s1 | grep -c cookie")
                                     or 0)}
        finally:
            down(ryu, net)
    save("spof", res)


def scale():
    """Increase the new-flow arrival rate; measure PacketIn throughput and setup delay."""
    ryu, net = up("scale")
    rows = []
    try:
        h1, h2 = net.get("h1", "h2")
        pid = ryu_pid(ryu)
        base = 30000
        for rate in (50, 100, 200, 400, 800, 1600):
            n = rate * 3
            s0, c0, t0 = rest("/qos/stats"), cpu_seconds(pid), time.time()
            h1.cmd("python3 %s 10.0.0.4 %d %d %d &" % (BURST, base, n, rate))
            time.sleep(1.5)
            probe = [first_ping_ms(h2, "10.0.0.5") for _ in range(3)]
            time.sleep(3)
            s1, c1, t1 = rest("/qos/stats"), cpu_seconds(pid), time.time()
            base += n
            rows.append({"offered_flows_per_s": rate, "new_flows": n,
                         "packet_in": s1["packet_in"] - s0["packet_in"],
                         "flow_setups": s1["flow_setups"] - s0["flow_setups"],
                         "controller_cpu_pct": round(100 * (c1 - c0) / (t1 - t0), 1),
                         "setup_us_mean_cumulative": s1["flow_setup_us_mean"],
                         "probe_first_rtt_ms": [p[0] if p else None for p in probe],
                         "probe_second_rtt_ms": [p[1] if len(p) > 1 else None for p in probe],
                         "table0_entries_s1": int(sh(
                             "ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 | grep -c cookie"))})
            print(rows[-1])
    finally:
        down(ryu, net)
    save("scale", rows)


def _ctrl_delay(ms):
    sh("tc qdisc del dev lo root 2>/dev/null")
    if ms <= 0:
        return
    sh("tc qdisc add dev lo root handle 1: prio")
    sh("tc qdisc add dev lo parent 1:3 handle 30: netem delay %dms" % ms)
    for d in ("dport", "sport"):
        sh("tc filter add dev lo protocol ip parent 1:0 prio 1 u32 match ip %s 6633 0xffff "
           "flowid 1:3" % d)


def ctrl_delay():
    """Delay only OpenFlow traffic (tcp/6633 on loopback) and time new-flow setup."""
    ryu, net = up("ctrl_delay")
    rows = []
    try:
        h2 = net.get("h2")
        for ms in (0, 5, 20, 50, 100):
            _ctrl_delay(ms)
            time.sleep(1)
            trials = [first_ping_ms(h2, "10.0.0.5") for _ in range(5)]
            first = [t[0] for t in trials if t]
            second = [t[1] for t in trials if len(t) > 1]
            rows.append({"ctrl_one_way_delay_ms": ms, "first_ping_rtt_ms": first,
                         "second_ping_rtt_ms": second,
                         "first_mean": round(sum(first) / len(first), 2) if first else None,
                         "second_mean": round(sum(second) / len(second), 2) if second else None})
            print(rows[-1])
    finally:
        _ctrl_delay(0)
        down(ryu, net)
    save("ctrl_delay", rows)


def _flood(net, ryu, limit_flows, n, rate):
    """Send n new flows at `rate`/s from h3; return switch/controller counters."""
    h2, h3 = net.get("h2", "h3")
    if limit_flows:
        sh("ovs-vsctl -- --id=@ft create Flow_Table flow_limit=%d overflow_policy=refuse "
           "-- set Bridge s1 flow_tables:0=@ft" % limit_flows)
    pid = ryu_pid(ryu)
    s0, c0, t0 = rest("/qos/stats"), cpu_seconds(pid), time.time()
    h3.cmd("python3 %s 10.0.0.6 40000 %d %d &" % (BURST, n, rate))
    time.sleep(n / float(rate) / 2)
    probe = [first_ping_ms(h2, "10.0.0.5") for _ in range(3)]
    time.sleep(n / float(rate) / 2 + 2)
    s1, c1, t1 = rest("/qos/stats"), cpu_seconds(pid), time.time()
    return {"new_flows_sent": n, "rate_per_s": rate,
            "packet_in": s1["packet_in"] - s0["packet_in"],
            "packet_in_dropped_by_limiter": s1["packet_in_dropped"] - s0["packet_in_dropped"],
            "flow_setups": s1["flow_setups"] - s0["flow_setups"],
            "of_errors": s1["of_errors"] - s0["of_errors"],
            "controller_cpu_pct": round(100 * (c1 - c0) / (t1 - t0), 1),
            "table0_entries_s1": int(sh(
                "ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 | grep -c priority")),
            "legit_new_flow_first_rtt_ms": [p[0] if p else None for p in probe]}


def table():
    """Overflow a capped flow table and record what the switch and controller do."""
    res = {"meter_features_s1": None, "runs": {}}
    for label, cap in (("unlimited", 0), ("flow_limit_200", 200)):
        ryu, net = up("table_" + label)
        try:
            if res["meter_features_s1"] is None:
                res["meter_features_s1"] = sh("ovs-ofctl -O OpenFlow13 meter-features s1")
            res["runs"][label] = _flood(net, ryu, cap, 600, 200)
            res["runs"][label]["meters_s1"] = int(sh(
                "ovs-ofctl -O OpenFlow13 dump-meters s1 | grep -c meter="))
        finally:
            down(ryu, net)
    save("table", res)


def security():
    """Flow-table-overflow / PacketIn flood, without and with the PacketIn limiter."""
    res = {}
    for label, limit in (("no_limiter", "0"), ("limiter_200pps", "200")):
        ryu, net = up("security_" + label, extra={"QOS_PKTIN_LIMIT": limit})
        try:
            res[label] = _flood(net, ryu, 0, 6000, 2000)
        finally:
            down(ryu, net)
    save("security", res)


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    setLogLevel("warning")
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    funcs = {"spof": spof, "scale": scale, "ctrl_delay": ctrl_delay, "table": table,
             "security": security}
    for name, fn in funcs.items():
        if which in ("all", name):
            fn()


if __name__ == "__main__":
    main()
