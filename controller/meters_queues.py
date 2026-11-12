"""Meter and queue installation helpers (QoS loop stages 3 and 4).

Two enforcement mechanisms sit behind one interface (`Enforcer`):
  * meters - OpenFlow 1.3 meter table, drop band, attached with the Meter
             instruction. The OVS kernel datapath (Linux 5.15) only offers
             the drop band, so DSCP remarking is done with set_field instead
             of a dscp_remark band.
  * queues - OFPActionSetQueue selects an HTB queue on the egress port.
             Queues themselves are created by topology/setup_queues.sh
             because OpenFlow cannot create queues.
The policy's `enforcement` field picks meter, queue or both.
"""

TABLE_CLASSIFY = 0
TABLE_FORWARD = 1


def meter_burst_kbits(rate_kbps):
    # ~100 ms of traffic as burst so TCP is not punished by a tiny bucket.
    return max(100, int(rate_kbps) // 10)


class Enforcer(object):
    def __init__(self, policy, logger):
        self.policy = policy
        self.log = logger
        self.meter_rates = {}  # (dpid, meter_id) -> current kbps

    # ---- meters ------------------------------------------------------
    def _meter_mod(self, dp, command, meter_id, rate_kbps):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        bands = [parser.OFPMeterBandDrop(rate=int(rate_kbps),
                                         burst_size=meter_burst_kbits(rate_kbps))]
        dp.send_msg(parser.OFPMeterMod(datapath=dp, command=command,
                                       flags=ofp.OFPMF_KBPS | ofp.OFPMF_BURST,
                                       meter_id=meter_id, bands=bands))
        self.meter_rates[(dp.id, meter_id)] = int(rate_kbps)

    def initial_rate(self, cls):
        return int(cls.get("cap_kbps", cls["max_kbps"]))

    def install_meters(self, dp):
        """Create one meter per metered class. Deletes stale meters first."""
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_DELETE,
                                       meter_id=ofp.OFPM_ALL))
        if not self.policy.uses_meters():
            return
        for name, cls in self.policy.classes().items():
            mid = int(cls["meter_id"])
            if mid > 0:
                rate = self.initial_rate(cls)
                self._meter_mod(dp, ofp.OFPMC_ADD, mid, rate)
                self.log.info("STAGE3 meter add dpid=%s class=%s meter=%d rate=%dkbps",
                              dp.id, name, mid, rate)

    def set_meter_rate(self, dp, meter_id, rate_kbps):
        """OFPMeterMod MODIFY - used by the adaptive monitor and REST API."""
        self._meter_mod(dp, dp.ofproto.OFPMC_MODIFY, meter_id, rate_kbps)

    def meter_rate(self, dpid, meter_id):
        return self.meter_rates.get((dpid, meter_id))

    # ---- per-class flow instructions --------------------------------
    def class_instructions(self, dp, cls):
        """Instructions for a classified flow entry in table 0.

        meter (optional) -> write-actions [set dscp, set_queue] -> goto table 1.
        Write-actions are used so set_queue runs before the output action
        that table 1 adds to the same action set.
        """
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = []
        mid = int(cls["meter_id"])
        if self.policy.uses_meters() and mid > 0:
            inst.append(parser.OFPInstructionMeter(mid))
        actions = [parser.OFPActionSetField(ip_dscp=int(cls["dscp"]))]
        if self.policy.uses_queues():
            actions.append(parser.OFPActionSetQueue(int(cls["queue_id"])))
        inst.append(parser.OFPInstructionActions(ofp.OFPIT_WRITE_ACTIONS, actions))
        inst.append(parser.OFPInstructionGotoTable(TABLE_FORWARD))
        return inst
