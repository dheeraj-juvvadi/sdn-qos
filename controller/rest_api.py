"""Northbound REST API (application plane -> control plane), ryu.app.wsgi.

  GET  /qos/policy    active policy document
  PUT  /qos/policy    partial update, e.g. {"classes":{"bulk":{"cap_kbps":2000}}}
  GET  /qos/flows     classified flow entries per switch (from the last stats poll)
  GET  /qos/stats     per-class kbps, utilisation, meter rate, PacketIn counters
  GET  /qos/classes   class definitions plus active flow count per class
  POST /qos/reset     restore the policy loaded at start-up
"""
import json

from ryu.app.wsgi import ControllerBase, Response, route

from controller.policy import PolicyError

REST_INSTANCE = "qos_app"


def _json(obj, status=200):
    return Response(status=status, content_type="application/json", charset="utf-8",
                    body=json.dumps(obj, indent=2, default=str).encode("utf-8"))


class QosRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(QosRestController, self).__init__(req, link, data, **config)
        self.app = data[REST_INSTANCE]

    @route("qos", "/qos/policy", methods=["GET"])
    def get_policy(self, req, **_):
        return _json(self.app.policy.doc())

    @route("qos", "/qos/policy", methods=["PUT"])
    def put_policy(self, req, **_):
        try:
            patch = json.loads(req.body.decode("utf-8") or "{}")
            doc = self.app.policy.update(patch)
        except (ValueError, PolicyError) as exc:
            return _json({"error": str(exc)}, 400)
        self.app.apply_policy()
        self.app.logger.info("REST PUT /qos/policy %s", patch)
        return _json(doc)

    @route("qos", "/qos/flows", methods=["GET"])
    def get_flows(self, req, **_):
        return _json({str(k): v for k, v in self.app.monitor.last_flows.items()})

    @route("qos", "/qos/stats", methods=["GET"])
    def get_stats(self, req, **_):
        snap = self.app.monitor.snapshot()
        times = self.app.setup_times_us
        snap["flow_setups"] = len(times)
        snap["flow_setup_us_mean"] = round(sum(times) / len(times), 1) if times else None
        return _json(snap)

    @route("qos", "/qos/classes", methods=["GET"])
    def get_classes(self, req, **_):
        out = self.app.policy.classes()
        for name in out:
            out[name]["active_flows"] = sum(
                1 for flows in self.app.monitor.last_flows.values()
                for f in flows if f["class"] == name)
        return _json(out)

    @route("qos", "/qos/reset", methods=["POST"])
    def reset(self, req, **_):
        doc = self.app.policy.reset()
        self.app.monitor.reset()
        self.app.apply_policy()
        self.app.logger.info("REST POST /qos/reset")
        return _json(doc)
