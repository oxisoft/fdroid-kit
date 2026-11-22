#!/usr/bin/env python3
"""Canary: run fdroidkit's APK checks against the REAL Android build-tools.

The unit tests feed recorded tool output to the parsers. That is how build-tools 37 broke
signer parsing unnoticed (FK-18): the real apksigner changed its format, the recordings did
not. This script builds a tiny real APK with aapt2, signs it with fresh keys (one key, then a
Play-style key rotation with a v3.1 lineage) and checks that `parse_signers` and
`check_identity` still read what the installed tools print.

    python tests/canary/real_tools.py --build-tools $ANDROID_HOME/build-tools/37.0.0

Needs a JDK (keytool) and an android.jar (newest under $ANDROID_HOME/platforms, or
--android-jar). Exit 0 = the parsers agree with the tools, 1 = they do not.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run from a checkout, installed or not
from fdroidkit import checks  # noqa: E402

PKG, CODE, NAME = "io.oxisoft.fdroidkit.canary", 7, "1.0.7"
PASS = "canary-pass"  # throwaway keys made and deleted inside a temp dir

MANIFEST = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{PKG}">
  <application android:label="fdroidkit canary" android:hasCode="false"/>
</manifest>
"""


def run(cmd: list, **kw) -> str:
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120, **kw)
    if p.returncode != 0:
        sys.exit(f"FAILED: {' '.join(map(str, cmd))}\n{p.stdout}{p.stderr}")
    return p.stdout


def _ver(p: Path) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", p.name))


def newest(d: Path) -> Path:
    c = sorted((x for x in d.iterdir() if x.is_dir()), key=_ver)
    if not c:
        sys.exit(f"nothing under {d}")
    return c[-1]


def keystore(tmp: Path, name: str) -> tuple[Path, str]:
    ks = tmp / f"{name}.p12"
    run(["keytool", "-genkeypair", "-keystore", ks, "-storetype", "PKCS12", "-alias", name,
         "-keyalg", "RSA", "-keysize", "2048", "-validity", "2", "-dname", f"CN={name}",
         "-storepass", PASS, "-keypass", PASS])
    der = subprocess.run(["keytool", "-exportcert", "-keystore", str(ks), "-alias", name,
                          "-storepass", PASS], capture_output=True, check=True).stdout
    return ks, hashlib.sha256(der).hexdigest()


def signer(ks: Path) -> list:
    return ["--ks", ks, "--ks-pass", f"pass:{PASS}", "--key-pass", f"pass:{PASS}"]


def expect(label: str, got: list[str], want: list[str], raw: str) -> bool:
    ok = sorted(got) == sorted(want)
    print(f"{'ok  ' if ok else 'FAIL'} {label}: parsed {len(got)} signer(s), expected {len(want)}")
    if not ok:
        print(f"  parsed:   {got}\n  expected: {want}\n  apksigner said:\n{raw}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="fdroidkit APK checks against the real build-tools")
    ap.add_argument("--build-tools", type=Path,
                    help="build-tools dir (default newest under $ANDROID_HOME/build-tools)")
    ap.add_argument("--android-jar", type=Path,
                    help="platform android.jar (default newest under $ANDROID_HOME/platforms)")
    ap.add_argument("--out", type=Path,
                    help="also write the one-key APK here, for an end-to-end publish (canary.apk)")
    a = ap.parse_args()
    home = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "~/android").expanduser()
    bt = a.build_tools or newest(home / "build-tools")
    jar = a.android_jar or newest(home / "platforms") / "android.jar"
    print(f"build-tools {bt.name}, apksigner {run([bt / 'apksigner', '--version']).strip()}, {jar.parent.name}")

    ok = True
    with tempfile.TemporaryDirectory(prefix="fdroidkit-canary-") as t:
        tmp = Path(t)
        (tmp / "AndroidManifest.xml").write_text(MANIFEST)
        run([bt / "aapt2", "link", "--manifest", tmp / "AndroidManifest.xml", "-I", jar,
             "--min-sdk-version", "24", "--target-sdk-version", "34",
             "--version-code", str(CODE), "--version-name", NAME, "-o", tmp / "raw.apk"])
        run([bt / "zipalign", "-f", "4", tmp / "raw.apk", tmp / "app.apk"])

        badging = run([bt / "aapt2", "dump", "badging", tmp / "app.apk"]).splitlines()[0]
        try:
            checks.check_identity(badging, PKG, CODE, NAME)
            print("ok   identity: aapt2 badging names package, versionCode, versionName")
        except checks.CheckFailed as e:
            print(f"FAIL {e}")
            ok = False

        old, old_sha = keystore(tmp, "old")
        new, new_sha = keystore(tmp, "new")

        one = tmp / "one.apk"
        run([bt / "apksigner", "sign", *signer(old), "--out", one, tmp / "app.apk"])
        raw = run([bt / "apksigner", "verify", "--print-certs", one])
        ok &= expect("one key", checks.parse_signers(raw), [old_sha], raw)
        if a.out:
            a.out.mkdir(parents=True, exist_ok=True)
            (a.out / "canary.apk").write_bytes(one.read_bytes())
            print(f"     wrote {a.out / 'canary.apk'} (signer {old_sha})")

        # Play-style rotation: v3.0 block signed by the old key for older Android, v3.1 by the
        # new key from SDK 33, with a proof-of-rotation lineage (what Habit Observer 34 has).
        lineage = tmp / "lineage"
        run([bt / "apksigner", "rotate", "--out", lineage,
             "--old-signer", *signer(old), "--new-signer", *signer(new)])
        rot = tmp / "rotated.apk"
        run([bt / "apksigner", "sign", *signer(old), "--next-signer", *signer(new),
             "--lineage", lineage, "--rotation-min-sdk-version", "33", "--out", rot, tmp / "app.apk"])
        raw = run([bt / "apksigner", "verify", "--print-certs", rot])
        ok &= expect("rotated key", checks.parse_signers(raw), [old_sha, new_sha], raw)

        try:
            checks.check_signers(checks.parse_signers(raw), [old_sha])
            print("FAIL rotated key with one pin passed check_signers")
            ok = False
        except checks.CheckFailed:
            print("ok   rotated key with only the old pin is refused")
    print("canary:", "green" if ok else "RED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
