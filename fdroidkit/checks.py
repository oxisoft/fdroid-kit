"""Hard checks run on an APK before it may enter the repo.

Pure functions over tool output and bytes, so they are testable without the Android
SDK. `publish` feeds them the output of `aapt2 dump badging`, `apksigner verify
--print-certs` and the APK's dex files. Each raises CheckFailed with a reason.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path


class CheckFailed(Exception):
    """An APK failed a publish check; the message says which and why."""


_SIGNER_RE = re.compile(
    # "Signer #1 ..." for one key; "Signer (minSdkVersion=..) ..." per key after a
    # Play signing-key rotation. Source Stamp lines start differently and are ignored.
    r"^Signer (?:#\d+|\([^)\n]*\)) certificate SHA-256 digest: ([0-9a-f]{64})",
    re.M,
)
_PIN_RE = re.compile(r"^\s*-\s*([0-9a-f]{64})\s*$", re.M)
_DEX_RE = re.compile(r"classes\d*\.dex")
PAIRIP_MARKER = b"Lcom/pairip/"


def parse_signers(apksigner_output: str) -> list[str]:
    """SHA-256 digests of every signer certificate in `apksigner verify --print-certs`."""
    return _SIGNER_RE.findall(apksigner_output)


def parse_pins(metadata_yml: str) -> list[str]:
    """Digests listed under AllowedAPKSigningKeys in an F-Droid metadata file."""
    return _PIN_RE.findall(metadata_yml)


def check_identity(badging_first_line: str, package: str, version_code: int, version_name: str) -> None:
    """The APK is the package and version we were asked to publish."""
    for want in (f"name='{package}'", f"versionCode='{version_code}'", f"versionName='{version_name}'"):
        if want not in badging_first_line:
            raise CheckFailed(f"identity: expected {want}, got: {badging_first_line}")


def check_signers(signers: list[str], pins: list[str]) -> None:
    """Every signer in the APK is pinned. An empty pin list never passes."""
    if not pins:
        raise CheckFailed("signer: no AllowedAPKSigningKeys pinned in the metadata")
    if not signers:
        raise CheckFailed("signer: apksigner reported no signer")
    unpinned = [s for s in signers if s not in pins]
    if unpinned:
        raise CheckFailed(f"signer: {', '.join(unpinned)} not pinned")


def check_version_code(version_code: int, published: int | None) -> None:
    """F-Droid clients only update upwards."""
    if published is not None and version_code <= published:
        raise CheckFailed(f"versionCode {version_code} is not higher than the published {published}")


def dex_has_pairip(dex: bytes) -> bool:
    return PAIRIP_MARKER in dex


def check_no_pairip(apk: Path) -> None:
    """Refuse Play automatic protection: its licence wall breaks every non-Play install.

    A launch on a test phone proves nothing (the wall can appear later), so the
    check reads the dex files themselves.
    """
    with zipfile.ZipFile(apk) as z:
        for name in z.namelist():
            if _DEX_RE.fullmatch(name) and dex_has_pairip(z.read(name)):
                raise CheckFailed(f"{name} contains com.pairip (Play automatic protection)")
