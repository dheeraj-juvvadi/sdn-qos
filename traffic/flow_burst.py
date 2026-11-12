#!/usr/bin/env python3
"""Open many new flows quickly: one UDP packet to each of `count` destination
ports at `rate` new flows per second. Every packet is a new 5-tuple, so each
one triggers a PacketIn at the edge switch (flow-setup load / table overflow).
Usage: flow_burst.py <dst_ip> <base_port> <count> <rate>"""
import socket
import sys
import time

dst, base, count, rate = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
start = time.time()
for i in range(count):
    wait = start + i / rate - time.time()
    if wait > 0:
        time.sleep(wait)
    s.sendto(b"x" * 64, (dst, base + i))
print("sent %d flows in %.2fs" % (count, time.time() - start))
