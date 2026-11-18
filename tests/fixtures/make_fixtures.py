"""Write tiny APK-shaped fixtures (zip + fixture.json) for the publish tests."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

PLAY = "a" * 64        # Play App Signing key
PLAY_NEW = "b" * 64    # key after a rotation
STAMP = "c" * 64       # Source Stamp signer, never a pin
STRANGER = "d" * 64
GOOGLE_DN = "CN=Android, OU=Android, O=Google Inc., L=Mountain View, ST=California, C=US"

KINDS = {
    "one_key": {"signers": f"Signer #1 certificate DN: CN=Android\n"
                           f"Signer #1 certificate SHA-256 digest: {PLAY}\n"
                           f"Source Stamp Signer certificate SHA-256 digest: {STAMP}\n",
                "dex": {"classes.dex": b"dex\nLcom/example/Main;"}},
    "rotated": {"signers": f"Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: {PLAY}\n"
                           f"Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: {PLAY_NEW}\n",
                "dex": {"classes.dex": b"dex\nLcom/example/Main;"}},
    "pairip": {"signers": f"Signer #1 certificate SHA-256 digest: {PLAY}\n",
               "dex": {"classes.dex": b"dex", "classes2.dex": b"..Lcom/pairip/licensecheck/LicenseClient;"}},
    "stranger": {"signers": f"Signer #1 certificate SHA-256 digest: {STRANGER}\n",
                 "dex": {"classes.dex": b"dex"}},
    # add-app --discover-signer: real apksigner DN lines
    "play": {"signers": f"Signer #1 certificate DN: {GOOGLE_DN}\n"
                        f"Signer #1 certificate SHA-256 digest: {PLAY}\n"
                        f"Signer #1 certificate SHA-1 digest: {'e' * 40}\n"
                        f"Source Stamp Signer certificate DN: CN=SourceStamp, O=Google Inc.\n"
                        f"Source Stamp Signer certificate SHA-256 digest: {STAMP}\n",
             "dex": {"classes.dex": b"dex\nLcom/example/Main;"}},
    "play_rotated": {"signers": f"Signer (minSdkVersion=24, maxSdkVersion=32) certificate DN: {GOOGLE_DN}\n"
                                f"Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: {PLAY}\n"
                                f"Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate DN: {GOOGLE_DN}\n"
                                f"Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: {PLAY_NEW}\n",
                     "dex": {"classes.dex": b"dex"}},
    "upload_key": {"signers": f"Signer #1 certificate DN: CN=Jane Dev, O=Example Ltd, C=PL\n"
                              f"Signer #1 certificate SHA-256 digest: {STRANGER}\n",
                   "dex": {"classes.dex": b"dex"}},
    "play_pairip": {"signers": f"Signer #1 certificate DN: {GOOGLE_DN}\n"
                               f"Signer #1 certificate SHA-256 digest: {PLAY}\n",
                    "dex": {"classes.dex": b"..Lcom/pairip/licensecheck/LicenseClient;"}},
}


def make_apk(path: Path, kind: str, package: str, code: int, name: str) -> Path:
    k = KINDS[kind]
    info = {
        "badging": f"package: name='{package}' versionCode='{code}' versionName='{name}' "
                   f"platformBuildVersionName='15'\nsdkVersion:'24'\n",
        "signers": k["signers"],
        "package": package, "versionCode": code, "versionName": name,
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", b"\0")
        z.writestr("fixture.json", json.dumps(info))
        for n, data in k["dex"].items():
            z.writestr(n, data)
    return path


def metadata(*pins: str, code: int | None = 12, name: str = "1.2.0") -> str:
    out = "Categories:\n  - Games\nAllowedAPKSigningKeys:\n" + "".join(f"  - {p}\n" for p in pins)
    if code is not None:
        out += f"CurrentVersion: {name}\nCurrentVersionCode: {code}\n"
    return out
