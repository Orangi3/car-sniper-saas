#!/usr/bin/env bash
# One-shot installer. Run from inside the sniper/ directory:  bash setup.sh
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.10+ first."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "→ creating venv .venv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "→ installing deps"
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo
echo "Installed. To run:"
echo "  source .venv/bin/activate"
echo "  python sniper.py daemon         # background poller"
echo "  python server.py                # dashboard at http://127.0.0.1:8765"
echo "  python vin.py 1HGCM82633A123456 # one-off VIN check"
echo "  python comps.py '2015 Mazda Miata'  # one-off comps"
