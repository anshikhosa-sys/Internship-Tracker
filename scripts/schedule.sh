#!/bin/bash
#
# schedule.sh — set up (or remove) the background jobs.
#
#   ./scripts/schedule.sh install     turn both on
#   ./scripts/schedule.sh uninstall   turn both off and remove them
#   ./scripts/schedule.sh status      what's installed, did it work
#   ./scripts/schedule.sh run-now     force a refresh right now, to test
#   ./scripts/schedule.sh restart     reload both (after editing code)
#
# WHAT GETS INSTALLED
# -------------------
# Two macOS "LaunchAgents" — small XML files describing jobs for launchd, the
# system's scheduler and process supervisor:
#
#   com.internship-finder.daily      runs refresh.py once a day
#   com.internship-finder.dashboard  keeps the Flask dashboard running
#
# Together they mean you never type a command: listings update each morning,
# and http://127.0.0.1:5000 is always there when you want to look.
#
# WHY launchd AND NOT cron
# ------------------------
# If your Mac is asleep at the scheduled time, cron skips that day and you
# silently get no update. launchd notices the missed run and fires it when the
# machine wakes. For a morning schedule on a laptop closed overnight, that's
# the difference between working and quietly not.
#
# cron also can't do the second job at all — keeping a process alive and
# restarting it after a reboot or crash is exactly what launchd's KeepAlive is
# for.
#
# NOTHING HERE NEEDS ADMIN RIGHTS. LaunchAgents live in your own home folder
# and run as you. Uninstalling removes them completely.

set -euo pipefail

# ---------------------------------------------------------------------------
# Absolute paths. launchd runs with a bare environment and no working
# directory, so relative paths would silently fail. Derived from this script's
# own location, so moving the project doesn't break anything.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

REFRESH_LABEL="com.internship-finder.daily"
DASH_LABEL="com.internship-finder.dashboard"

PLIST_DIR="$HOME/Library/LaunchAgents"
REFRESH_PLIST="$PLIST_DIR/$REFRESH_LABEL.plist"
DASH_PLIST="$PLIST_DIR/$DASH_LABEL.plist"

LOG_DIR="$PROJECT_DIR/logs"

# What time the daily refresh runs (24-hour clock). Change these, then re-run
# `./scripts/schedule.sh install` to apply.
RUN_HOUR=8
RUN_MINUTE=0

DASH_PORT=5000

# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $0 {install|uninstall|status|run-now|restart}"
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

write_refresh_plist() {
  cat > "$REFRESH_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$REFRESH_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$PROJECT_DIR/refresh.py</string>
    </array>

    <!-- refresh.py writes internships.db relative to the working directory,
         so this must be set or the database lands somewhere unexpected. -->
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <!-- Once a day. If the Mac is asleep, launchd runs it on wake. -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$RUN_HOUR</integer>
        <key>Minute</key>
        <integer>$RUN_MINUTE</integer>
    </dict>

    <!-- Don't fire on install/login — only on the schedule. -->
    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/refresh.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/refresh.error.log</string>
</dict>
</plist>
PLIST
}

write_dashboard_plist() {
  cat > "$DASH_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$DASH_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$PROJECT_DIR/app.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <!-- Start as soon as it's installed, and again at every login. -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart it if it ever stops. This is what makes the dashboard
         "always there" rather than something you have to remember. -->
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/dashboard.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/dashboard.error.log</string>
</dict>
</plist>
PLIST
}

do_install() {
  require_venv
  mkdir -p "$PLIST_DIR" "$LOG_DIR"

  # Unload first so we cleanly replace anything already there.
  launchctl unload "$REFRESH_PLIST" 2>/dev/null || true
  launchctl unload "$DASH_PLIST" 2>/dev/null || true

  write_refresh_plist
  write_dashboard_plist

  launchctl load "$REFRESH_PLIST"
  launchctl load "$DASH_PLIST"

  # Give Flask a moment to bind the port before we report success.
  sleep 3

  printf '%s\n' \
    "Installed. Nothing to run by hand from now on." \
    "" \
    "  Daily refresh:  $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE") every day" \
    "  Dashboard:      http://127.0.0.1:$DASH_PORT  (always on)" \
    "  Logs:           $LOG_DIR/" \
    ""

  if curl -sf -o /dev/null "http://127.0.0.1:$DASH_PORT/"; then
    echo "  Dashboard is up. Bookmark http://127.0.0.1:$DASH_PORT"
  else
    echo "  NOTE: the dashboard didn't answer yet. Check with:"
    echo "    $0 status"
  fi
}

do_uninstall() {
  local found=0
  for plist in "$REFRESH_PLIST" "$DASH_PLIST"; do
    if [ -f "$plist" ]; then
      launchctl unload "$plist" 2>/dev/null || true
      rm -f "$plist"
      found=1
    fi
  done
  if [ "$found" -eq 0 ]; then
    echo "Nothing installed."
  else
    echo "Both jobs removed. Your database and logs were left alone."
  fi
}

do_restart() {
  require_venv
  launchctl unload "$DASH_PLIST" 2>/dev/null || true
  launchctl load "$DASH_PLIST" 2>/dev/null || true
  sleep 2
  echo "Dashboard restarted."
}

report_job() {
  local label="$1" plist="$2" desc="$3"
  if [ ! -f "$plist" ]; then
    echo "  $desc: NOT INSTALLED"
    return
  fi
  if launchctl list | grep -q "$label"; then
    local line exit_code
    line="$(launchctl list | grep "$label")"
    exit_code="$(echo "$line" | awk '{print $2}')"
    echo "  $desc: loaded (last exit status: $exit_code)"
  else
    echo "  $desc: installed but NOT loaded — try '$0 install'"
  fi
}

do_status() {
  echo "JOBS"
  report_job "$REFRESH_LABEL" "$REFRESH_PLIST" "Daily refresh"
  report_job "$DASH_LABEL" "$DASH_PLIST" "Dashboard    "

  echo
  echo "DASHBOARD"
  if curl -sf -o /dev/null "http://127.0.0.1:$DASH_PORT/"; then
    echo "  Responding at http://127.0.0.1:$DASH_PORT"
  else
    echo "  Not responding on port $DASH_PORT"
    if [ -f "$LOG_DIR/dashboard.error.log" ]; then
      echo "  Last error output:"
      tail -n 8 "$LOG_DIR/dashboard.error.log" | sed 's/^/    /'
    fi
  fi

  echo
  echo "LAST REFRESH"
  if [ -f "$LOG_DIR/refresh.log" ]; then
    tail -n 12 "$LOG_DIR/refresh.log" | sed 's/^/  /'
  else
    echo "  Hasn't run yet. Test it with '$0 run-now'."
  fi
}

do_run_now() {
  if [ ! -f "$REFRESH_PLIST" ]; then
    echo "Not installed. Run '$0 install' first."
    exit 1
  fi
  echo "Triggering a refresh..."
  launchctl start "$REFRESH_LABEL"
  sleep 8
  echo
  if [ -f "$LOG_DIR/refresh.log" ]; then
    tail -n 20 "$LOG_DIR/refresh.log" | sed 's/^/  /'
  else
    echo "  (no output yet — check again with '$0 status')"
  fi
}

case "${1:-}" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
  status)    do_status ;;
  run-now)   do_run_now ;;
  restart)   do_restart ;;
  *)         usage ;;
esac
