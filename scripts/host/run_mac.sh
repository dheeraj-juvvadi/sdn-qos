#!/usr/bin/env bash
# Run any Makefile target inside the Lima VM, e.g.:
#   bash scripts/host/run_mac.sh run-all        (full reproduction, ~75 min)
#   bash scripts/host/run_mac.sh smoke
# Results are written straight into this repo (the VM mounts it).
set -euo pipefail
exec limactl shell --workdir /mnt/sdn-qos sdn-qos -- make "$@"
