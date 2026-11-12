# SDN-Based QoS Management for Prioritized Network Traffic

Software Defined Networks: Use Case Project Report
SRM Institute of Science and Technology, School of Computing, Dept. of Networking and Communications

---

## Abstract

<!-- ABSTRACT -->

---

## 1. Introduction and motivation

Data-center and enterprise networks carry very different traffic over the same links.
Interactive voice needs low, stable delay: ITU-T G.114 recommends at most 150 ms one way
for good conversational quality. Live video needs a steady few Mbit/s with little loss.
Bulk transfers such as backups only care about finishing eventually. Traditional
networks forward all of it best effort through FIFO queues. When a bulk TCP transfer
fills a shared uplink, the queue in front of that link grows until it overflows. Every
VoIP and video packet then waits behind it (bufferbloat) and some are dropped.
Per-device QoS configuration (class maps, policers, queue profiles on each router) can fix
this, but it is static, error-prone and hard to keep consistent across a network.

SDN separates the control plane from the data plane. One logically centralised
controller sees every edge switch, so it can classify traffic, install consistent priority
and policing rules network-wide, and adapt them to measured load from one program.

**Scenario.** A university data center carries a live video lecture, a VoIP helpdesk call
and a nightly backup over the same uplink. Without QoS the backup starves VoIP and video.
With the SDN scheme in this project, flows are classified at the edge, VoIP and video are
mapped to protected priority queues, and the backup is policed by an OpenFlow meter. A
monitoring loop relaxes that cap when the interactive traffic goes idle, so the backup
can use the leftover bandwidth.

**Objectives.**
1. Design an SDN QoS scheme that classifies and prioritises traffic by application need.
2. Implement it with Ryu and Open vSwitch (OpenFlow 1.3) in Mininet.
3. Measure latency, jitter, loss, throughput and congestion against a non-prioritised baseline.
4. Document the findings and the limitations, and propose future work (ML/DRL prioritisation).

These map onto the review milestones: M1 (controller and topology), M2
(classification and priority rules), M3 (performance against baseline) and M4 (analysis
and documentation).

## 2. Literature review

| # | Paper | Key idea | How it informed this design |
|---|---|---|---|
| 1 | Aguirre Sanchez, Shen and Guo, *DQS: A QoS-driven routing optimization approach in SDN using deep reinforcement learning*, JPDC, 2024 | DRL agent picks paths from link bandwidth, delay, loss and queue state. A lightweight classifier looks at the first 3 packets of a flow. Prototype on Ryu. | Confirms that **Ryu plus early-packet classification** is a workable prototype stack. Our classifier also decides on the first packet at the edge. A learned agent (DQS-style) is the natural replacement for our rule-based adaptation (future work, Section 9). |
| 2 | Gunavathie and Umamaheswari, *MLPRS: A machine learning-based proactive re-routing scheme for flow classification and priority assignment*, J. Engineering Research, 2023 | KNN flow classification assigns priority, then proactive re-routing on high-betweenness links. | Separates **classification** from **priority assignment** the same way we split `classifier.py` from `policy.py`. Our heavy-hitter promotion (best effort above 2 Mbit/s for 3 s becomes bulk) is a simple stand-in for MLPRS's flow-feature classification. |
| 3 | Chen, Lin, Hsu, Sun, Huang and Hsiao, *Adaptive traffic control: OpenFlow-based prioritization strategies for achieving high QoS in SDN*, IEEE TNSM, 2025 | Assigns OpenFlow priority levels with Lagrangian relaxation on an M/G/1 priority-queue model to minimise end-to-end delay. | Motivates **strict priority between classes**: VoIP above video above bulk in both flow-table priority and HTB borrowing priority. Their queueing view also explains our baseline: a FIFO queue in front of a saturated link makes delay grow with buffer occupancy. |
| 4 | Shahriar et al., *Prioritized multi-tenant traffic engineering for dynamic QoS provisioning in autonomous SDN-OpenFlow edge networks*, IEEE, 2024 | OpenFlow **meter tables** reserve bandwidth for high priority and share the rest among lower priorities. | Directly adopted: the bulk class is **policed with an OpenFlow 1.3 meter**, and the adaptive loop retunes the meter rate (`OFPMeterMod MODIFY`) to hand unused high-priority capacity to bulk. |
| 5 | Beshley, Kryvinska, Beshley, Panchenko and Medvetskyi, *Traffic engineering and QoS/QoE supporting techniques for emerging service-oriented SDN* (SOSDN), JCN, 2024 | SLA/QoE-driven adaptive prioritisation and multi-criteria QoS routing. | Shaped the **policy-as-data** design: per-class SLA parameters (guaranteed rate, max rate, DSCP, priority) live in a YAML policy that can be changed at runtime through the northbound REST API. |

## 3. System design

### 3.1 Architecture

The design follows the three SDN planes. Full diagrams are in
[architecture.md](architecture.md).

* **Application plane.** The QoS policy (`policies/default_policy.yaml`) and the operator
  tools that call the northbound REST API.
* **Control plane.** One Ryu application with five modules: classifier, policy model,
  enforcer (meters and queues), monitor and REST API.
* **Data plane.** Two OpenFlow 1.3 Open vSwitch edge switches, each with a two-table
  pipeline, a meter table and HTB priority queues on the bottleneck port.
* **Southbound.** OpenFlow 1.3 over TCP 6633.

### 3.2 Traffic classes and policy

| Class | Match (first packet) | Flow priority | DSCP | HTB queue (min rate, borrow prio) | Meter |
|---|---|---|---|---|---|
| VoIP | UDP dst 5060 or 10000-20000, or DSCP 46 | 300 | EF 46 | q1: 0.5 Mbit/s, prio 0 | none (never limited below demand) |
| Video | UDP dst 5004, or DSCP 34 | 200 | AF41 34 | q2: 4 Mbit/s, prio 1 | none |
| Bulk | TCP 5201 (either direction), TCP 20/21; heavy best-effort flows | 100 | CS1 8 | q3: 1 Mbit/s, prio 2 | meter 3, drop band; 5 Mbit/s cap while VoIP/video active, 9.5 Mbit/s when idle |
| Best effort | everything else | 10 | 0 | q0: 0.5 Mbit/s, prio 3 | none |

All queues may borrow up to the full 10 Mbit/s link rate (`ceil`), so HTB is
work-conserving. The guaranteed rates add up to 6 Mbit/s, below the link capacity.

### 3.3 The five-stage QoS loop

| Stage | Where | Mechanism | Trace in logs |
|---|---|---|---|
| 1. Traffic classification | `classifier.py`, called on PacketIn | first-packet header rules from the policy; heavy-hitter promotion from the monitor | `classification_log.csv` (timestamp, dpid, 5-tuple, DSCP, class, setup time); `STAGE1-3` lines |
| 2. Policy decision | `policy.py` | class gives priority, DSCP, queue id, meter id and rates | `STAGE1-3 ... prio= queue= meter=` |
| 3. Rule installation | `meters_queues.py`, `qos_controller.py` | `OFPFlowMod` (5-tuple, cookie = class id, idle 30 s), `OFPMeterMod`, Meter instruction, `set_field`, `OFPActionSetQueue` | `STAGE3 meter add`, `dump-flows`, `dump-meters` |
| 4. Priority queuing and forwarding | OVS `linux-htb` on `s1-eth4` | queue selected per class; HTB serves min rates first, then lends spare bandwidth in priority order | `queue-stats`, `tc -s class show` |
| 5. Monitoring and feedback | `monitor.py` | Flow and port stats every 1 s. Per-class rates are taken at ingress (s1) and egress (s2), utilisation from s1 port 4. Rules R1-R4 drive `OFPMeterMod MODIFY`. | `monitor_series.jsonl`, `adaptation_events.csv`, `STAGE5 adapt` lines |

**Adaptation rules (stage 5).** They are kept simple so every decision can be explained:

* **R1 tighten.** If utilisation is above 90 % and VoIP+video delivered at s2 is below
  95 % of VoIP+video offered at s1, cut the bulk meter by 500 kbit/s (floor 1 Mbit/s).
* **R2 relax.** If VoIP+video is below 50 kbit/s for 2 polls, raise the bulk meter to its
  maximum (9.5 Mbit/s). This is the work-conserving step.
* **R3 restore.** If VoIP+video becomes active while the bulk meter is above its cap, set
  it back to the cap (5 Mbit/s).
* **R4 recover.** If VoIP+video is active and not losing packets and the meter is below
  the cap, raise it by 500 kbit/s.

### 3.4 Baseline ("traditional network")

The baseline uses the same topology, the same 10 Mbit/s bottleneck and the same
1000-packet buffer. The controller acts as a plain L2 learning switch: no
classification, meters or queue selection, so all traffic shares one FIFO HTB class.
The only difference between baseline and QoS runs is therefore the QoS scheme.

## 4. Implementation

| Module | Lines of note |
|---|---|
| `controller/qos_controller.py` | Ryu app (OpenFlow 1.3). On switch connect it clears the tables, installs meters, an ARP rule, and table-miss entries to the controller in tables 0 and 1. On PacketIn it learns the MAC, classifies (non-baseline modes), installs the 5-tuple entry in table 0 and the `eth_dst` entry in table 1, then re-injects the packet with `OFPP_TABLE` so the first packet gets the same treatment as the rest. It also has a token-bucket PacketIn limiter and counts OpenFlow errors. |
| `controller/classifier.py` | Pure function `classify(headers, classes)`. Rules are tried in class-priority order. Also an append-only CSV decision log. |
| `controller/policy.py` | YAML/JSON loader, validation (dscp range, min ≤ max, all classes present), thread-safe `update()` and `reset()` for the REST API. |
| `controller/meters_queues.py` | `Enforcer`: meter add/modify (kbps, drop band, burst = 100 ms of rate). Builds the per-class instruction list: Meter, write-actions(set_field dscp, set_queue), goto table 1. |
| `controller/monitor.py` | Stats polling thread, per-flow byte deltas keyed by match, per-class rates from the cookie, utilisation, rules R1-R4, heavy-hitter promotion, JSONL time series. |
| `controller/rest_api.py` | `ryu.app.wsgi` controller: `GET/PUT /qos/policy`, `GET /qos/flows`, `GET /qos/stats`, `GET /qos/classes`, `POST /qos/reset`. A PUT re-installs meters and flushes the classified entries, so flows are re-classified under the new policy on their next packet. |
| `topology/topo_qos.py` | Mininet: h1-h3 on s1, h4-h6 on s2, 100 Mbit/s access links, one s1-s2 TCLink (bandwidth and delay are parameters), remote controller 127.0.0.1:6633, `fail-mode=secure`. |
| `topology/setup_queues.sh` | `ovs-vsctl` linux-htb QoS on `s1-eth4` (1 queue in baseline, 4 in QoS), plus a 5 ms netem leaf (limit 1000 packets) under every HTB class. |
| `traffic/udp_flow.py` | CBR UDP sender and receiver. Every packet carries a sequence number and send time. The sender sets IP TOS from the DSCP. |
| `traffic/run_scenario.py` | Starts Ryu and Mininet, warms up ARP/MAC learning with `pingAll`, starts receivers and senders at an agreed absolute time, dumps OVS state mid-congestion, writes `meta.json`. |
| `experiments/run_experiments.py` | N repetitions per configuration, interleaved (baseline, static, adaptive, baseline, ...) so slow drift in the VM affects all modes equally. Resumable, with a lock against concurrent batches. |
| `analysis/*.py` | Parsing, statistics (mean, std, t-based 95 % CI, Welch t-test, Mann-Whitney U), figures and the generated `RESULTS.md`. |

**Problems hit during implementation and how they were solved:**

* **Ryu on modern Python.** Ryu 4.34 fails on Python 3.10+, and its `setup.py` fails on
  setuptools 58+. The fix is a Python 3.9 venv (deadsnakes) with `setuptools==57.5.0`,
  `--no-build-isolation`, `eventlet==0.30.2` and `dnspython==1.16.0`. eventlet 0.30.2
  rejects dnspython 2.x, which pip reported as `ResolutionImpossible`.
* **Meter band support.** `ovs-ofctl meter-features` on the Linux 5.15 kernel datapath
  reports `max_bands:1, band_types: drop`. Meters work, but a `dscp_remark` band does not,
  so remarking uses `set_field` actions.
* **OVS HTB versus Mininet TCLink.** Both use `tc`. OVS replaces the root qdisc on the
  bottleneck port, which removes TCLink's delay. `setup_queues.sh` puts the delay back as a
  netem leaf under each HTB class.
* **First packet treatment.** Sending the PacketOut directly to a port would skip the meter
  and queue. Re-injecting with `OFPP_TABLE` runs the just-installed entry.

## 5. Experimental setup

| Item | Value |
|---|---|
| Host | MacBook (Apple Silicon, 16 GB) running Lima VM: Ubuntu 22.04.5 LTS, kernel 5.15.0-194, aarch64, 4 vCPU, 4 GiB RAM |
| Software | Mininet 2.3.0, Open vSwitch 2.17.12, iperf 3.9, Ryu 4.34 on Python 3.9.25, analysis on Python 3.11.18 (pandas 2.2.2, numpy 1.26.4, scipy 1.13.1, matplotlib 3.9.0) |
| Topology | 2 switches, 6 hosts. Access links 100 Mbit/s. Bottleneck s1-s2 10 Mbit/s (sweep: 5 and 20), 5 ms one way, 1000-packet buffer |
| VoIP | h1 to h4, UDP dst 10000, 50 pkt/s, 172 B payload (G.711 20 ms + RTP header), about 87 kbit/s on the wire, DSCP EF |
| Video | h2 to h5, UDP dst 5004, 312 pkt/s, 1200 B payload, about 3.07 Mbit/s, DSCP AF41 |
| Bulk | h3 to h6, iperf3 TCP (CUBIC) to port 5201, 2 parallel streams (sweep: 1 and 4) |
| Timeline per run | 0-8 s **idle** (VoIP + video), 8-28 s **congested** (+ bulk), 28-38 s **recovery** (bulk alone) |
| Configurations | baseline, SDN-QoS static (classification + meters + queues, fixed 5 Mbit/s bulk cap), SDN-QoS adaptive (static + monitoring loop) |
| Repetitions | 10 runs per main configuration, interleaved. Sweep: 3 runs per point |
| Statistics | mean ± 95 % CI (t distribution). Welch t-test and Mann-Whitney U, baseline against each QoS mode |
