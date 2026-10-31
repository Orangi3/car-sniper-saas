#!/usr/bin/env bash
# deploy/bootstrap_archive.sh — build the release archive that a new server
# needs for deploy/install.sh. Run it on your computer, in the repo:
#
#   bash deploy/bootstrap_archive.sh APPROVED_SHA [OUT]     # OUT: /tmp/sniper-bootstrap.tgz
#
# It packs only the committed tree of APPROVED_SHA (`git archive`), never the
# working directory: untracked or ignored files (.env, keys, local databases,
# .venv, .git) cannot get in. It stops, and leaves no archive, if:
#   - HEAD is not exactly APPROVED_SHA (a full 40-character SHA);
#   - a file install.sh reads from its own directory is missing;
#   - the commit itself holds a file that looks like a secret (.env, a
#     private key, a database). Templates (*.example, *.sample, *.template)
#     are allowed.
set -euo pipefail

sha=${1:-}; out=${2:-/tmp/sniper-bootstrap.tgz}
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo "usage: bash deploy/bootstrap_archive.sh APPROVED_SHA [OUT] (the full 40-character SHA)"; exit 2; }
cd "$(git rev-parse --show-toplevel)"
head=$(git rev-parse HEAD)
[[ $head == "$sha" ]] || { echo "STOP: HEAD is $head, not the approved $sha (git checkout $sha first)"; exit 1; }

rm -f "$out"
git archive --format=tar.gz --output="$out" "$head"
list=$(tar -tzf "$out")
stop() { rm -f "$out"; echo "STOP: $*"; exit 1; }

# everything install.sh reads from its own directory ("$HERE/...") must be there
need=$(tar -xzOf "$out" deploy/install.sh | grep -o '"$HERE/[^"]*"' | sed 's#"$HERE/#deploy/#; s#"$##' | sort -u)
[[ -n $need ]] || stop "could not read deploy/install.sh from the archive"
for f in deploy/install.sh $need; do
    grep -qxF "$f" <<<"$list" || stop "$f is missing from the archive"
done

# defense in depth: nothing committed that looks like a secret or a database
bad=$(grep -E '(^|/)(\.env(\.[^/]*)?|id_(rsa|dsa|ecdsa|ed25519)|[^/]+\.(pem|key|p12|pfx|db|sqlite3?))$' <<<"$list" \
      | grep -vE '\.(example|sample|template)$' || true)
[[ -z $bad ]] || stop "the commit holds files that look secret, so nothing was packed: $(tr '\n' ' ' <<<"$bad")"

echo "ok: $out holds $(grep -vc '/$' <<<"$list") files, the committed tree of ${head:0:12} only"
