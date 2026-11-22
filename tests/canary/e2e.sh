#!/usr/bin/env bash
# Canary end to end with the REAL tools: a fresh repo (init), the canary APK pinned
# (add-app), published through the real `fdroid update` (no deploy), landing page rendered
# and verified against the signed index. Catches fdroidserver changes the stand-in in
# tests/fixtures/bin/fdroid cannot see.
#
#   tests/canary/e2e.sh <work dir> <build-tools dir>
# Needs fdroidkit[site] + fdroidserver installed (fdroid on PATH) and a JDK.
set -euo pipefail
work="$1"
bt="$2"
here="$(cd "$(dirname "$0")" && pwd)"
pkg=io.oxisoft.fdroidkit.canary

rm -rf "$work"
mkdir -p "$work"
python3 "$here/real_tools.py" --build-tools "$bt" --out "$work"

# Throwaway index key for this run only; never a real secret.
FDROID_KEYSTORE_PASS="canary-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
export FDROID_KEYSTORE_PASS
repo="$work/fdroid"
mkdir -p "$repo"
fdroidkit --repo "$repo" init --name "fdroidkit canary" --url https://canary.invalid/fdroid/repo \
  --keystore "$work/index.p12" --sdk-path "$(dirname "$(dirname "$bt")")"
fdroidkit --repo "$repo" add-app "$pkg" --discover-signer "$work/canary.apk" --not-play --name "fdroidkit canary"
fdroidkit --repo "$repo" publish "$pkg" 7 1.0.7 --source file --apk "$work/canary.apk" \
  --build-tools "$bt" --no-deploy --no-readback
test -s "$repo/repo/index-v2.json"
test -s "$repo/repo/entry.jar"
grep -q "\"$pkg\"" "$repo/repo/index-v2.json"
fdroidkit --repo "$repo" site
fdroidkit --repo "$repo" verify
echo "canary e2e: green ($(fdroid --version 2>/dev/null | head -1 || echo fdroid ?), build-tools $(basename "$bt"))"
