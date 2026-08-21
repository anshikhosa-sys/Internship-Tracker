#!/bin/bash
#
# schedule.sh — install, remove, or inspect the daily automatic refresh.
#
#   ./scripts/schedule.sh install     turn on the daily refresh
#   ./scripts/schedule.sh uninstall   turn it off and remove it
#   ./scripts/schedule.sh status      is it installed? did it run?
#   ./scripts/schedule.sh run-now     trigger it immediately (to test)
#
# WHAT THIS SETS UP
# -----------------
# A macOS "LaunchAgent" — a small XML file describing a job for launchd, the
# system's scheduler. Once installed it runs refresh.py once a day, in the
# background, whether or not the dashboard is open.
#
# WHY launchd AND NOT cron
# ------------------------
# If your Mac is asleep at the scheduled time, cron simply skips that day and
# you'd silently get no update. launchd notices the missed run and fires it as
# soon as the machine wakes. For a laptop that's closed overnight — which is
# exactly when a morning refresh would be scheduled — that difference matters.
#
# NOTHING HERE NEEDS ADMIN RIGHTS. A LaunchAgent lives in your own home
# folder and runs as you. Uninstalling removes the file completely.

set -euo pipefail

# ---------------------------------------------------------------------------
# Work out where everything lives.
#
# The plist needs ABSOLUTE paths — launchd runs with a bare environment and no
# notion of a working directory, so relative paths would silently fail. We
# derive them from this script's own location so it works wherever the project
# is moved to.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

LABEL="com.internship-finder.daily"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"

LOG_DIR="$PROJECT_DIR/logs"
LOG_OUT="$LOG_DIR/refresh.log"
LOG_ERR="$LOG_DIR/refresh.error.log"

# What time of day to run (24-hour clock). Change these two, then re-run
# `./scripts/schedule.sh install` to apply.
RUN_HOUR=8
RUN_MINUTE=0

# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $0 {install|uninstall|status|run-now}"
  exit 1
}

require_venv() {
  if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: no virtual environment found at:"
    echo "  $PYTHON_BIN"
    echo
    echo "Create it first:"
    echo "  cd \"$PROJECT_DIR\""
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt"
    exit 1
  fi
}

do_install() {
  require_venv
  mkdir -p "$PLIST_DIR" "$LOG_DIR"

  # If it's already loaded, unload first so we cleanly replace it.
  launchctl unload "$PLIST_PATH" 2>/dev/null || true

  cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <!-- What to run. First item is the program, the rest are arguments. -->
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$PROJECT_DIR/refresh.py</string>
    </array>

    <!-- refresh.py writes internships.db relative to the working directory,
         so this must be set or the database would land somewhere unexpected. -->
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <!-- Run once a day at the configured time. If the Mac is asleep then,
         launchd runs it when the machine next wakes. -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$RUN_HOUR</integer>
        <key>Minute</key>
        <integer>$RUN_MINUTE</integer>
    </dict>

    <!-- Don't fire on login/install — only on the schedule. -->
    <key>RunAtLoad</key>
    <false/>

    <!-- Keep output so you can check what happened. -->
    <key>StandardOutPath</key>
    <string>$LOG_OUT</string>
    <key>StandardErrorPath</key>
    <string>$LOG_ERR</string>
</dict>
</plist>
PLIST

  launchctl load "$PLIST_PATH"

  printf '%s\n' \
    "Daily refresh installed." \
    "" \
    "  runs at:  $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE") every day" \
    "  project:  $PROJECT_DIR" \
    "  log:      $LOG_OUT" \
    "" \
    "Test it immediately with:  $0 run-now" \
    "Turn it off with:          $0 uninstall"
}

do_uninstall() {
  if [ ! -f "$PLIST_PATH" ]; then
    echo "Not installed — nothing to remove."
    exit 0
  fi
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  rm -f "$PLIST_PATH"
  echo "Daily refresh removed. Your database and logs were left alone."
}

do_status() {
  if [ -f "$PLIST_PATH" ]; then
    echo "Installed:  $PLIST_PATH"
    echo "Scheduled:  $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE") daily"
  else
    echo "Not installed. Run:  $0 install"
    exit 0
  fi

  echo
  if launchctl list | grep -q "$LABEL"; then
    echo "Loaded in launchd: yes"
    # Columns are: PID  LastExitStatus  Label
    echo "  $(launchctl list | grep "$LABEL")"
    echo "  (a '-' PID just means it isn't running this instant, which is"
    echo "   normal; exit status 0 means the last run succeeded)"
  else
    echo "Loaded in launchd: NO — try '$0 install' again"
  fi

  echo
  if [ -f "$LOG_OUT" ]; then
    echo "Last run output:"
    tail -n 15 "$LOG_OUT" | sed 's/^/  /'
  else
    echo "No log yet — it hasn't run. Use '$0 run-now' to test."
  fi
}

do_run_now() {
  if [ ! -f "$PLIST_PATH" ]; then
    echo "Not installed. Run '$0 install' first."
    exit 1
  fi
  echo "Triggering a run..."
  launchctl start "$LABEL"
  sleep 6
  echo
  if [ -f "$LOG_OUT" ]; then
    tail -n 20 "$LOG_OUT" | sed 's/^/  /'
  else
    echo "  (no output yet — check again with '$0 status')"
  fi
}

case "${1:-}" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
  status)    do_status ;;
  run-now)   do_run_now ;;
  *)         usage ;;
esac
