"""Fetch an APK from a GitHub release (self-signed apps, IzzyOnDroid style).

The developer signs the APK with their own key and attaches it to a release. The kit
takes the latest release (or a given tag), picks the one asset matching a glob
(default `*.apk`) and downloads it. The signer pin in the metadata is what proves the
APK is theirs: a release asset alone proves nothing about who built it.

Public repos need no token. `$GITHUB_TOKEN` (or the env var named by `token: {env: X}`
in fdroidkit.json) is sent when set: it lifts the 60 requests/hour anonymous limit and
reads private repos. `$FDROIDKIT_GITHUB_API` overrides the API base (tests, GHE).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(RuntimeError):
    """The release or asset could not be found or downloaded."""


@dataclass
class Asset:
    name: str
    url: str          # browser_download_url
    api_url: str      # API asset url (needs Accept: application/octet-stream)
    size: int
    tag: str


def api_base() -> str:
    return os.environ.get("FDROIDKIT_GITHUB_API", API).rstrip("/")


def _headers(token: str | None, accept: str = "application/vnd.github+json") -> dict:
    h = {"Accept": accept, "User-Agent": "fdroidkit", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def check_repo(repo: str) -> str:
    repo = repo.strip().removeprefix("https://github.com/").rstrip("/")
    if not _REPO_RE.match(repo):
        raise GitHubError(f"{repo!r} is not a GitHub repo like owner/name")
    return repo


def release(repo: str, tag: str | None = None, token: str | None = None) -> dict:
    """Release JSON: /releases/latest (newest non-draft, non-prerelease) or /releases/tags/<tag>."""
    repo = check_repo(repo)
    path = f"releases/tags/{urllib.parse.quote(tag, safe='')}" if tag else "releases/latest"
    url = f"{api_base()}/repos/{repo}/{path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_headers(token)), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            what = f"tag {tag}" if tag else "published (non-prerelease) release"
            raise GitHubError(f"{repo} has no {what} (or the repo is private: set GITHUB_TOKEN)") from e
        if e.code in (403, 429):
            raise GitHubError(f"GitHub API {e.code} for {url}: rate limited? set GITHUB_TOKEN") from e
        raise GitHubError(f"GitHub API {e.code} for {url}") from e


def pick_asset(rel: dict, pattern: str = "*.apk") -> Asset:
    """Exactly one asset whose name matches the glob; zero or several is an error."""
    assets = rel.get("assets") or []
    hits = [a for a in assets if fnmatch.fnmatchcase(a.get("name", ""), pattern)]
    tag = rel.get("tag_name", "?")
    if len(hits) != 1:
        names = ", ".join(a.get("name", "?") for a in assets) or "none"
        n = "no asset" if not hits else f"{len(hits)} assets"
        raise GitHubError(f"release {tag}: {n} matching {pattern!r} (assets: {names}); "
                          "pass --asset GLOB to pick one")
    a = hits[0]
    return Asset(name=a["name"], url=a.get("browser_download_url", ""), api_url=a.get("url", ""),
                 size=int(a.get("size") or 0), tag=tag)


def download(asset: Asset, dest: Path, token: str | None = None) -> Path:
    """Public assets via browser_download_url; with a token via the API asset URL (private repos)."""
    if token and asset.api_url:
        req = urllib.request.Request(asset.api_url, headers=_headers(token, "application/octet-stream"))
    else:
        req = urllib.request.Request(asset.url, headers={"User-Agent": "fdroidkit"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        raise GitHubError(f"download of {asset.name} from {asset.tag} failed: HTTP {e.code}") from e
    got = dest.stat().st_size
    if asset.size and got != asset.size:
        raise GitHubError(f"{asset.name}: got {got} bytes, release says {asset.size}")
    return dest


def fetch(repo: str, dest: Path, pattern: str = "*.apk", tag: str | None = None,
          token: str | None = None, log=print) -> Asset:
    rel = release(repo, tag, token)
    asset = pick_asset(rel, pattern)
    log(f"github release {check_repo(repo)} {asset.tag}: {asset.name} ({asset.size} bytes)")
    download(asset, dest, token)
    return asset


def token_from(cfg: dict | None) -> str | None:
    """`token: {env: NAME}` in the source config, else $GITHUB_TOKEN. Never a literal token."""
    env = "GITHUB_TOKEN"
    t = (cfg or {}).get("token")
    if isinstance(t, dict) and t.get("env"):
        env = t["env"]
    elif t is not None:
        raise GitHubError("source.token must be {\"env\": \"NAME\"}, never the token itself")
    return os.environ.get(env) or None


def source_config(repo_dir: Path, package: str) -> dict:
    """`apps.<package>.source` from <repo>/fdroidkit.json, or {} when absent."""
    p = repo_dir / "fdroidkit.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise GitHubError(f"{p}: {e}") from e
    src = ((data.get("apps") or {}).get(package) or {}).get("source") or {}
    if src and src.get("type", "github-release") != "github-release":
        return {}
    return src


def save_source_config(repo_dir: Path, package: str, src: dict) -> Path:
    """Write apps.<package>.source into fdroidkit.json, other keys untouched."""
    p = repo_dir / "fdroidkit.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    data.setdefault("apps", {}).setdefault(package, {})["source"] = src
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p
