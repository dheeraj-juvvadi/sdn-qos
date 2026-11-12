#!/usr/bin/env python3
"""Video generator: RTP-like CBR video, ~3 Mbit/s:
1200 B UDP payload at 312 pkt/s (~3.07 Mbit/s incl. headers),
UDP dst port 5004 (RTP), DSCP AF41 (34).
Usage: gen_voip.py send|recv [udp_flow.py options]; defaults fill in the preset."""
import os
import sys

PRESET = {"port": 5004, "pps": 312, "size": 1200, "dscp": 34}
UDP_FLOW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "udp_flow.py")


def args(mode, **kw):
    """Build the udp_flow.py argv for this preset."""
    p = dict(PRESET, **kw)
    if mode == "recv":
        keys = ("port", "duration", "out")
    else:
        keys = ("dst", "port", "pps", "size", "dscp", "start_at", "duration", "out")
    argv = ["python3", UDP_FLOW, mode]
    for k in keys:
        argv += ["--" + k.replace("_", "-"), str(p[k])]
    return argv


if __name__ == "__main__":
    # preset flags first, user flags after (argparse keeps the last value)
    mode, rest = sys.argv[1], sys.argv[2:]
    preset = ["--port", str(PRESET["port"])]
    if mode == "send":
        preset += ["--pps", str(PRESET["pps"]), "--size", str(PRESET["size"]),
                   "--dscp", str(PRESET["dscp"])]
    os.execvp("python3", ["python3", UDP_FLOW, mode] + preset + rest)
