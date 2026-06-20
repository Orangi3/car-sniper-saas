#!/bin/bash
# Car Sniper — one-click installer + launcher.
# Double-click this file. First time: installs deps (~30 sec).
# Every other time: starts the sniper and opens the dashboard.

# Always run from the folder this script lives in.
cd "$(dirname "$0")" || exit 1

# Strip macOS quarantine on the other .command files so they double-click cleanly.
xattr -d com.apple.quarantine "STOP.command" 2>/dev/null || true
chmod +x "STOP.command" 2>/dev/null || true

clear
cat <<'BANNER'
====================================================
   CAR SNIPER — Tuscaloosa Metro
====================================================

BANNER

# --- Find a usable Python ---------------------------------------------------
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c "import sys; assert sys.version_info >= (3, 9)" 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    osascript -e 'display dialog "Python 3.9 or newer is required.\n\nOpen Terminal and run:\n  xcode-select --install\n\nThen try again." buttons {"OK"} default button 1 with icon stop'
    exit 1
fi
echo "Using $($PYTHON --version) at $(command -v $PYTHON)"

# --- First-time setup -------------------------------------------------------
if [ ! -d ".venv" ]; then
    echo ""
    echo "First-time setup — installing dependencies (about 30 seconds)..."
    echo "This only runs once."
    echo ""
    if ! "$PYTHON" -m venv .venv 2>&1; then
        osascript -e 'display dialog "Could not create the Python environment.\n\nIf this is the first time you have used Python on this Mac, open Terminal and run:\n  xcode-select --install\n\nThen click START HERE.command again." buttons {"OK"} default button 1 with icon stop'
        exit 1
    fi
    .venv/bin/python -m pip install --quiet --upgrade pip setuptools wheel
    if ! .venv/bin/python -m pip install --quiet -r requirements.txt; then
        osascript -e 'display dialog "Could not install dependencies. Check your internet connection and try again." buttons {"OK"} default button 1 with icon stop'
        exit 1
    fi
    echo "Setup complete."
fi

# --- Stop any old instance --------------------------------------------------
pkill -f "sniper.py daemon" 2>/dev/null
pkill -f "server.py"        2>/dev/null
sleep 1

# --- Start the poller in the background -------------------------------------
echo ""
echo "Starting the sniper..."
nohup .venv/bin/python sniper.py daemon > sniper.log 2>&1 &
echo $! > sniper.pid

# --- Start the dashboard in the background ----------------------------------
nohup .venv/bin/python server.py > server.log 2>&1 &
echo $! > server.pid

# --- Wait for the dashboard to be ready, then open the browser --------------
for i in $(seq 1 15); do
    if curl -fs http://127.0.0.1:8765/api/stats >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Force Safari to drop any cached 127.0.0.1 tab so the new design loads fresh.
# 1. Close any tabs currently pointing to the sniper.
osascript -e '
tell application "Safari"
  try
    repeat with w in (every window)
      repeat with t in (every tab of w whose URL contains "127.0.0.1:8765")
        try
          close t
        end try
      end repeat
    end repeat
  end try
end tell' 2>/dev/null || true

# 2. Open with a cache-busting query param so Safari fetches fresh HTML.
CACHEBUST="$(date +%s)"
open "http://127.0.0.1:8765/?v=$CACHEBUST"

# --- Friendly status --------------------------------------------------------
osascript -e 'display notification "Dashboard open at 127.0.0.1:8765" with title "Car Sniper" sound name "Glass"'

cat <<'DONE'

====================================================
  RUNNING.
====================================================

  Dashboard:  http://127.0.0.1:8765   (just opened in your browser)

  The sniper is now polling Birmingham, Tuscaloosa, and
  Montgomery Craigslist every 2 minutes in the background.

  You can close this Terminal window — it will keep running.

  TO STOP:    double-click  STOP.command
  TO RESTART: double-click  START HERE.command  again

====================================================

DONE

# Give the user a moment to read before the window auto-closes.
sleep 2
