#!/usr/bin/env python3
"""Phase 6 acceptance: change the policy at runtime via the REST API and show
that the bulk bandwidth share follows (measured with iperf3).

Timeline: bulk iperf3 h3->h6 for 30 s (static mode, bulk cap 5000 kbit/s)
  t=10 s  PUT /qos/policy  bulk cap_kbps -> 2000
  t=20 s  PUT /qos/policy  bulk cap_kbps -> 8000
Every curl command and its response is written to docs/evidence/rest_demo.txt.
Usage: sudo python3 experiments/rest_demo.py
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mininet.log import setLogLevel  # noqa: E402

from topology.topo_qos import build_net  # noqa: E402
from traffic import gen_bulk  # noqa: E402
from traffic.ryu_proc import start_ryu, stop_ryu  # noqa: E402

API = "http://127.0.0.1:8080"
EVID = os.path.join(ROOT, "docs", "evidence")
LOG = os.path.join(ROOT, "experiments", "results", "rest_demo")


def curl(fh, method, path, body=None, show=40):
    cmd = ["curl", "-s", "-X", method, API + path]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    shown = "curl -s -X %s %s%s" % (method, API, path)
    if body is not None:
        shown += " -d '%s'" % json.dumps(body)
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    lines = out.splitlines()
    fh.write("$ %s\n%s%s\n\n" % (shown, "\n".join(lines[:show]),
                                  "\n  ... (%d more lines)" % (len(lines) - show)
                                  if len(lines) > show else ""))
    fh.flush()
    return out


def main():
    setLogLevel("warning")
    os.makedirs(EVID, exist_ok=True)
    subprocess.call("mn -c >/dev/null 2>&1", shell=True)
    ryu = start_ryu("static", LOG)
    net = build_net(10, 5, qos_mode="qos")
    fh = open(os.path.join(EVID, "rest_demo.txt"), "w")
    try:
        time.sleep(2)
        net.pingAll(timeout=1)
        h3, h6 = net.get("h3", "h6")
        h6.popen(gen_bulk.server_args(os.path.join(LOG, "server.json")))
        time.sleep(1)
        out = os.path.join(LOG, "bulk_client.json")
        cli = h3.popen(gen_bulk.client_args("10.0.0.6", 30, 1, out))
        t0 = time.time()
        curl(fh, "GET", "/qos/classes", show=30)
        time.sleep(max(0, t0 + 10 - time.time()))
        curl(fh, "PUT", "/qos/policy", {"classes": {"bulk": {"cap_kbps": 2000}}}, show=0)
        fh.write("(t=10 s) bulk cap set to 2000 kbit/s\n\n")
        time.sleep(max(0, t0 + 15 - time.time()))
        curl(fh, "GET", "/qos/stats")
        curl(fh, "GET", "/qos/flows", show=25)
        time.sleep(max(0, t0 + 20 - time.time()))
        curl(fh, "PUT", "/qos/policy", {"classes": {"bulk": {"cap_kbps": 8000}}}, show=0)
        fh.write("(t=20 s) bulk cap set to 8000 kbit/s\n\n")
        cli.wait()
        curl(fh, "POST", "/qos/reset", show=0)
        curl(fh, "GET", "/qos/policy", show=12)
        d = json.load(open(out))
        per_s = [round(i["sum"]["bits_per_second"] / 1e6, 2) for i in d["intervals"]]
        avg = lambda xs: round(sum(xs) / len(xs), 2)  # noqa: E731
        res = {"bulk_mbps_per_second": per_s,
               "mean_mbps_0_10s_cap5000": avg(per_s[1:10]),
               "mean_mbps_10_20s_cap2000": avg(per_s[11:20]),
               "mean_mbps_20_30s_cap8000": avg(per_s[21:30])}
        fh.write("iperf3 result (h3->h6, 1 TCP stream):\n%s\n" % json.dumps(res, indent=1))
        print(json.dumps(res))
    finally:
        fh.close()
        net.stop()
        stop_ryu(ryu)


if __name__ == "__main__":
    main()
