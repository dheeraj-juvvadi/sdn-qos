"""Unit tests: synthetic packet headers -> expected traffic class (Phase 3)."""
import os

import pytest

from controller.classifier import DecisionLog, classify
from controller.policy import Policy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASSES = Policy.load(os.path.join(ROOT, "policies", "default_policy.yaml")).classes()
UDP, TCP, ICMP = 17, 6, 1


def pkt(proto, sport=None, dport=None, dscp=0):
    return {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.4", "ip_proto": proto,
            "src_port": sport, "dst_port": dport, "dscp": dscp}


@pytest.mark.parametrize("headers,expected", [
    (pkt(UDP, 40000, 5060), "voip"),          # SIP
    (pkt(UDP, 40000, 10000), "voip"),         # RTP range low edge
    (pkt(UDP, 40000, 20000), "voip"),         # RTP range high edge
    (pkt(UDP, 40000, 15000), "voip"),
    (pkt(ICMP, dscp=46), "voip"),             # DSCP EF, any protocol
    (pkt(TCP, 40000, 443, dscp=46), "voip"),
    (pkt(UDP, 40000, 5004), "video"),         # RTP video
    (pkt(UDP, 40000, 8000, dscp=34), "video"),  # DSCP AF41
    (pkt(TCP, 40000, 5201), "bulk"),          # iperf3 data
    (pkt(TCP, 5201, 40000), "bulk"),          # iperf3 reverse / ACKs
    (pkt(TCP, 40000, 21), "bulk"),            # FTP control
    (pkt(TCP, 40000, 20), "bulk"),            # FTP data
    (pkt(UDP, 40000, 20001), "best_effort"),  # just outside RTP range
    (pkt(UDP, 40000, 9999), "best_effort"),
    (pkt(TCP, 40000, 80), "best_effort"),
    (pkt(UDP, 40000, 5201), "best_effort"),   # port 5201 but UDP
    (pkt(ICMP), "best_effort"),
])
def test_classify(headers, expected):
    assert classify(headers, CLASSES) == expected


def test_priority_order_voip_beats_video():
    # DSCP EF on the video port: the higher-priority class wins
    assert classify(pkt(UDP, 40000, 5004, dscp=46), CLASSES) == "voip"


def test_policy_driven(tmp_path):
    classes = {k: dict(v) for k, v in CLASSES.items()}
    classes["video"]["match"] = [{"ip_proto": "udp", "dst_port": 7000}]
    assert classify(pkt(UDP, 40000, 7000), classes) == "video"
    assert classify(pkt(UDP, 40000, 5004), classes) == "best_effort"


def test_decision_log(tmp_path):
    path = str(tmp_path / "log.csv")
    log = DecisionLog(path)
    log.write(1, pkt(UDP, 40000, 5004), "video")
    lines = open(path).read().strip().splitlines()
    assert lines[0].startswith("ts,dpid,src_ip")
    assert lines[1].endswith(",video,first-packet")
