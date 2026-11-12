"""Traffic classifier (QoS loop stage 1: traffic classification).

Version A: header-based, applied to the first packet of a flow at the edge
switch. Rules come from the policy YAML, so changing the policy changes the
classifier without code changes. Classes are tried in priority order
(voip > video > bulk) and the first matching rule wins; anything else is
best_effort. Heavy best-effort flows are promoted to bulk later by the
monitor (see monitor.py), which covers "large sustained throughput".
"""
import csv
import os
import threading
import time

PROTO_NUM = {"tcp": 6, "udp": 17, "icmp": 1}


def _proto(value):
    if value is None:
        return None
    if isinstance(value, str):
        return PROTO_NUM[value.lower()]
    return int(value)


def rule_matches(rule, pkt):
    """Return True if every field present in `rule` matches packet `pkt`.

    pkt keys: ip_proto (int), src_port, dst_port, dscp (ints or None).
    """
    if "ip_proto" in rule and _proto(rule["ip_proto"]) != pkt.get("ip_proto"):
        return False
    if "dst_port" in rule and rule["dst_port"] != pkt.get("dst_port"):
        return False
    if "src_port" in rule and rule["src_port"] != pkt.get("src_port"):
        return False
    if "dst_port_range" in rule:
        lo, hi = rule["dst_port_range"]
        port = pkt.get("dst_port")
        if port is None or not lo <= port <= hi:
            return False
    if "dscp" in rule and rule["dscp"] != pkt.get("dscp"):
        return False
    return True


def classify(pkt, classes):
    """Return the class name for packet headers `pkt` given policy `classes`."""
    ordered = sorted(classes.items(), key=lambda kv: -int(kv[1]["priority"]))
    for name, cls in ordered:
        for rule in cls.get("match") or []:
            if rule_matches(rule, pkt):
                return name
    return "best_effort"


class DecisionLog(object):
    """Append-only CSV log of every classification decision."""

    FIELDS = ["ts", "dpid", "src_ip", "dst_ip", "ip_proto", "src_port",
              "dst_port", "dscp", "cls", "reason"]

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        new = not os.path.exists(path)
        self._fh = open(path, "a", newline="")
        self._w = csv.DictWriter(self._fh, fieldnames=self.FIELDS)
        if new:
            self._w.writeheader()
            self._fh.flush()

    def write(self, dpid, pkt, cls, reason="first-packet"):
        row = {"ts": "%.6f" % time.time(), "dpid": dpid, "cls": cls, "reason": reason}
        for key in ("src_ip", "dst_ip", "ip_proto", "src_port", "dst_port", "dscp"):
            row[key] = pkt.get(key)
        with self._lock:
            self._w.writerow(row)
            self._fh.flush()
