"""Main Ryu application: L2 learning switch + 5-stage QoS pipeline.

Pipeline (OpenFlow 1.3, two tables):
  table 0  classification / metering / queue selection
           - ARP            -> goto table 1                  (proactive)
           - per-flow entry -> [meter] write(dscp, queue) -> goto 1 (reactive)
           - table-miss     -> controller (first packet of each flow)
  table 1  L2 forwarding: eth_dst -> write(output)           (learned)
           - table-miss     -> controller (flood + learn)

Modes (env QOS_MODE):
  baseline - no classification, every packet best-effort (traditional net)
  static   - classification + meters/queues with fixed rates
  adaptive - static + monitoring loop that retunes the bulk meter

Run:  ryu-manager --ofp-tcp-listen-port 6633 --wsapi-port 8080 \
          controller/qos_controller.py
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.app.wsgi import WSGIApplication  # noqa: E402
from ryu.base import app_manager  # noqa: E402
from ryu.controller import ofp_event  # noqa: E402
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls  # noqa: E402
from ryu.lib import hub  # noqa: E402
from ryu.lib.packet import ethernet, ether_types, ipv4, packet, tcp, udp  # noqa: E402
from ryu.ofproto import ofproto_v1_3  # noqa: E402

from controller.classifier import DecisionLog, classify  # noqa: E402
from controller.meters_queues import TABLE_CLASSIFY, TABLE_FORWARD, Enforcer  # noqa: E402
from controller.monitor import Monitor  # noqa: E402
from controller.policy import CLASS_IDS, Policy  # noqa: E402
from controller.rest_api import QosRestController, REST_INSTANCE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env(name, default):
    return os.environ.get(name, default)


class TokenBucket(object):
    """PacketIn rate limiter (security mitigation for flow-table overflow / DoS)."""

    def __init__(self, rate):
        self.rate, self.tokens, self.t = float(rate), float(rate), time.time()

    def allow(self):
        if self.rate <= 0:
            return True
        now = time.time()
        self.tokens = min(self.rate, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class QosController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(QosController, self).__init__(*args, **kwargs)
        self.mode = env("QOS_MODE", "adaptive")
        assert self.mode in ("baseline", "static", "adaptive"), self.mode
        self.log_dir = env("QOS_LOG_DIR", os.path.join(ROOT, "experiments", "results", "live"))
        os.makedirs(self.log_dir, exist_ok=True)
        self.policy = Policy.load(env("QOS_POLICY", os.path.join(ROOT, "policies",
                                                                  "default_policy.yaml")))
        if os.environ.get("QOS_LINK_KBPS"):   # bottleneck size set by the experiment
            doc = self.policy.doc()
            doc["link"]["bottleneck_kbps"] = int(os.environ["QOS_LINK_KBPS"])
            self.policy = Policy(doc)
        self.flow_idle = int(env("QOS_FLOW_IDLE", "30"))
        bdp, bport = env("QOS_BOTTLENECK", "1:4").split(":")
        self.mac_to_port = {}
        self.datapaths = {}
        self.packet_in_count = 0
        self.packet_in_dropped = 0
        self.of_errors = 0
        self.setup_times_us = []
        self.limiter = TokenBucket(env("QOS_PKTIN_LIMIT", "0"))
        self.decisions = DecisionLog(os.path.join(self.log_dir, "classification_log.csv"))
        self.enforcer = Enforcer(self.policy, self.logger)
        self.monitor = Monitor(self, self.policy, self.enforcer, self.log_dir, self.mode,
                               (int(bdp), int(bport)))
        kwargs["wsgi"].register(QosRestController, {REST_INSTANCE: self})
        fh = logging.FileHandler(os.path.join(self.log_dir, "controller.log"))
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(fh)
        self.logger.setLevel(logging.INFO)
        self.logger.info("QoS controller start mode=%s enforcement=%s",
                         self.mode, self.policy.enforcement)
        hub.spawn(self._poll_loop)

    # ---- helpers -----------------------------------------------------
    def add_flow(self, dp, table, priority, match, inst, cookie=0, idle=0):
        parser = dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(datapath=dp, table_id=table, priority=priority,
                                      match=match, instructions=inst, cookie=cookie,
                                      idle_timeout=idle))

    def _poll_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                self.monitor.poll(dp)
            hub.sleep(float(self.policy.adaptation().get("interval_s", 1.0)))

    # ---- switch connect: proactive rules -------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp
        dp.send_msg(parser.OFPFlowMod(datapath=dp, table_id=ofp.OFPTT_ALL,
                                      command=ofp.OFPFC_DELETE, out_port=ofp.OFPP_ANY,
                                      out_group=ofp.OFPG_ANY))
        to_ctrl = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, [
            parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])]
        goto1 = [parser.OFPInstructionGotoTable(TABLE_FORWARD)]
        if self.mode == "baseline":
            self.add_flow(dp, TABLE_CLASSIFY, 0, parser.OFPMatch(), goto1)
        else:
            self.enforcer.install_meters(dp)
            self.add_flow(dp, TABLE_CLASSIFY, 5,
                          parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP), goto1)
            self.add_flow(dp, TABLE_CLASSIFY, 0, parser.OFPMatch(), to_ctrl)
        self.add_flow(dp, TABLE_FORWARD, 0, parser.OFPMatch(), to_ctrl)
        self.logger.info("switch connected dpid=%s mode=%s", dp.id, self.mode)

    # ---- packet-in: learn, classify, install ----------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        t0 = time.time()
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.packet_in_count += 1
        if not self.limiter.allow():
            self.packet_in_dropped += 1
            return
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        macs = self.mac_to_port.setdefault(dp.id, {})
        macs[eth.src] = in_port
        out_port = macs.get(eth.dst)

        if out_port is not None:  # make sure table 1 knows the destination
            self.add_flow(dp, TABLE_FORWARD, 1, parser.OFPMatch(eth_dst=eth.dst),
                          [parser.OFPInstructionActions(ofp.OFPIT_WRITE_ACTIONS,
                                                        [parser.OFPActionOutput(out_port)])])
        ip = pkt.get_protocol(ipv4.ipv4)
        if msg.table_id == TABLE_CLASSIFY and ip is not None and self.mode != "baseline":
            self._classify_and_install(dp, pkt, ip, t0)

        if out_port is not None:
            actions = [parser.OFPActionOutput(ofp.OFPP_TABLE)]
        else:
            actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                                        in_port=in_port, actions=actions, data=msg.data))

    def _flow_match(self, parser, h):
        fields = dict(eth_type=ether_types.ETH_TYPE_IP, ipv4_src=h["src_ip"],
                      ipv4_dst=h["dst_ip"], ip_proto=h["ip_proto"])
        if h["ip_proto"] == 6:
            fields.update(tcp_src=h["src_port"], tcp_dst=h["dst_port"])
        elif h["ip_proto"] == 17:
            fields.update(udp_src=h["src_port"], udp_dst=h["dst_port"])
        return parser.OFPMatch(**fields)

    def _classify_and_install(self, dp, pkt, ip, t0, forced=None, reason="first-packet"):
        """Stages 1-3: classify the 5-tuple, decide policy, install flow+meter/queue."""
        h = {"src_ip": ip.src, "dst_ip": ip.dst, "ip_proto": ip.proto,
             "dscp": ip.tos >> 2, "src_port": None, "dst_port": None}
        l4 = pkt.get_protocol(tcp.tcp) or pkt.get_protocol(udp.udp)
        if l4 is not None:
            h["src_port"], h["dst_port"] = l4.src_port, l4.dst_port
        classes = self.policy.classes()
        cname = forced or classify(h, classes)                       # stage 1
        cls = classes[cname]                                         # stage 2
        parser = dp.ofproto_parser
        self.add_flow(dp, TABLE_CLASSIFY, int(cls["priority"]),      # stage 3
                      self._flow_match(parser, h),
                      self.enforcer.class_instructions(dp, cls),
                      cookie=CLASS_IDS[cname], idle=self.flow_idle)
        setup_us = (time.time() - t0) * 1e6
        self.setup_times_us.append(setup_us)
        self.decisions.write(dp.id, h, cname, "%s setup_us=%.0f" % (reason, setup_us))
        self.logger.info("STAGE1-3 dpid=%s %s:%s->%s:%s proto=%s class=%s prio=%s queue=%s "
                         "meter=%s", dp.id, h["src_ip"], h["src_port"], h["dst_ip"],
                         h["dst_port"], h["ip_proto"], cname, cls["priority"],
                         cls["queue_id"], cls["meter_id"])

    def promote_flow(self, dp, match, cname, reason):
        """Re-install an existing best-effort flow as another class (heavy hitter)."""
        m = dict(match.items())
        cls = self.policy.cls(cname)
        new_match = dp.ofproto_parser.OFPMatch(**m)
        self.add_flow(dp, TABLE_CLASSIFY, int(cls["priority"]), new_match,
                      self.enforcer.class_instructions(dp, cls),
                      cookie=CLASS_IDS[cname], idle=self.flow_idle)
        h = {"src_ip": m.get("ipv4_src"), "dst_ip": m.get("ipv4_dst"),
             "ip_proto": m.get("ip_proto"), "dscp": None,
             "src_port": m.get("tcp_src", m.get("udp_src")),
             "dst_port": m.get("tcp_dst", m.get("udp_dst"))}
        self.decisions.write(dp.id, h, cname, reason)
        self.logger.info("STAGE1 reclassify dpid=%s %s -> %s (%s)", dp.id, m, cname, reason)

    def apply_policy(self):
        """Push the current policy to all switches (after REST PUT/reset)."""
        bulk = self.policy.cls("bulk")
        for dp in list(self.datapaths.values()):
            ofp, parser = dp.ofproto, dp.ofproto_parser
            if self.mode != "baseline":
                self.enforcer.install_meters(dp)
            # drop classified flows; they are re-classified on their next packet
            for cid in CLASS_IDS.values():
                dp.send_msg(parser.OFPFlowMod(datapath=dp, table_id=TABLE_CLASSIFY,
                                              command=ofp.OFPFC_DELETE, cookie=cid,
                                              cookie_mask=0xffffffffffffffff,
                                              out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY))
        self.logger.info("policy applied: bulk cap=%s", bulk.get("cap_kbps"))

    # ---- stats replies -> monitor -------------------------------------
    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply(self, ev):
        self.monitor.on_flow_stats(ev.msg.datapath, ev.msg.body)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        self.monitor.on_port_stats(ev.msg.datapath, ev.msg.body)

    @set_ev_cls(ofp_event.EventOFPErrorMsg, MAIN_DISPATCHER)
    def error_msg(self, ev):
        """Count switch errors, e.g. OFPFMFC_TABLE_FULL when the flow table overflows."""
        msg = ev.msg
        self.of_errors += 1
        if self.of_errors <= 20 or self.of_errors % 100 == 0:
            self.logger.warning("OFPErrorMsg #%d dpid=%s type=%d code=%d", self.of_errors,
                                msg.datapath.id, msg.type, msg.code)
