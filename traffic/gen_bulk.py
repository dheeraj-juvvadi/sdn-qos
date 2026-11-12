#!/usr/bin/env python3
"""Bulk generator: iperf3 TCP to port 5201 (nightly-backup stand-in), saturating.
`-P N` parallel streams give the Jain's fairness input; JSON output has
per-stream, per-second throughput.
Usage: gen_bulk.py <server_ip> <seconds> <streams> <json_out>"""
import os
import sys


def client_args(server, seconds, streams, out):
    return ["iperf3", "-c", server, "-p", "5201", "-t", str(seconds), "-P", str(streams),
            "-i", "1", "-J", "--logfile", out]


def server_args(out):
    return ["iperf3", "-s", "-p", "5201", "-1", "-J", "--logfile", out]


if __name__ == "__main__":
    srv, secs, streams, out = sys.argv[1:5]
    os.execvp("iperf3", client_args(srv, secs, streams, out))
