"""Download the Play-generated signed universal APK for a versionCode.

Play App Signing re-signs the app with Google's key, so this APK and a Play install
update each other. Play generates the APKs a little after the upload: retry.
Needs the `play` extra (google-api-python-client, google-auth).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

SCOPE = "https://www.googleapis.com/auth/androidpublisher"


def client(sa_file: str):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as e:  # pragma: no cover - depends on the extra
        raise RuntimeError("--source play needs: pip install 'fdroidkit[play]'") from e
    creds = service_account.Credentials.from_service_account_file(sa_file, scopes=[SCOPE])
    return build("androidpublisher", "v3", credentials=creds, cache_discovery=False)


def find_universal(api, package: str, version_code: int, attempts: int = 10,
                   delay: float = 30, sleep: Callable = time.sleep) -> dict:
    """The generatedUniversalApk entry; retried while Play is still generating."""
    last = None
    for attempt in range(attempts):
        if attempt:
            sleep(delay)
        try:
            apks = api.generatedapks().list(packageName=package, versionCode=version_code).execute()
            gen = next((g for g in apks.get("generatedApks", []) if g.get("generatedUniversalApk")), None)
            if gen:
                return gen["generatedUniversalApk"]
            last = "no universal APK yet"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        print(f"generatedapks attempt {attempt + 1}: {last}", flush=True)
    raise RuntimeError(f"Play has no universal APK for {package} {version_code}: {last}")


def download(package: str, version_code: int, sa_file: str, dest: Path, api=None,
             sleep: Callable = time.sleep) -> Path:
    api = api or client(sa_file)
    uni = find_universal(api, package, version_code, sleep=sleep)
    req = api.generatedapks().download_media(packageName=package, versionCode=version_code,
                                             downloadId=uni["downloadId"])
    from googleapiclient.http import MediaIoBaseDownload
    with open(dest, "wb") as f:
        dl, done = MediaIoBaseDownload(f, req, chunksize=8 * 1024 * 1024), False
        while not done:
            _, done = dl.next_chunk()
    return dest
