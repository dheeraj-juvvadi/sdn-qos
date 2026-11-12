#!/usr/bin/env python3
"""Live smoke test: start Ryu + topology, pingall, iperf3, dump flows/queues.
Usage: sudo python3 tests/smoke_net.py [baseline|static|adaptive] [outdir]"""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mininet.log import setLogLevel
from topology.topo_qos import build_net
from traffic.ryu_proc import start_ryu, stop_ryu

mode = sys.argv[1] if len(sys.argv) > 1 else "static"
out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/smoke"
os.makedirs(out, exist_ok=True)
setLogLevel("warning")
subprocess.call("mn -c >/dev/null 2>&1", shell=True)
ryu = start_ryu(mode, out)
net = build_net(10, 5, qos_mode="baseline" if mode == "baseline" else "qos")
try:
    time.sleep(2)
    loss = net.pingAll(timeout=2)
    print("PINGALL_LOSS_PCT", loss)
    h3, h6 = net.get("h3", "h6")
    h6.cmd("iperf3 -s -D -1")
    time.sleep(0.5)
    print(h3.cmd("iperf3 -c 10.0.0.6 -t 5 | tail -4"))
    def sh(c):
        return subprocess.run(c, shell=True, capture_output=True, text=True).stdout
    with open(os.path.join(out, "dump.txt"), "w") as fh:
        for c in ["ovs-ofctl -O OpenFlow13 dump-flows s1", "ovs-ofctl -O OpenFlow13 dump-meters s1",
                  "ovs-ofctl -O OpenFlow13 meter-stats s1", "ovs-ofctl -O OpenFlow13 queue-stats s1",
                  "ovs-vsctl list qos", "ovs-vsctl list queue", "tc -s class show dev s1-eth4",
                  "tc qdisc show dev s1-eth4"]:
            fh.write("$ %s\n%s\n" % (c, sh(c)))
finally:
    net.stop()
    stop_ryu(ryu)
