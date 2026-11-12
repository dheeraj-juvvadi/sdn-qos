#!/usr/bin/env bash
# Attach an OVS linux-htb QoS with priority queues to the bottleneck port.
# Usage: setup_queues.sh <port> <bw_mbps> <baseline|qos> [delay_ms]
#
# OpenFlow cannot create queues, so they are configured here with ovs-vsctl
# and selected by the controller with OFPActionSetQueue(queue_id).
#   baseline : one FIFO queue (queue 0) at line rate  -> "traditional" network
#   qos      : queue 0 best-effort, 1 VoIP, 2 video, 3 bulk; min rates are a
#              share of the link (matches policies/default_policy.yaml at 10 Mbit/s)
# OVS replaces the root qdisc that Mininet's TCLink created, so the link delay
# is re-added as a netem leaf under every HTB class. The leaf limit (1000 pkts)
# is the buffer that causes queueing delay under congestion.
set -euo pipefail
PORT=$1; BW=$2; MODE=$3; DELAY=${4:-5}
BPS=$((BW * 1000000))

ovs-vsctl -- --if-exists clear port "$PORT" qos
ovs-vsctl -- --all destroy qos -- --all destroy queue >/dev/null 2>&1 || true

if [ "$MODE" = "baseline" ]; then
  ovs-vsctl -- set port "$PORT" qos=@q \
    -- --id=@q create qos type=linux-htb other-config:max-rate=$BPS queues:0=@q0 \
    -- --id=@q0 create queue other-config:min-rate=$BPS other-config:max-rate=$BPS \
    >/dev/null
  NQ=1
else
  pct() { echo $((BPS * $1 / 100)); }
  # queue: min-rate share, priority (0 = served first when borrowing spare bandwidth)
  ovs-vsctl -- set port "$PORT" qos=@q \
    -- --id=@q create qos type=linux-htb other-config:max-rate=$BPS \
       queues:0=@be queues:1=@voip queues:2=@video queues:3=@bulk \
    -- --id=@be    create queue other-config:min-rate=$(pct 5)  other-config:max-rate=$BPS other-config:priority=3 \
    -- --id=@voip  create queue other-config:min-rate=$(pct 5)  other-config:max-rate=$BPS other-config:priority=0 \
    -- --id=@video create queue other-config:min-rate=$(pct 40) other-config:max-rate=$BPS other-config:priority=1 \
    -- --id=@bulk  create queue other-config:min-rate=$(pct 10) other-config:max-rate=$BPS other-config:priority=2 \
    >/dev/null
  NQ=4
fi

# Wait for OVS to program the HTB classes, then add the delay leaves.
for _ in $(seq 1 50); do
  [ "$(tc class show dev "$PORT" | grep -c 'class htb 1:')" -ge "$NQ" ] && break
  sleep 0.1
done
for cid in $(tc class show dev "$PORT" | awk '/class htb 1:/ && /parent 1:fffe/ {print $3}'); do
  minor=$((16#${cid#1:}))
  tc qdisc replace dev "$PORT" parent "$cid" handle "$((minor + 10)):" netem delay "${DELAY}ms" limit 1000
done
echo "queues on $PORT ($MODE, ${BW}Mbit/s):"
tc class show dev "$PORT" | sed 's/^/  /'
