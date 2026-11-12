#!/usr/bin/env bash
# Shell wrapper, see gen_voip.py for the traffic profile.
exec python3 "$(dirname "$0")/gen_voip.py" "$@"
