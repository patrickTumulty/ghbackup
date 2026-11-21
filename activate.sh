#!/usr/bin/env bash
# Usage: ./activate.sh [--dir DIR] [--schedule "CRON"]   (prompts for anything omitted)
exec python3 "$(dirname "$0")/install.py" install "$@"
