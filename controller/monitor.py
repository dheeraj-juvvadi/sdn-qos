"""Monitoring and adaptive feedback (QoS loop stage 5).

Every `interval_s` the monitor sends OFPFlowStatsRequest (table 0) and
OFPPortStatsRequest to every switch. Flow entries carry the class id in
their cookie, so per-class throughput is the sum of per-flow byte deltas.

Measurement points:
  * ingress edge (s1, dpid 1): offered load per class
  * egress edge  (s2, dpid 2): delivered load per class
  * s1 bottleneck port tx bytes: link utilisation

Adaptation rules (adaptive mode only, kept deliberately explainable):
  R1 tighten : utilisation > threshold AND high-priority (HP) delivered
               < 95% of HP offered  -> bulk meter -= step (not below floor)
  R2 relax   : HP idle for `idle_intervals` polls -> bulk meter = max_kbps
  R3 restore : HP active again while bulk meter > cap -> bulk meter = cap
  R4 recover : HP active, no loss, meter < cap -> bulk meter += step
Every change is an OFPMeterMod MODIFY and is logged with reason.
"""
import csv
import json
import os
import time

from controller.policy import CLASS_IDS, CLASS_ORDER

ID_TO_CLASS = {v: k for k, v in CLASS_IDS.items()}
HP_CLASSES = ("voip", "video")


class Monitor(object):
    def __init__(self, app, policy, enforcer, log_dir, mode, bottleneck,
                 ingress_dpid=1, egress_dpid=2):
        self.app = app
        self.policy = policy
        self.enforcer = enforcer
        self.mode = mode
        self.log = app.logger
        self.bottleneck = bottleneck          # (dpid, port_no)
        self.ingress, self.egress = ingress_dpid, egress_dpid
        self.flow_bytes = {}                  # (dpid, flow key) -> (bytes, t)
        self.flow_hot = {}                    # heavy-hitter counters
        self.port_bytes = {}                  # (dpid, port) -> (tx_bytes, t)
        self.class_kbps = {}                  # dpid -> {class: kbps}
        self.util = 0.0
        self.idle_count = 0
        self.events = []
        self.last_flows = {}                  # dpid -> list of flow dicts
        self.series_fh = open(os.path.join(log_dir, "monitor_series.jsonl"), "a")
        path = os.path.join(log_dir, "adaptation_events.csv")
        new = not os.path.exists(path)
        self.ev_fh = open(path, "a", newline="")
        self.ev_w = csv.writer(self.ev_fh)
        if new:
            self.ev_w.writerow(["ts", "dpid", "meter_id", "old_kbps", "new_kbps",
                                "reason", "util", "hp_offered_kbps",
                                "hp_delivered_kbps"])
            self.ev_fh.flush()

    # ---- polling -----------------------------------------------------
    def poll(self, dp):
        parser, ofp = dp.ofproto_parser, dp.ofproto
        dp.send_msg(parser.OFPFlowStatsRequest(dp, table_id=0))
        dp.send_msg(parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))

    def on_flow_stats(self, dp, body):
        now = time.time()
        rates = {c: 0.0 for c in CLASS_ORDER}
        flows = []
        for st in body:
            cname = ID_TO_CLASS.get(st.cookie)
            if cname is None:
                continue
            key = (dp.id, str(st.match))
            prev = self.flow_bytes.get(key)
            self.flow_bytes[key] = (st.byte_count, now)
            kbps = 0.0
            if prev and now > prev[1] and st.byte_count >= prev[0]:
                kbps = (st.byte_count - prev[0]) * 8 / 1000.0 / (now - prev[1])
            rates[cname] += kbps
            flows.append({"class": cname, "priority": st.priority,
                          "match": dict(st.match.items()), "packets": st.packet_count,
                          "bytes": st.byte_count, "kbps": round(kbps, 1)})
            if cname == "best_effort" and dp.id == self.ingress:
                self._heavy_hitter(dp, st, kbps)
        self.class_kbps[dp.id] = rates
        self.last_flows[dp.id] = flows
        if dp.id == self.ingress:
            self._tick(now)

    def on_port_stats(self, dp, body):
        now = time.time()
        for st in body:
            if (dp.id, st.port_no) != tuple(self.bottleneck):
                continue
            prev = self.port_bytes.get((dp.id, st.port_no))
            self.port_bytes[(dp.id, st.port_no)] = (st.tx_bytes, now)
            if prev and now > prev[1]:
                kbps = (st.tx_bytes - prev[0]) * 8 / 1000.0 / (now - prev[1])
                cap = float(self.policy.doc()["link"]["bottleneck_kbps"])
                self.util = kbps / cap

    # ---- heavy-hitter promotion (classifier helper) -------------------
    def _heavy_hitter(self, dp, st, kbps):
        bulk = self.policy.cls("bulk")
        limit = float(bulk.get("heavy_hitter_kbps", 0) or 0)
        if limit <= 0:
            return
        key = str(st.match)
        self.flow_hot[key] = self.flow_hot.get(key, 0) + 1 if kbps > limit else 0
        if self.flow_hot[key] == 3:
            self.app.promote_flow(dp, st.match, "bulk", "heavy-hitter %.0fkbps" % kbps)

    # ---- one control-loop tick (after ingress stats arrive) -----------
    def _tick(self, now):
        offered = self.class_kbps.get(self.ingress, {})
        delivered = self.class_kbps.get(self.egress, {})
        hp_off = sum(offered.get(c, 0.0) for c in HP_CLASSES)
        hp_del = sum(delivered.get(c, 0.0) for c in HP_CLASSES)
        bulk = self.policy.cls("bulk")
        mid = int(bulk["meter_id"])
        rate = self.enforcer.meter_rate(self.ingress, mid)
        if self.mode == "adaptive" and self.policy.uses_meters() and rate:
            self._adapt(now, bulk, mid, rate, hp_off, hp_del)
        rec = {"ts": round(now, 3), "util": round(self.util, 4),
               "offered": {k: round(v, 1) for k, v in offered.items()},
               "delivered": {k: round(v, 1) for k, v in delivered.items()},
               "bulk_meter_kbps": self.enforcer.meter_rate(self.ingress, mid),
               "packet_in": self.app.packet_in_count,
               "packet_in_dropped": self.app.packet_in_dropped}
        self.series_fh.write(json.dumps(rec) + "\n")
        self.series_fh.flush()

    def _adapt(self, now, bulk, mid, rate, hp_off, hp_del):
        ad = self.policy.adaptation()
        cap, top = int(bulk["cap_kbps"]), int(bulk["max_kbps"])
        step = int(ad.get("tighten_step_kbps", 500))
        floor = int(ad.get("bulk_floor_kbps", 1000))
        idle = hp_off < float(ad.get("hp_idle_kbps", 50))
        self.idle_count = self.idle_count + 1 if idle else 0
        congested = self.util > float(ad.get("util_threshold", 0.9))
        hp_losing = hp_off > 0 and hp_del < 0.95 * hp_off
        new, reason = rate, None
        if self.idle_count >= int(ad.get("idle_intervals", 2)):
            if rate != top:
                new, reason = top, "R2 relax: high-priority idle"
        elif not idle and rate > cap:
            new, reason = cap, "R3 restore: high-priority active again"
        elif not idle and congested and hp_losing:
            new, reason = max(floor, rate - step), "R1 tighten: congestion and HP loss"
        elif not idle and not hp_losing and rate < cap:
            new, reason = min(cap, rate + step), "R4 recover: HP healthy"
        if reason and new != rate:
            self.set_bulk_rate(new, reason, hp_off, hp_del)

    def set_bulk_rate(self, new, reason, hp_off=0.0, hp_del=0.0):
        """Apply a bulk meter rate on every switch and log the event."""
        mid = int(self.policy.cls("bulk")["meter_id"])
        now = time.time()
        for dp in list(self.app.datapaths.values()):
            old = self.enforcer.meter_rate(dp.id, mid)
            if old is None:
                continue
            self.enforcer.set_meter_rate(dp, mid, new)
            self.ev_w.writerow(["%.3f" % now, dp.id, mid, old, new, reason,
                                "%.3f" % self.util, "%.1f" % hp_off, "%.1f" % hp_del])
            self.log.info("STAGE5 adapt dpid=%s meter=%d %s->%s kbps (%s)",
                          dp.id, mid, old, new, reason)
        self.ev_fh.flush()
        self.events.append({"ts": now, "new_kbps": new, "reason": reason})

    # ---- REST helpers -------------------------------------------------
    def snapshot(self):
        mid = int(self.policy.cls("bulk")["meter_id"])
        return {"mode": self.mode, "util": round(self.util, 4),
                "class_kbps": {str(k): v for k, v in self.class_kbps.items()},
                "bulk_meter_kbps": self.enforcer.meter_rate(self.ingress, mid),
                "packet_in": self.app.packet_in_count,
                "packet_in_dropped": self.app.packet_in_dropped,
                "adaptation_events": len(self.events),
                "of_errors": self.app.of_errors}

    def reset(self):
        self.idle_count = 0
        self.events = []
