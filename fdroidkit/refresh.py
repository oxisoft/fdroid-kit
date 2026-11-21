"""`fdroidkit refresh` and `fdroidkit restore`: rebuild and redeploy the index without a new APK.

`publish` is the only path that runs `fdroid update`, and it refuses a versionCode that is
already published. So a metadata-only change (a new translation, an edited description, new
screenshots, a renamed app) would stay invisible until some app ships a new release. `refresh`
closes that gap:

  1. optional restore: download every APK the LIVE index lists that is missing locally
     (hosts like GitHub Pages keep no APKs in git)
  2. guard: every app whose metadata says CurrentVersionCode > 0 must still have an APK in
     repo/ or archive/, otherwise `fdroid update` would drop it from the index and the
     deploy would remove it for every user
  --dry-run stops here.
  3. `fdroid update`, optional site hooks, `fdroid deploy`, optional rsync of the landing page
  4. read back: the live index-v2.json must carry the new local index timestamp, entry.jar 200

No APK is added and no version moves.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .publish import PublishError, find_fdroid, log, missing_env, read_config, run


@dataclass
class RefreshOptions:
    repo: Path
    fdroid: str | None = None
    live_url: str | None = None
    after_update: list[str] = field(default_factory=list)
    rsync_site: str | None = None
    readback: bool = True
    deploy: bool = True
    restore: bool = False
    dry_run: bool = False
    readback_attempts: int = 6
    readback_delay: float = 5.0


def _live(repo: Path, explicit: str | None) -> str:
    return (explicit or read_config(repo).get("repo_url") or "").rstrip("/")


def restore(repo: Path, live_url: str, opener: Callable = urllib.request.urlopen) -> list[str]:
    """Download into repo/ every APK the live index lists that is not there yet.

    A live index that does not exist yet (404) is a first publish: nothing to restore."""
    if not live_url:
        raise PublishError("restore: no live URL (--live-url or repo_url in config.yml)")
    base = live_url.rstrip("/")
    try:
        with opener(f"{base}/index-v2.json", timeout=60) as r:
            index = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log(f"restore: {base}/index-v2.json not found, first publish")
            return []
        raise PublishError(f"restore: {base}/index-v2.json answers {e.code}") from e
    except urllib.error.URLError as e:
        raise PublishError(f"restore: {base}/index-v2.json: {e.reason}") from e
    dest_dir = repo / "repo"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fetched = []
    for pkg in index.get("packages", {}).values():
        for v in pkg.get("versions", {}).values():
            name = v.get("file", {}).get("name", "").lstrip("/")
            if not name or "/" in name or ".." in name:
                continue
            dest = dest_dir / name
            if dest.exists():
                continue
            log(f"restore: {name}")
            tmp = dest.with_name(dest.name + ".part")
            with opener(f"{base}/{name}", timeout=300) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.rename(dest)
            fetched.append(name)
    log(f"restore: {len(fetched)} APK(s) fetched from {base}")
    return fetched


_CODE_RE = re.compile(r"^CurrentVersionCode:\s*(\d+)", re.M)


def unbacked_apps(repo: Path) -> list[str]:
    """Apps whose metadata says they are published but that have no APK in repo/ or archive/.

    `fdroid update` would leave them out of the new index, and the deploy would delete
    them from the live repo."""
    present: set[str] = set()
    for d in (repo / "repo", repo / "archive"):
        if d.is_dir():
            for p in d.glob("*.apk"):
                m = re.match(r"(.+)_\d+\.apk$", p.name)
                if m:
                    present.add(m.group(1))
    out = []
    meta_dir = repo / "metadata"
    for meta in sorted(meta_dir.glob("*.yml")) if meta_dir.is_dir() else []:
        m = _CODE_RE.search(meta.read_text())
        if m and int(m.group(1)) > 0 and meta.stem not in present:
            out.append(f"{meta.stem} ({m.group(1)})")
    return out


def local_timestamp(repo: Path) -> int | None:
    try:
        idx = json.loads((repo / "repo" / "index-v2.json").read_text())
    except (OSError, ValueError):
        return None
    ts = idx.get("repo", {}).get("timestamp")
    return int(ts) if isinstance(ts, (int, float)) else None


def readback(opts: RefreshOptions, live_url: str, want_ts: int | None,
             sleep: Callable = time.sleep) -> int | None:
    """Wait until the live index carries the timestamp `fdroid update` just wrote."""
    last = None
    for attempt in range(opts.readback_attempts):
        if attempt:
            sleep(opts.readback_delay)
        try:
            req = urllib.request.Request(f"{live_url}/index-v2.json", headers={"Cache-Control": "no-cache"})
            idx = json.load(urllib.request.urlopen(req, timeout=30))
            ts = idx.get("repo", {}).get("timestamp")
            if want_ts is None or (isinstance(ts, (int, float)) and ts >= want_ts):
                st = urllib.request.urlopen(urllib.request.Request(f"{live_url}/entry.jar", method="HEAD"),
                                            timeout=30).status
                if st != 200:
                    raise PublishError(f"entry.jar answers {st}")
                log("live index is the new one; entry.jar served (200)")
                return int(ts) if isinstance(ts, (int, float)) else None
            last = f"live index timestamp {ts}, want {want_ts}"
        except PublishError:
            raise
        except Exception as e:  # noqa: BLE001  (network, CDN lag, partial deploy)
            last = str(e)
        log(f"read-back attempt {attempt + 1}: {last}")
    raise PublishError(f"read-back failed: {last}")


def refresh(opts: RefreshOptions, runner: Callable = run, sleep: Callable = time.sleep) -> int:
    os.umask(0o022)  # served publicly, see publish()
    repo = opts.repo.resolve()
    if not (repo / "metadata").is_dir():
        raise PublishError(f"no metadata/ in {repo} (is --repo the F-Droid working tree?)")
    live_url = _live(repo, opts.live_url)
    fdroid = ""
    if not opts.dry_run:
        fdroid = find_fdroid(opts.fdroid)
        unset = missing_env(repo)
        if unset:
            raise PublishError("config.yml reads from unset environment variables: " + ", ".join(unset))
        if opts.readback and not live_url:
            raise PublishError("no live URL: set repo_url in config.yml, pass --live-url, or --no-readback")
    if opts.restore:
        restore(repo, live_url)
    missing = unbacked_apps(repo)
    if missing:
        raise PublishError("refusing to refresh: no APK in repo/ or archive/ for " + ", ".join(missing)
                           + "; `fdroid update` would drop them from the live index (use --restore, "
                             "or copy the APKs in first)")
    if opts.dry_run:
        log("dry run: every published app has its APK, nothing rebuilt or deployed")
        return 0
    runner([fdroid, "update"], cwd=repo)
    want_ts = local_timestamp(repo)
    for cmd in opts.after_update:
        runner(["sh", "-c", cmd], cwd=repo)
    if opts.deploy:
        runner([fdroid, "deploy"], cwd=repo)
    else:
        log("deploy skipped (--no-deploy): the caller ships repo/ itself, e.g. as a Pages artifact")
    if opts.rsync_site:
        runner(["rsync", "-a", "--exclude", "fdroid", f"{repo}/site/", opts.rsync_site])
    if opts.readback:
        readback(opts, live_url, want_ts, sleep)
        log(f"F-Droid: index refreshed and live (timestamp {want_ts})")
    else:
        log(f"index refreshed (timestamp {want_ts}); read-back skipped")
    return 0
