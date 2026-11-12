#!/usr/bin/env python3
"""Mininet topology with a single bottleneck link (data plane).

    h1 (VoIP src) --+                      +-- h4 (VoIP sink)
    h2 (video src) -+-- s1 ==bottleneck== s2 -+-- h5 (video sink)
    h3 (bulk src)  -+   port4         port4    +-- h6 (bulk sink)

Access links: 100 Mbit/s. Bottleneck: --bw Mbit/s (default 10), --delay ms (5).
Switches are OVS, OpenFlow 1.3, connected to a remote Ryu controller.
Host i gets 10.0.0.i and MAC 00:00:00:00:00:0i.

Interactive use:  sudo python3 topology/topo_qos.py --cli
"""
import argparse
import os
import subprocess

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

HERE = os.path.dirname(os.path.abspath(__file__))
BOTTLENECK_PORT = "s1-eth4"   # s1 port 4 faces s2 (hosts use ports 1-3)


class QosTopo(Topo):
    def build(self, bw=10, delay_ms=5):
        s1 = self.addSwitch("s1", dpid="0000000000000001", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", dpid="0000000000000002", protocols="OpenFlow13")
        for i in range(1, 7):
            h = self.addHost("h%d" % i, ip="10.0.0.%d/24" % i, mac="00:00:00:00:00:%02x" % i)
            self.addLink(h, s1 if i <= 3 else s2, bw=100)
        # added last so it is port 4 on both switches
        self.addLink(s1, s2, bw=bw, delay="%dms" % delay_ms, max_queue_size=1000)


def build_net(bw=10, delay_ms=5, ctrl_port=6633, qos_mode="baseline"):
    """Create and start the network, then attach HTB queues to the bottleneck."""
    net = Mininet(topo=QosTopo(bw=bw, delay_ms=delay_ms), link=TCLink,
                  switch=OVSSwitch, controller=None, autoSetMacs=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=ctrl_port)
    net.start()
    for sw in net.switches:   # fail-secure: no fallback to standalone L2 mode
        sw.cmd("ovs-vsctl set bridge %s fail-mode=secure" % sw.name)
    setup_queues(bw, delay_ms, qos_mode)
    return net


def setup_queues(bw, delay_ms, qos_mode):
    subprocess.check_call(["bash", os.path.join(HERE, "setup_queues.sh"), BOTTLENECK_PORT,
                           str(bw), qos_mode, str(delay_ms)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bw", type=int, default=10, help="bottleneck Mbit/s")
    ap.add_argument("--delay", type=int, default=5, help="bottleneck one-way delay ms")
    ap.add_argument("--qos", choices=["baseline", "qos"], default="qos")
    ap.add_argument("--cli", action="store_true")
    args = ap.parse_args()
    setLogLevel("info")
    net = build_net(args.bw, args.delay, qos_mode=args.qos)
    if args.cli:
        CLI(net)
    net.stop()


if __name__ == "__main__":
    main()
