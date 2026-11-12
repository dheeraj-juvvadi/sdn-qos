#!/usr/bin/env python3
"""Run one full experiment: controller + topology + mixed traffic.

Timeline (seconds from t0, defaults):
  0  .. 8   idle       : VoIP (h1->h4) + video (h2->h5) only
  8  .. 28  congested  : + bulk iperf3 TCP (h3->h6), saturating
  28 .. 38  recovery   : VoIP/video stop, bulk alone (tests work conservation)

Usage (root, inside the Linux VM):
  sudo python3 traffic/run_scenario.py --mode adaptive --out experiments/results/x
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mininet.log import setLogLevel  # noqa: E402

from topology.topo_qos import build_net  # noqa: E402
from traffic import gen_bulk, gen_video, gen_voip  # noqa: E402
from traffic.ryu_proc import start_ryu, stop_ryu  # noqa: E402


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def dump_state(out, tag):
    """Save flow/meter/queue tables as evidence."""
    with open(os.path.join(out, "dump_%s.txt" % tag), "w") as fh:
        for c in ["ovs-ofctl -O OpenFlow13 dump-flows s1", "ovs-ofctl -O OpenFlow13 dump-meters s1",
                  "ovs-ofctl -O OpenFlow13 meter-stats s1", "ovs-ofctl -O OpenFlow13 queue-stats s1",
                  "ovs-vsctl list qos", "ovs-vsctl list queue", "tc -s class show dev s1-eth4"]:
            fh.write("$ %s\n%s\n" % (c, sh(c)))


def run(a):
    os.makedirs(a.out, exist_ok=True)
    sh("mn -c >/dev/null 2>&1; pkill -f udp_flow.py; pkill iperf3")
    extra = {"QOS_PKTIN_LIMIT": str(a.pktin_limit), "QOS_LINK_KBPS": str(a.bw * 1000)}
    ryu = start_ryu(a.mode, a.out, policy=a.policy, extra_env=extra)
    net = build_net(a.bw, a.delay, qos_mode="baseline" if a.mode == "baseline" else "qos")
    procs = []
    try:
        time.sleep(2)
        net.pingAll(timeout=1)       # learn MACs before measuring
        h1, h2, h3, h4, h5, h6 = [net.get("h%d" % i) for i in range(1, 7)]
        hp_end = a.idle + a.congested
        total = hp_end + a.recovery
        t0 = time.time() + 3.0
        o = lambda name: os.path.join(a.out, name)  # noqa: E731
        procs.append(h4.popen(gen_voip.args("recv", duration=total + 6, out=o("voip_recv.csv"))))
        procs.append(h5.popen(gen_video.args("recv", duration=total + 6, out=o("video_recv.csv"))))
        procs.append(h6.popen(gen_bulk.server_args(o("bulk_server.json"))))
        time.sleep(1)
        procs.append(h1.popen(gen_voip.args("send", dst="10.0.0.4", start_at=t0,
                                            duration=hp_end, out=o("voip_send.json"))))
        procs.append(h2.popen(gen_video.args("send", dst="10.0.0.5", start_at=t0,
                                             duration=hp_end, out=o("video_send.json"))))
        time.sleep(max(0, t0 + a.idle - time.time()))
        bulk = h3.popen(gen_bulk.client_args("10.0.0.6", a.congested + a.recovery,
                                             a.bulk_flows, o("bulk_client.json")))
        procs.append(bulk)
        time.sleep(max(0, t0 + a.idle + a.congested / 2.0 - time.time()))
        dump_state(a.out, "congested")
        bulk.wait()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        dump_state(a.out, "end")
        meta = {"mode": a.mode, "bw_mbps": a.bw, "delay_ms": a.delay,
                "bulk_flows": a.bulk_flows, "t0": t0, "idle": a.idle,
                "congested": a.congested, "recovery": a.recovery, "run": a.run}
        with open(o("meta.json"), "w") as fh:
            json.dump(meta, fh, indent=1)
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        net.stop()
        stop_ryu(ryu)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["baseline", "static", "adaptive"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bw", type=int, default=10)
    ap.add_argument("--delay", type=int, default=5)
    ap.add_argument("--bulk-flows", type=int, default=2)
    ap.add_argument("--idle", type=float, default=8)
    ap.add_argument("--congested", type=float, default=20)
    ap.add_argument("--recovery", type=float, default=10)
    ap.add_argument("--policy", default=None)
    ap.add_argument("--pktin-limit", type=int, default=0)
    ap.add_argument("--run", type=int, default=0)
    a = ap.parse_args()
    setLogLevel("warning")
    run(a)


if __name__ == "__main__":
    main()
