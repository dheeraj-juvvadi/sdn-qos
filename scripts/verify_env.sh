#!/usr/bin/env bash
# Print the environment and verify every tool the project needs. Run as root.
set -u
ok() { echo "  [ok]   $*"; }; bad() { echo "  [FAIL] $*"; FAIL=1; }
FAIL=0
echo "== host"; . /etc/os-release; echo "  $PRETTY_NAME, kernel $(uname -r), $(uname -m)"
echo "== tools"
command -v mn >/dev/null && ok "mininet $(mn --version 2>&1)" || bad "mininet missing"
command -v ovs-vsctl >/dev/null && ok "$(ovs-vsctl --version | head -1)" || bad "OVS missing"
command -v iperf3 >/dev/null && ok "$(iperf3 --version | head -1)" || bad "iperf3 missing"
command -v tcpdump >/dev/null && ok "tcpdump $(tcpdump --version 2>&1 | head -1 | awk '{print $3}')" || bad "tcpdump missing"
command -v ITGSend >/dev/null && ok "D-ITG (ITGSend/ITGRecv) installed" || echo "  [warn] D-ITG not installed (optional)"
R=/opt/sdn-qos/venv-ryu/bin; A=/opt/sdn-qos/venv-analysis/bin
[ -x $R/ryu-manager ] && ok "$($R/ryu-manager --version 2>&1 | tail -1) on $($R/python --version)" || bad "Ryu venv missing"
[ -x $A/python ] && ok "analysis venv $($A/python --version), pandas $($A/python -c 'import pandas;print(pandas.__version__)')" || bad "analysis venv missing"
echo "== OpenFlow 1.3 meters on the kernel datapath"
ovs-vsctl --may-exist add-br vtest -- set bridge vtest protocols=OpenFlow13
if ovs-ofctl -O OpenFlow13 add-meter vtest meter=1,kbps,band=type=drop,rate=1000 2>/dev/null; then
  ok "add-meter works (kernel datapath)"
else
  echo "  [warn] kernel meters unavailable -> set datapath_type=netdev or use queue-only policy"
fi
ovs-ofctl -O OpenFlow13 meter-features vtest | sed -n '2,3p' | sed 's/^/         /'
ovs-vsctl del-br vtest
echo "== Ryu smoke test (load the QoS app, then stop it)"
cd "$(dirname "$0")/.."
QOS_LOG_DIR=/tmp/ryu-verify timeout 6 $R/ryu-manager --ofp-tcp-listen-port 6699 --wsapi-port 8099 \
  controller/qos_controller.py >/tmp/ryu-verify.log 2>&1
grep -q "QoS controller start" /tmp/ryu-verify/controller.log && ok "Ryu QoS app loads" || bad "Ryu app failed (see /tmp/ryu-verify.log)"
[ $FAIL = 0 ] && echo "ALL CHECKS PASSED" || { echo "SOME CHECKS FAILED"; exit 1; }
