"""`fdroidkit publish`: check an APK, add it to the repo, update the index, deploy, read back.

Port of the publish script that has shipped OxiSoft's apps to fdroid.oxisoft.io since
2026-11, with every path made a parameter. Each step is a hard stop on failure:

  1. versionCode must be higher than the one already published (cheap, before download)
  2. fetch the APK (Play: the Google-signed universal APK; file: a path on disk)
  3. identity: package, versionCode, versionName (aapt2 dump badging)
  4. every signer pinned in AllowedAPKSigningKeys (apksigner; rotation aware,
     Source Stamp ignored)
  5. no Play automatic protection (com.pairip in any dex)
  --dry-run stops here and publishes nothing.
  6. copy in, bump CurrentVersion(Code), `fdroid update`, optional site hooks,
     `fdroid deploy`, optional rsync of the landing page
  7. read the versionCode back from the LIVE index-v2.json, HEAD entry.jar and the APK
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import checks
from .checks import CheckFailed


class PublishError(Exception):
    """Setup problem (missing tool, metadata, config) rather than a failed APK check."""


@dataclass
class Options:
    repo: Path
    package: str
    version_code: int
    version_name: str
    source: str = "play"
    apk: Path | None = None
    dry_run: bool = False
    fdroid: str | None = None
    build_tools: Path | None = None
    live_url: str | None = None
    after_update: list[str] = field(default_factory=list)
    rsync_site: str | None = None
    readback: bool = True
    play_sa_file: str | None = None
    readback_attempts: int = 6
    readback_delay: float = 5.0


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list, cwd: Path | None = None) -> str:
    log("+ " + " ".join(map(str, cmd)))
    r = subprocess.run(list(map(str, cmd)), cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise PublishError(f"{Path(str(cmd[0])).name} exited {r.returncode}: {(r.stderr or r.stdout).strip()[-2000:]}")
    return r.stdout


# ---- environment discovery -------------------------------------------------

def _version_key(p: Path) -> list:
    return [int(x) if x.isdigit() else -1 for x in re.split(r"[.-]", p.name)]


def find_build_tools(explicit: Path | None, repo: Path) -> Path:
    """Directory holding aapt2 + apksigner: --build-tools, $ANDROID_HOME, config sdk_path, ~/android."""
    if explicit:
        cands = [explicit]
    else:
        roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                 read_config(repo).get("sdk_path"), str(Path.home() / "android")]
        cands = []
        for r in filter(None, roots):
            bt = Path(r).expanduser() / "build-tools"
            if bt.is_dir():
                cands += sorted((d for d in bt.iterdir() if d.is_dir()), key=_version_key, reverse=True)
    for c in cands:
        if (c / "aapt2").exists() and (c / "apksigner").exists():
            return c
    aapt2 = shutil.which("aapt2")
    if not explicit and aapt2 and shutil.which("apksigner"):
        return Path(aapt2).parent
    raise PublishError("no Android build-tools with aapt2 + apksigner found (use --build-tools or ANDROID_HOME)")


def find_fdroid(explicit: str | None) -> str:
    for c in filter(None, [explicit, shutil.which("fdroid"), str(Path.home() / ".local/bin/fdroid")]):
        if Path(c).exists() or shutil.which(c):
            return c
    raise PublishError("fdroid (fdroidserver) not found (use --fdroid or pip install fdroidserver)")


_ENV_REF_RE = re.compile(r"^\s*([a-z_]+):\s*\{\s*env:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}", re.M)


def missing_env(repo: Path) -> list[str]:
    """`key: {env: NAME}` lines in config.yml whose NAME is unset. fdroidserver only
    logs those and then crashes deep in keytool/jarsigner with a TypeError."""
    cfg = repo / "config.yml"
    if not cfg.exists():
        return []
    return [f"{k} (${v})" for k, v in _ENV_REF_RE.findall(cfg.read_text()) if not os.environ.get(v)]


def read_config(repo: Path) -> dict:
    """The few scalar keys we need from config.yml, without a YAML dependency."""
    cfg = repo / "config.yml"
    if not cfg.exists():
        return {}
    out = {}
    for m in re.finditer(r"^(repo_url|sdk_path):\s*['\"]?([^'\"\s#]+)", cfg.read_text(), re.M):
        out[m.group(1)] = m.group(2)
    return out


# ---- metadata --------------------------------------------------------------

def metadata_path(repo: Path, package: str) -> Path:
    return repo / "metadata" / f"{package}.yml"


def published_code(meta: str, repo: Path, package: str) -> int | None:
    """Highest versionCode known: CurrentVersionCode, or an APK already in repo/ or archive/."""
    codes = []
    m = re.search(r"^CurrentVersionCode:\s*(\d+)", meta, re.M)
    if m:
        codes.append(int(m.group(1)))
    pat = re.compile(re.escape(package) + r"_(\d+)\.apk$")
    for d in (repo / "repo", repo / "archive"):
        if d.is_dir():
            codes += [int(mm.group(1)) for p in d.iterdir() if (mm := pat.match(p.name))]
    return max(codes) if codes else None


def bump_metadata(meta: str, version_code: int, version_name: str) -> str:
    for key, val in (("CurrentVersion", version_name), ("CurrentVersionCode", str(version_code))):
        line = f"{key}: {val}"
        if re.search(rf"^{key}:.*$", meta, re.M):
            meta = re.sub(rf"^{key}:.*$", line, meta, flags=re.M)
        else:
            meta = meta.rstrip("\n") + "\n" + line + "\n"
    return meta


# ---- the command -----------------------------------------------------------

def fetch(opts: Options, dest: Path) -> Path:
    if opts.source == "file":
        if not opts.apk or not Path(opts.apk).is_file():
            raise PublishError("--source file needs --apk <existing path>")
        shutil.copyfile(opts.apk, dest)
        return dest
    if opts.source == "play":
        from .sources import play
        sa = opts.play_sa_file or os.environ.get("PLAY_SA_FILE")
        if not sa:
            raise PublishError("--source play needs --play-sa-file or $PLAY_SA_FILE (service-account JSON)")
        return play.download(opts.package, opts.version_code, sa, dest)
    raise PublishError(f"source {opts.source!r} is not implemented yet")


def check_apk(apk: Path, opts: Options, bt: Path, pins: list[str], runner: Callable = run) -> None:
    badging = runner([bt / "aapt2", "dump", "badging", apk]).splitlines()
    if not badging:
        raise CheckFailed("identity: aapt2 printed nothing")
    log(badging[0])
    checks.check_identity(badging[0], opts.package, opts.version_code, opts.version_name)
    signers = checks.parse_signers(runner([bt / "apksigner", "verify", "--print-certs", apk]))
    checks.check_signers(signers, pins)
    log(f"signer pinned OK ({len(signers)} signer{'s' if len(signers) > 1 else ''})")
    checks.check_no_pairip(apk)
    log("no pairip licence check")


def readback(opts: Options, live_url: str, sleep: Callable = time.sleep) -> list[int]:
    base = live_url.rstrip("/")
    last = None
    for attempt in range(opts.readback_attempts):
        if attempt:
            sleep(opts.readback_delay)
        try:
            req = urllib.request.Request(f"{base}/index-v2.json", headers={"Cache-Control": "no-cache"})
            idx = json.load(urllib.request.urlopen(req, timeout=30))
            pkg = idx.get("packages", {}).get(opts.package, {})
            live = sorted(v["manifest"]["versionCode"] for v in pkg.get("versions", {}).values())
            if opts.version_code in live:
                for path in ("entry.jar", f"{opts.package}_{opts.version_code}.apk"):
                    st = urllib.request.urlopen(urllib.request.Request(f"{base}/{path}", method="HEAD"),
                                                timeout=30).status
                    if st != 200:
                        raise PublishError(f"{path} answers {st}")
                log("entry.jar and APK served (200)")
                return live
            last = f"live index has {live}"
        except PublishError:
            raise
        except Exception as e:  # noqa: BLE001  (network, CDN lag, partial deploy)
            last = str(e)
        log(f"read-back attempt {attempt + 1}: {last}")
    raise PublishError(f"read-back failed: {last}")


def publish(opts: Options, runner: Callable = run, sleep: Callable = time.sleep) -> int:
    # CI often runs under umask 077 for key files. Everything written here is served
    # publicly and rsync carries modes: a 0600 entry.jar is a 403 for every client.
    os.umask(0o022)
    repo = opts.repo.resolve()
    meta_file = metadata_path(repo, opts.package)
    if not meta_file.exists():
        raise PublishError(f"no metadata {meta_file} (run `fdroidkit add-app {opts.package}` first)")
    meta = meta_file.read_text()
    pins = checks.parse_pins(meta)
    if not pins:
        raise CheckFailed(f"signer: no AllowedAPKSigningKeys pinned in {meta_file}")
    checks.check_version_code(opts.version_code, published_code(meta, repo, opts.package))
    bt = find_build_tools(opts.build_tools, repo)
    live_url = opts.live_url or read_config(repo).get("repo_url") or ""
    fdroid = ""
    if not opts.dry_run:
        fdroid = find_fdroid(opts.fdroid)
        unset = missing_env(repo)
        if unset:
            raise PublishError("config.yml reads from unset environment variables: " + ", ".join(unset))
        if opts.readback and not live_url:
            raise PublishError("no live URL: set repo_url in config.yml, pass --live-url, or --no-readback")

    with tempfile.TemporaryDirectory(prefix="fdroidkit-") as td:
        apk = fetch(opts, Path(td) / f"{opts.package}_{opts.version_code}.apk")
        log(f"apk {apk.stat().st_size} bytes from {opts.source}")
        check_apk(apk, opts, bt, pins, runner)
        if opts.dry_run:
            log("dry run: all checks passed, nothing published")
            return 0
        dest = repo / "repo" / apk.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(apk, dest)
    meta_file.write_text(bump_metadata(meta, opts.version_code, opts.version_name))
    runner([fdroid, "update"], cwd=repo)
    for cmd in opts.after_update:
        runner(["sh", "-c", cmd], cwd=repo)
    runner([fdroid, "deploy"], cwd=repo)
    if opts.rsync_site:
        runner(["rsync", "-a", "--exclude", "fdroid", f"{repo}/site/", opts.rsync_site])
    if opts.readback:
        live = readback(opts, live_url, sleep)
        log(f"F-Droid: {opts.package} {opts.version_name} ({opts.version_code}) live; versions served {live}")
    else:
        log(f"published {opts.package} {opts.version_name} ({opts.version_code}); read-back skipped")
    return 0
