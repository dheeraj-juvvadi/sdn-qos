#!/usr/bin/env bash
# Provision an Ubuntu 22.04 machine (VM or native) for the SDN-QoS project.
# Idempotent: safe to re-run. Must run as root (sudo).
#   - Mininet 2.3.0 + Open vSwitch (Ubuntu 22.04 archive versions)
#   - iperf3, tcpdump, D-ITG, ethtool
#   - Python 3.9 (deadsnakes) venv for Ryu  -> /opt/sdn-qos/venv-ryu
#   - Python 3.11 venv for analysis          -> /opt/sdn-qos/venv-analysis
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_ROOT=/opt/sdn-qos
export DEBIAN_FRONTEND=noninteractive

echo "[setup] apt packages"
apt-get update -qq
apt-get install -y -qq software-properties-common >/dev/null
if ! apt-cache policy python3.9 | grep -q deadsnakes; then
  add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
  apt-get update -qq
fi
apt-get install -y -qq \
  mininet openvswitch-switch openvswitch-testcontroller- \
  iperf3 tcpdump ethtool iputils-ping net-tools curl jq make \
  d-itg \
  python3.9 python3.9-venv python3.9-dev \
  python3.11 python3.11-venv \
  build-essential >/dev/null

systemctl enable --now openvswitch-switch >/dev/null 2>&1 || true

echo "[setup] Ryu venv (Python 3.9)"
mkdir -p "$VENV_ROOT"
[ -x "$VENV_ROOT/venv-ryu/bin/python" ] || python3.9 -m venv "$VENV_ROOT/venv-ryu"
"$VENV_ROOT/venv-ryu/bin/pip" install -q --upgrade "pip==24.0"
# Ryu 4.34's setup.py breaks on setuptools>=58 (use_2to3 / easy_install API).
"$VENV_ROOT/venv-ryu/bin/pip" install -q "setuptools==57.5.0" "wheel==0.42.0"
"$VENV_ROOT/venv-ryu/bin/pip" install -q --no-build-isolation \
  -r "$PROJECT_DIR/requirements-ryu.txt"

echo "[setup] analysis venv (Python 3.11)"
[ -x "$VENV_ROOT/venv-analysis/bin/python" ] || python3.11 -m venv "$VENV_ROOT/venv-analysis"
"$VENV_ROOT/venv-analysis/bin/pip" install -q --upgrade "pip==24.0"
"$VENV_ROOT/venv-analysis/bin/pip" install -q -r "$PROJECT_DIR/requirements-analysis.txt"

echo "[setup] versions"
mn --version
ovs-vsctl --version | head -1
iperf3 --version | head -1
"$VENV_ROOT/venv-ryu/bin/ryu-manager" --version 2>&1 | tail -1
"$VENV_ROOT/venv-ryu/bin/python" --version
"$VENV_ROOT/venv-analysis/bin/python" --version
command -v ITGSend >/dev/null && echo "D-ITG: installed" || echo "D-ITG: not available"
echo "[setup] done"
