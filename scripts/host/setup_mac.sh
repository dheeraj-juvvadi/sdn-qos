#!/usr/bin/env bash
# One-command setup on a fresh Mac (Apple Silicon or Intel, macOS 13+).
#   1. installs Homebrew (if missing) and Lima
#   2. creates the "sdn-qos" Ubuntu 22.04 VM with this repo mounted at /mnt/sdn-qos
#   3. runs `make setup verify test` inside the VM
# Usage:  bash scripts/host/setup_mac.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
VM=sdn-qos

if ! command -v brew >/dev/null 2>&1; then
  echo "[mac] installing Homebrew (asks for your password once)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi
command -v limactl >/dev/null 2>&1 || { echo "[mac] installing Lima"; brew install lima; }

if limactl list -q 2>/dev/null | grep -qx "$VM"; then
  echo "[mac] VM $VM exists, starting it"
  limactl start "$VM" >/dev/null 2>&1 || true
else
  echo "[mac] creating VM $VM (downloads Ubuntu 22.04, ~600 MB)"
  sed "s|__PROJECT__|$REPO|" "$REPO/scripts/lima.yaml" > /tmp/sdn-qos-lima.yaml
  limactl start --name="$VM" --tty=false /tmp/sdn-qos-lima.yaml
fi

echo "[mac] provisioning inside the VM (5-10 min the first time)"
limactl shell --workdir /mnt/sdn-qos "$VM" -- make setup verify test
echo
echo "Ready. Run experiments with:  bash scripts/host/run_mac.sh run-all"
echo "Or open a shell in the VM:      limactl shell --workdir /mnt/sdn-qos $VM"
