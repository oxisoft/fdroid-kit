import io
import zipfile

import pytest

from fdroidkit import checks
from fdroidkit.checks import CheckFailed

A = "a" * 64
B = "b" * 64
C = "c" * 64

APKSIGNER_ONE = f"""Signer #1 certificate DN: CN=Android, OU=Android, O=Google Inc., L=Mountain View, ST=California, C=US
Signer #1 certificate SHA-256 digest: {A}
Signer #1 certificate SHA-1 digest: 0000000000000000000000000000000000000000
Source Stamp Signer certificate SHA-256 digest: {C}
"""

APKSIGNER_ROTATED = f"""Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: {A}
Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: {B}
"""

METADATA = f"""Categories:
  - Games
AllowedAPKSigningKeys:
  - {A}
  - {B}
CurrentVersion: 1.2.0
CurrentVersionCode: 12
"""


def test_parse_signers_ignores_source_stamp():
    assert checks.parse_signers(APKSIGNER_ONE) == [A]


def test_parse_signers_rotated_key():
    assert checks.parse_signers(APKSIGNER_ROTATED) == [A, B]


def test_parse_pins():
    assert checks.parse_pins(METADATA) == [A, B]


def test_signers_pinned():
    checks.check_signers([A, B], [A, B])


def test_signer_not_pinned():
    with pytest.raises(CheckFailed, match="not pinned"):
        checks.check_signers([A, C], [A, B])


def test_no_pins_never_passes():
    with pytest.raises(CheckFailed, match="no AllowedAPKSigningKeys"):
        checks.check_signers([A], [])


def test_no_signer_never_passes():
    with pytest.raises(CheckFailed):
        checks.check_signers([], [A])


BADGING = "package: name='com.example.app' versionCode='13' versionName='1.3.0' platformBuildVersionName='15'"


def test_identity_ok():
    checks.check_identity(BADGING, "com.example.app", 13, "1.3.0")


@pytest.mark.parametrize("pkg,code,name", [
    ("com.example.other", 13, "1.3.0"),
    ("com.example.app", 14, "1.3.0"),
    ("com.example.app", 13, "1.3.1"),
])
def test_identity_mismatch(pkg, code, name):
    with pytest.raises(CheckFailed, match="identity"):
        checks.check_identity(BADGING, pkg, code, name)


def test_identity_no_prefix_match():
    # versionCode='1' must not match versionCode='13'
    with pytest.raises(CheckFailed):
        checks.check_identity(BADGING, "com.example.app", 1, "1.3.0")


def test_version_code():
    checks.check_version_code(13, 12)
    checks.check_version_code(1, None)
    with pytest.raises(CheckFailed):
        checks.check_version_code(12, 12)


def _apk(tmp_path, dexes):
    p = tmp_path / "t.apk"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", b"\0")
        for name, data in dexes.items():
            z.writestr(name, data)
    p.write_bytes(buf.getvalue())
    return p


def test_pairip_clean(tmp_path):
    checks.check_no_pairip(_apk(tmp_path, {"classes.dex": b"dex\nLcom/example/Main;"}))


def test_pairip_in_secondary_dex(tmp_path):
    apk = _apk(tmp_path, {"classes.dex": b"dex", "classes3.dex": b"..Lcom/pairip/licensecheck/LicenseClient;"})
    with pytest.raises(CheckFailed, match="classes3.dex"):
        checks.check_no_pairip(apk)


def test_pairip_outside_dex_ignored(tmp_path):
    # Only dex code counts; an asset that mentions the string is not a licence wall.
    checks.check_no_pairip(_apk(tmp_path, {"classes.dex": b"dex", "assets/notes.txt": b"Lcom/pairip/"}))
