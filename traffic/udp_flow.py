#!/usr/bin/env python3
"""Constant-bitrate UDP sender/receiver with per-packet timestamps.

All Mininet hosts share the VM clock, so one-way delay = recv_ts - send_ts
is exact (no clock sync needed). Standard library only (runs with any python3).

  send: udp_flow.py send --dst 10.0.0.4 --port 10000 --pps 50 --size 172 \
            --dscp 46 --start-at <epoch> --duration 20 --out sender.json
  recv: udp_flow.py recv --port 10000 --duration 40 --out recv.csv

Receiver CSV columns: seq,send_ts,recv_ts,bytes
"""
import argparse
import json
import socket
import struct
import time

HDR = struct.Struct("!Id")  # seq, send timestamp


def send(a):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, a.dscp << 2)
    pad = b"\x00" * max(0, a.size - HDR.size)
    interval = 1.0 / a.pps
    start = a.start_at or time.time()
    while time.time() < start:
        time.sleep(min(0.01, start - time.time()))
    n = int(a.duration * a.pps)
    sent = 0
    for seq in range(n):
        target = start + seq * interval
        delay = target - time.time()
        if delay > 0:
            time.sleep(delay)
        try:
            sock.sendto(HDR.pack(seq, time.time()) + pad, (a.dst, a.port))
            sent += 1
        except OSError:
            pass  # local buffer full counts as loss
    with open(a.out, "w") as fh:
        json.dump({"dst": a.dst, "port": a.port, "pps": a.pps, "size": a.size,
                   "dscp": a.dscp, "start": start, "planned": n, "sent": sent,
                   "end": time.time()}, fh)


def recv(a):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("0.0.0.0", a.port))
    sock.settimeout(0.5)
    end = time.time() + a.duration
    with open(a.out, "w") as fh:
        fh.write("seq,send_ts,recv_ts,bytes\n")
        while time.time() < end:
            try:
                data = sock.recv(65535)
            except socket.timeout:
                continue
            now = time.time()
            seq, ts = HDR.unpack_from(data)
            fh.write("%d,%.6f,%.6f,%d\n" % (seq, ts, now, len(data)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--dst", required=True)
    s.add_argument("--port", type=int, required=True)
    s.add_argument("--pps", type=float, required=True)
    s.add_argument("--size", type=int, required=True, help="UDP payload bytes")
    s.add_argument("--dscp", type=int, default=0)
    s.add_argument("--start-at", type=float, default=0.0)
    s.add_argument("--duration", type=float, required=True)
    s.add_argument("--out", required=True)
    r = sub.add_parser("recv")
    r.add_argument("--port", type=int, required=True)
    r.add_argument("--duration", type=float, required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args()
    send(a) if a.cmd == "send" else recv(a)


if __name__ == "__main__":
    main()
