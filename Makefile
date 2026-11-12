# SDN-Based QoS Management for Prioritized Network Traffic
# Run these targets INSIDE the Ubuntu 22.04 VM (or a native Ubuntu 22.04 box),
# from the repository root. On macOS/Windows use scripts/host/* first (see README).

RUNS        ?= 5
SWEEP_RUNS  ?= 2
RYU_PY      := /opt/sdn-qos/venv-ryu/bin/python
AN_PY       := /opt/sdn-qos/venv-analysis/bin/python
SUDO        := $(if $(filter 0,$(shell id -u)),,sudo)

.PHONY: help setup verify test smoke run-baseline run-qos run-sweep run-meteronly run-main challenges \
        rest-demo plots evidence run-all clean clean-net

help:
	@echo "make setup        install Mininet/OVS/iperf3/D-ITG + Ryu (py3.9) and analysis (py3.11) venvs"
	@echo "make verify       print tool versions and check OVS meter support"
	@echo "make test         unit tests (classifier, policy)"
	@echo "make smoke        live test: pingall, iperf3, dump flows/meters/queues"
	@echo "make run-baseline baseline (no QoS) x RUNS"
	@echo "make run-qos      SDN-QoS static + adaptive x RUNS"
	@echo "make run-sweep    bandwidth 5/10/20 and bulk 1/2/4 sweep x SWEEP_RUNS"
	@echo "make run-meteronly R1 demo: meter-only policy, static vs adaptive x SWEEP_RUNS"
	@echo "make challenges   Phase 9 limitation experiments"
	@echo "make rest-demo    runtime policy change via REST, verified by iperf3"
	@echo "make plots        parse results + draw figures"
	@echo "make run-all      everything above, end to end (~45 min with RUNS=5)"
	@echo "make clean-net    kill stray experiment processes, reset Mininet"
	@echo "make clean        clean-net + delete generated results"

setup:
	$(SUDO) bash scripts/setup_vm.sh

verify:
	$(SUDO) bash scripts/verify_env.sh

test:
	$(RYU_PY) -m pytest -q tests/test_classifier.py tests/test_policy.py

smoke:
	$(SUDO) python3 tests/smoke_net.py static docs/evidence/smoke

run-baseline:
	$(SUDO) python3 experiments/run_experiments.py --runs $(RUNS) --modes baseline

run-qos:
	$(SUDO) python3 experiments/run_experiments.py --runs $(RUNS) --modes static,adaptive

run-main:
	$(SUDO) python3 experiments/run_experiments.py --runs $(RUNS)

run-sweep:
	$(SUDO) python3 experiments/run_experiments.py --runs 0 --sweep --sweep-runs $(SWEEP_RUNS)

run-meteronly:
	$(SUDO) python3 experiments/run_experiments.py --runs 0 --meter-only --sweep-runs $(SWEEP_RUNS)

challenges:
	$(SUDO) python3 experiments/challenges.py all

rest-demo:
	$(SUDO) python3 experiments/rest_demo.py

plots:
	$(AN_PY) analysis/parse_results.py
	$(AN_PY) analysis/make_plots.py
	$(AN_PY) analysis/summarize.py

evidence:
	$(SUDO) bash scripts/collect_evidence.sh

run-all: verify test smoke run-main run-sweep run-meteronly challenges rest-demo plots evidence
	@echo "done: see experiments/results/ and docs/figures/"

clean-net:
	$(SUDO) bash scripts/cleanup_net.sh

clean: clean-net
	$(SUDO) rm -rf experiments/results/raw experiments/results/challenges \
	    experiments/results/rest_demo experiments/results/figures experiments/results/*.csv
