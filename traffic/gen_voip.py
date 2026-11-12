#!/usr/bin/env python3
"""VoIP generator: G.711-like CBR, 20 ms packetisation -> 50 pkt/s,
160 B voice + 12 B RTP header = 172 B UDP payload (~87 kbit/s on the wire),
UDP dst port 10000 (RTP range), DSCP EF (46).
Usage: gen_voip.py send|recv [udp_flow.py options]; defaults fill in the preset."""
import os
import sys

PRESET = {"port": 10000, "pps": 50, "size": 172, "dscp": 46}
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
