#!/bin/bash
# CLOUDFLARE TUNNEL.command
# Manual install of cloudflared (no Homebrew, no sudo).
# Forwards http://127.0.0.1:8765 -> public https://*.trycloudflare.com URL.
# Writes new URL to ngrok-url.txt and auto-opens it in Chrome.
# Keep this Terminal open — closing it kills the tunnel.

set -u
cd "$(dirname "$0")" || exit 1
clear

cat <<'BANNER'
==========================================================
   SNIPER — CLOUDFLARE QUICK TUNNEL (manual install)
==========================================================
Tunneling http://127.0.0.1:8765 -> public https URL.
KEEP THIS TERMINAL OPEN. Closing it kills the tunnel.
==========================================================

BANNER

# 1. Kill any prior tunnel sessions
pkill -f "ssh.*localhost.run"           2>/dev/null || true
pkill -f "ngrok http 8765"              2>/dev/null || true
pkill -f "cloudflared.*localhost:8765"  2>/dev/null || true
pkill -f "cloudflared.*127.0.0.1:8765"  2>/dev/null || true

# 2. Verify backend is up
if ! curl -fs --max-time 3 http://127.0.0.1:8765/health >/dev/null 2>&1; then
    echo "⚠ Backend not responding at http://127.0.0.1:8765/health"
    echo "   Start it first (double-click START HERE.command)."
    read -p "Press return to close." _; exit 1
fi
echo "✓ Local /health OK."

ARCH="$(uname -m)"
echo "✓ Detected arch: $ARCH"

# 3. Manually install cloudflared into project folder if not present
LOCAL_CFD="$(pwd)/cloudflared"
CFD=""
if command -v cloudflared >/dev/null 2>&1; then
    CFD="$(command -v cloudflared)"
elif [ -x "$LOCAL_CFD" ]; then
    CFD="$LOCAL_CFD"
else
    case "$ARCH" in
        arm64)
            DL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz" ;;
        x86_64)
            DL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz" ;;
        *)
            echo "Unknown arch: $ARCH. Aborting."
            read -p "Press return to close." _; exit 1 ;;
    esac
    echo "Downloading cloudflared from Cloudflare's official release for $ARCH..."
    if ! curl -fsSL --max-time 60 -o cloudflared.tgz "$DL"; then
        echo "⚠ Cloudflare download failed. Falling back to ngrok manual install..."
        bash "$(dirname "$0")/NGROK FALLBACK.command" 2>/dev/null && exit 0 || {
            echo "ngrok fallback unavailable. See NGROK FALLBACK.command if present."
            read -p "Press return to close." _; exit 1
        }
    fi
    tar -xzf cloudflared.tgz || { echo "Untar failed"; read -p "Press return." _; exit 1; }
    rm -f cloudflared.tgz
    chmod +x cloudflared
    CFD="$LOCAL_CFD"
fi

echo "✓ cloudflared installed at: $CFD"
"$CFD" --version || true

# 4. Reset URL files
rm -f ngrok-url.txt cloudflared.log

echo ""
echo "Starting tunnel..."
echo "Public URL will appear within ~5 seconds."
echo "----------------------------------------------------------"

# 5. Run cloudflared in the foreground; tee output so we can scrape the URL
{
    "$CFD" tunnel --no-autoupdate --url http://127.0.0.1:8765 2>&1
} | tee cloudflared.log | while IFS= read -r line; do
    echo "$line"
    URL=$(echo "$line" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1)
    if [ -n "$URL" ] && [ ! -s ngrok-url.txt ]; then
        echo "$URL" > ngrok-url.txt
        osascript -e "display notification \"$URL\" with title \"Sniper Public URL\"" 2>/dev/null
        # Auto-open in Chrome so the user (and Claude) can see it immediately
        open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"
        cat <<DONE

==========================================================
  ⟁ PUBLIC URL: $URL
  written to:    ngrok-url.txt
  opened in Chrome (also try $URL/health to verify).
==========================================================

DONE
    fi
done
