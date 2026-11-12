"""QoS policy model and loader (QoS loop stage 2: policy decision).

A policy is a set of traffic classes. Each class carries the enforcement
parameters the controller needs: OpenFlow priority, DSCP remark value,
HTB queue id, meter id and rate limits. Policies are loaded from YAML
(or JSON, which is valid YAML) and can be replaced at runtime via REST.
"""
import copy
import json
import threading

import yaml

CLASS_ORDER = ["voip", "video", "bulk", "best_effort"]
CLASS_IDS = {name: i + 1 for i, name in enumerate(CLASS_ORDER)}  # used as cookie
REQUIRED = ("priority", "dscp", "queue_id", "meter_id", "min_kbps", "max_kbps")


class PolicyError(ValueError):
    pass


def validate(doc):
    """Raise PolicyError if the policy document is malformed."""
    if not isinstance(doc, dict) or "classes" not in doc:
        raise PolicyError("policy must be a mapping with a 'classes' key")
    if doc.get("enforcement", "both") not in ("meter", "queue", "both"):
        raise PolicyError("enforcement must be meter, queue or both")
    for name in CLASS_ORDER:
        cls = doc["classes"].get(name)
        if cls is None:
            raise PolicyError("missing class %r" % name)
        for key in REQUIRED:
            if key not in cls:
                raise PolicyError("class %s missing %r" % (name, key))
        if not 0 <= int(cls["dscp"]) <= 63:
            raise PolicyError("class %s dscp out of range" % name)
        if int(cls["min_kbps"]) > int(cls["max_kbps"]):
            raise PolicyError("class %s min_kbps > max_kbps" % name)
    return doc


class Policy(object):
    """Thread-safe holder for the active policy document."""

    def __init__(self, doc):
        self._lock = threading.Lock()
        self._doc = validate(copy.deepcopy(doc))
        self._original = copy.deepcopy(self._doc)

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            text = fh.read()
        doc = json.loads(text) if path.endswith(".json") else yaml.safe_load(text)
        return cls(doc)

    # --- accessors -------------------------------------------------------
    def doc(self):
        with self._lock:
            return copy.deepcopy(self._doc)

    def cls(self, name):
        with self._lock:
            return copy.deepcopy(self._doc["classes"][name])

    def classes(self):
        with self._lock:
            return {n: copy.deepcopy(self._doc["classes"][n]) for n in CLASS_ORDER}

    @property
    def enforcement(self):
        return self._doc.get("enforcement", "both")

    def uses_meters(self):
        return self.enforcement in ("meter", "both")

    def uses_queues(self):
        return self.enforcement in ("queue", "both")

    def adaptation(self):
        return dict(self._doc.get("adaptation", {}))

    # --- mutation (REST PUT / POST reset) --------------------------------
    def update(self, patch):
        """Merge a partial policy, e.g. {"classes": {"bulk": {"cap_kbps": 2000}}}."""
        with self._lock:
            new = copy.deepcopy(self._doc)
            for key, val in patch.items():
                if key == "classes":
                    for cname, fields in val.items():
                        if cname not in new["classes"]:
                            raise PolicyError("unknown class %r" % cname)
                        new["classes"][cname].update(fields)
                elif isinstance(val, dict) and isinstance(new.get(key), dict):
                    new[key].update(val)
                else:
                    new[key] = val
            self._doc = validate(new)
            return copy.deepcopy(self._doc)

    def reset(self):
        with self._lock:
            self._doc = copy.deepcopy(self._original)
            return copy.deepcopy(self._doc)
