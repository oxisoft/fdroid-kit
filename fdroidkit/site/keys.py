"""The repo signing fingerprint, read from what was actually published.

F-Droid clients pin the SHA-256 of the certificate that signs entry.jar (index-v1.jar on
old repos). Reading it from the signed jar, not from the keystore or a config value, means the
landing page can only show the key users will really see, and no keystore password is needed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable


class KeyReadError(RuntimeError):
    pass


def _keytool(cmd: list) -> str:
    if not shutil.which(cmd[0]):
        raise KeyReadError("keytool not found: install a JDK (fdroidserver needs one anyway)")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise KeyReadError(f"keytool exited {r.returncode}: {(r.stderr or r.stdout).strip()[-500:]}")
    return r.stdout


def parse_sha256(text: str) -> str:
    """Every distinct SHA256 line in keytool output, as one 64-char upper hex string."""
    found = {m.replace(":", "").upper() for m in re.findall(r"SHA-?256:\s*([0-9A-Fa-f:]{95})", text)}
    if not found:
        raise KeyReadError("no SHA-256 certificate fingerprint in keytool output")
    if len(found) > 1:
        raise KeyReadError(f"the jar has {len(found)} signers, an F-Droid index has exactly one")
    return found.pop()


def jar_fingerprint(jar: Path, runner: Callable | None = None) -> str:
    """SHA-256 of the certificate that signed `jar`, e.g. repo/entry.jar."""
    jar = Path(jar)
    if not jar.exists():
        alt = jar.with_name("index-v1.jar")
        if not alt.exists():
            raise KeyReadError(f"no {jar}: run `fdroid update` with a repo key first")
        jar = alt
    # English labels whatever the host locale is.
    cmd = ["keytool", "-J-Duser.language=en", "-printcert", "-jarfile", str(jar)]
    return parse_sha256((runner or _keytool)(cmd))
