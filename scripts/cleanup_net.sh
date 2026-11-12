#!/usr/bin/env bash
# Stop any running experiment and reset Mininet/OVS state. Run as root.
# Bracketed patterns ([r]un_...) stop pkill from matching this script itself.
systemctl stop sdnbatch 2>/dev/null || true
pkill -f '[r]un_experiments.py' || true
pkill -f '[r]un_scenario.py' || true
pkill -f '[r]yu-manager' || true
pkill -f '[u]dp_flow.py' || true
pkill -x iperf3 || true
mn -c >/dev/null 2>&1 || true
echo "network state cleaned"
