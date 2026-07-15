#!/bin/bash
# Double-click to start the G29 speed-sensitive auto-centering daemon.
# Opens in Terminal; press Ctrl-C in the window to stop it.
cd "$(dirname "$0")" || exit 1
if pgrep -f "g29.py auto" >/dev/null 2>&1; then
  echo "G29 auto-centering is already running — nothing to do."
  echo "You can close this window."
  exit 0
fi
echo "──────────────────────────────────────────────"
echo "  G29 auto-centering for ETS2  —  Ctrl-C to stop"
echo "──────────────────────────────────────────────"
exec python3 g29.py auto
