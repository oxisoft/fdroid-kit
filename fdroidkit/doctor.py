"""`fdroidkit doctor`: audit a LIVE F-Droid repo the way a client and a careful user see it.

Every check reads what is actually served, never the working tree alone:

- tls        the HTTPS certificate of the repo host is valid for more than 14 days
- index      entry.jar is signed (jarsigner, when installed), the entry.json inside the jar
             vouches for the served index-v2.json (SHA-256), index-v1.jar has the same signer
- freshness  age of the signed index (a warning only: a quiet repo is not a broken one)
- landing    every fingerprint on the landing page equals the entry.jar signer, the add-repo
             link points at this repo with that fingerprint, qr.svg encodes exactly that link
- signer     each app's signers (from the signed index) are pinned in the local metadata
             (`--repo`), and stay the same across versions
- pairip     the newest APK of each app carries no Play automatic protection; only the dex
             entries are read, with HTTP Range requests (no full download when the host
             supports ranges)
- version    the live versionCode is not behind the local metadata or the local index
- storage    bytes of every APK the signed indexes of repo/ and archive/ list, against a host
             budget (`--budget github-pages` = the 1 GB Pages site limit, or a byte count):
             warning from 80%, failure from 100%; sizes come from the indexes, no download

Exit status: 0 nothing failed (warnings allowed), 1 at least one check failed, 3 the repo could
not be read at all.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import io
import json
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import checks
from .site import keys
from .site.render import qr_svg, site_root
from .site.verify import FP_RE, _qr_modules

TLS_MIN_DAYS = 14
STALE_DAYS = 90
UA = "fdroidkit-doctor"
BUDGET_WARN = 0.8
# Named host budgets: (bytes, where the number comes from). 1 GB is read as 10^9 bytes, the
# stricter reading of GitHub's "may be no larger than 1 GB".
BUDGETS = {
    "github-pages": (1_000_000_000, "GitHub Pages: published sites may be no larger than 1 GB, "
                     "https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits"),
}
_UNITS = {"": 1, "k": 10**3, "m": 10**6, "g": 10**9, "t": 10**12,
          "ki": 2**10, "mi": 2**20, "gi": 2**30, "ti": 2**40}


class DoctorError(RuntimeError):
    """The repo could not be read at all (no entry.jar, bad URL)."""


@dataclass
class Check:
    name: str
    subject: str
    status: str  # ok | warn | fail | skip
    detail: str


@dataclass
class Report:
    url: str
    site: str
    checks: list[Check] = field(default_factory=list)
    storage: dict | None = None

    def add(self, name: str, subject: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, subject, status, detail))

    @property
    def ok(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    def counts(self) -> dict:
        out = {s: 0 for s in ("ok", "warn", "fail", "skip")}
        for c in self.checks:
            out[c.status] += 1
        return out

    def lines(self) -> list[str]:
        w1 = max([len(c.name) for c in self.checks] + [5])
        w2 = max([len(c.subject) for c in self.checks] + [7])
        out = [f"repo: {self.url}", f"site: {self.site}", "",
               f"{'CHECK':<{w1}}  {'SUBJECT':<{w2}}  {'RESULT':<6}  DETAIL"]
        mark = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "skip": "skip"}
        out += [f"{c.name:<{w1}}  {c.subject:<{w2}}  {mark[c.status]:<6}  {c.detail}" for c in self.checks]
        n = self.counts()
        out += ["", f"verdict: {'healthy' if self.ok else 'UNHEALTHY'} "
                    f"({n['ok']} ok, {n['warn']} warn, {n['fail']} fail, {n['skip']} skipped)"]
        return out

    def to_json(self) -> str:
        out = {"url": self.url, "site": self.site, "ok": self.ok, "counts": self.counts(),
               "checks": [asdict(c) for c in self.checks]}
        if self.storage is not None:
            out["storage"] = self.storage
        return json.dumps(out, indent=2)


# ---- network (injectable for tests) ------------------------------------------

class Net:
    """Everything doctor reads from the network. Tests replace it with a fake."""

    def get(self, url: str) -> bytes | None:
        """Body of url, None on 404."""
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (repo URL)
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def open_apk(self, url: str):
        """A seekable file object for a remote APK: Range reads, or a full download."""
        return HttpRangeFile.open(url)

    def tls_days(self, host: str, port: int = 443) -> float:
        """Days until the server certificate expires (raises on an invalid certificate)."""
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=20) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert = s.getpeercert() or {}
        return (ssl.cert_time_to_seconds(str(cert["notAfter"])) - time.time()) / 86400


class HttpRangeFile(io.RawIOBase):
    """Read-only seekable view of a URL through HTTP Range requests, so zipfile reads only
    the central directory and the members asked for."""

    def __init__(self, url: str, size: int):
        self.url, self.size, self.pos, self.fetched = url, size, 0, 0

    @classmethod
    def open(cls, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            cr = r.headers.get("Content-Range", "")
            m = re.match(r"bytes 0-0/(\d+)", cr) if r.status == 206 else None
            if m:
                return cls(url, int(m.group(1)))
        tmp = tempfile.TemporaryFile()  # no range support: download once
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:  # noqa: S310
            shutil.copyfileobj(r, tmp)
        tmp.seek(0)
        return tmp

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def read(self, n=-1):
        if self.pos >= self.size:
            return b""
        end = self.size - 1 if n is None or n < 0 else min(self.size - 1, self.pos + n - 1)
        req = urllib.request.Request(self.url, headers={"User-Agent": UA, "Range": f"bytes={self.pos}-{end}"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            data = r.read()
        self.pos += len(data)
        self.fetched += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


def _run(cmd: list) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise keys.KeyReadError(f"{cmd[0]} exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


# ---- the checks -----------------------------------------------------------------

def _host(url: str) -> tuple[str, int, str]:
    u = urllib.parse.urlparse(url)
    return u.hostname or "", u.port or 443, u.scheme


def check_tls(rep: Report, net: Net, url: str) -> None:
    host, port, scheme = _host(url)
    if scheme != "https":
        rep.add("tls", host, "fail", f"{scheme}:// repo: F-Droid clients need https")
        return
    try:
        days = net.tls_days(host, port)
    except (OSError, ssl.SSLError) as e:
        rep.add("tls", host, "fail", f"certificate not accepted: {e}")
        return
    st = "ok" if days > TLS_MIN_DAYS else "fail"
    rep.add("tls", host, st, f"certificate valid {days:.0f} more day(s) (minimum {TLS_MIN_DAYS})")


def _jar_entry_json(jar: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(jar)) as z:
        return json.loads(z.read("entry.json"))


def check_index(rep: Report, net: Net, url: str, runner: Callable) -> tuple[str, dict]:
    """Returns (index key fingerprint, index-v2 data). Raises DoctorError without entry.jar."""
    base = url.rstrip("/")
    jar = net.get(base + "/entry.jar")
    if jar is None:
        raise DoctorError(f"{base}/entry.jar not found: not an F-Droid repo URL (it ends in /repo)")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "entry.jar"
        p.write_bytes(jar)
        key = keys.jar_fingerprint(p, runner=runner)
        if shutil.which("jarsigner") or runner is not _run:
            try:
                out = runner(["jarsigner", "-verify", str(p)])
                good = "jar verified" in out
                rep.add("index", "entry.jar", "ok" if good else "fail",
                        f"signed by {key[:16]}..., jarsigner: {'verified' if good else out.strip()[-120:]}")
            except keys.KeyReadError as e:
                rep.add("index", "entry.jar", "fail", f"signature does not verify: {e}")
        else:
            rep.add("index", "entry.jar", "warn", f"signer {key[:16]}..., not verified (no jarsigner, install a JDK)")
    try:
        entry = _jar_entry_json(jar)
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        raise DoctorError(f"entry.jar holds no readable entry.json: {e}") from e
    iname = entry.get("index", {}).get("name", "/index-v2.json")
    raw = net.get(base + iname)
    want = entry.get("index", {}).get("sha256", "")
    data: dict = {}
    if raw is None:
        rep.add("index", iname.lstrip("/"), "fail", "missing, but entry.json lists it")
    elif hashlib.sha256(raw).hexdigest() != want:
        rep.add("index", iname.lstrip("/"), "fail",
                f"sha256 {hashlib.sha256(raw).hexdigest()[:16]}... is not the signed {want[:16]}... "
                "(stale CDN or a half-finished deploy)")
    else:
        data = json.loads(raw)
        rep.add("index", iname.lstrip("/"), "ok",
                f"matches signed entry.json, {len(data.get('packages', {}))} package(s)")
    v1 = net.get(base + "/index-v1.jar")
    if v1 is None:
        rep.add("index", "index-v1.jar", "warn", "missing: F-Droid clients older than 1.17 cannot read the repo")
    else:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "index-v1.jar"
            p.write_bytes(v1)
            try:
                k1 = keys.jar_fingerprint(p, runner=runner)
                rep.add("index", "index-v1.jar", "ok" if k1 == key else "fail",
                        "same signer as entry.jar" if k1 == key else f"signer {k1[:16]}... differs from entry.jar")
            except keys.KeyReadError as e:
                rep.add("index", "index-v1.jar", "fail", str(e))
    ts = entry.get("timestamp")
    if ts:
        age = (time.time() - ts / 1000) / 86400
        when = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rep.add("freshness", "index", "ok" if age <= STALE_DAYS else "warn",
                f"signed {when}, {age:.1f} day(s) ago" + ("" if age <= STALE_DAYS else f" (over {STALE_DAYS})"))
    return key, data


def check_landing(rep: Report, net: Net, url: str, site: str, key: str) -> None:
    page_b = net.get(site.rstrip("/") + "/")
    if page_b is None:
        rep.add("landing", "index.html", "fail", f"{site}/ not found")
        return
    page = page_b.decode("utf-8", "replace")
    hits = {re.sub(r"[^0-9A-Fa-f]", "", h).upper() for h in FP_RE.findall(page.replace("<br>", " "))}
    if not hits:
        rep.add("landing", "fingerprint", "fail", "no fingerprint on the landing page")
    else:
        bad = sorted(h for h in hits if h != key)
        rep.add("landing", "fingerprint", "fail" if bad else "ok",
                f"{len(bad)} of {len(hits)} differ from the index key: {', '.join(b[:16] + '...' for b in bad)}"
                if bad else f"{len(hits)} printed, all equal the index key")
    m = re.search(r'class="qr" href="([^"]+)"', page) or re.search(r'href="(fdroidrepos?://[^"]+)"', page)
    if not m:
        rep.add("landing", "add-repo link", "warn", "no add-repo link found on the page")
        return
    add = html.unescape(m.group(1))
    q = urllib.parse.parse_qs(urllib.parse.urlparse(add).query)
    addr = re.sub(r"^fdroidrepos://", "https://", re.sub(r"^fdroidrepo://", "http://", add.split("?")[0]))
    addr_ok = addr.rstrip("/") == url.rstrip("/")
    fp_ok = (q.get("fingerprint") or [""])[0].upper() == key
    rep.add("landing", "add-repo link", "ok" if addr_ok and fp_ok else "fail",
            "points at this repo with the index fingerprint" if addr_ok and fp_ok else
            f"{add} ({'wrong address' if not addr_ok else ''}{' and ' if not addr_ok and not fp_ok else ''}"
            f"{'wrong fingerprint' if not fp_ok else ''})")
    if "qr.svg" not in page:
        rep.add("landing", "qr.svg", "skip", "the page shows no QR")
        return
    svg = net.get(site.rstrip("/") + "/qr.svg")
    fresh = qr_svg(add)
    if svg is None:
        rep.add("landing", "qr.svg", "fail", "the page links qr.svg, but it is missing")
    elif fresh is None:
        rep.add("landing", "qr.svg", "skip", "cannot decode: pip install 'fdroidkit[site]' (qrcode)")
    else:
        same = _qr_modules(fresh) == _qr_modules(svg.decode("utf-8", "replace"))
        rep.add("landing", "qr.svg", "ok" if same else "fail",
                "encodes the add-repo link" if same else "does NOT encode the add-repo link on the page")


def _versions(pkg: dict) -> list[dict]:
    return sorted(pkg.get("versions", {}).values(), key=lambda v: v["manifest"]["versionCode"], reverse=True)


def check_apps(rep: Report, net: Net, url: str, data: dict, repo: Path | None,
               scan_apks: bool = True, all_versions: bool = False) -> None:
    base = url.rstrip("/")
    for pkg_id, pkg in sorted(data.get("packages", {}).items()):
        vers = _versions(pkg)
        if not vers:
            rep.add("version", pkg_id, "fail", "listed with no version")
            continue
        sets = {tuple(sorted(v["manifest"].get("signer", {}).get("sha256", []))) for v in vers}
        newest = list(vers[0]["manifest"].get("signer", {}).get("sha256", []))
        meta_p = repo / "metadata" / f"{pkg_id}.yml" if repo else None
        if meta_p and meta_p.exists():
            pins = checks.parse_pins(meta_p.read_text())
            try:
                for v in vers:
                    checks.check_signers(v["manifest"].get("signer", {}).get("sha256", []), pins)
                rep.add("signer", pkg_id, "ok", f"{len(vers)} version(s), every signer pinned "
                        f"({len(pins)} pin(s){', rotation' if len(sets) > 1 else ''})")
            except checks.CheckFailed as e:
                rep.add("signer", pkg_id, "fail", str(e))
        elif repo:
            rep.add("signer", pkg_id, "fail", f"served, but no metadata/{pkg_id}.yml in {repo}")
        elif len(sets) > 1:
            rep.add("signer", pkg_id, "warn", f"{len(sets)} different signer sets across versions, "
                    "pass --repo to check them against the pins")
        else:
            rep.add("signer", pkg_id, "ok" if newest else "fail",
                    f"one signer {newest[0][:16]}... across {len(vers)} version(s) (pins not checked, no --repo)"
                    if newest else "the index lists no signer")
        if scan_apks:
            for v in (vers if all_versions else vers[:1]):
                _check_pairip(rep, net, base + v["file"]["name"], pkg_id, v["manifest"]["versionCode"])
        else:
            rep.add("pairip", pkg_id, "skip", "--no-apk")
        live = vers[0]["manifest"]["versionCode"]
        local = _local_codes(repo, pkg_id) if repo else {}
        if not local:
            rep.add("version", pkg_id, "ok", f"live {live}" + ("" if repo else " (no --repo to compare)"))
        else:
            ahead = {src: c for src, c in local.items() if c > live}
            rep.add("version", pkg_id, "fail" if ahead else "ok",
                    f"live {live} is behind {', '.join(f'{s} {c}' for s, c in ahead.items())}: not deployed"
                    if ahead else f"live {live}, local {max(local.values())}")
    if repo and (repo / "metadata").is_dir():
        served = set(data.get("packages", {}))
        for p in sorted((repo / "metadata").glob("*.yml")):
            if p.stem not in served:
                if "local index" in _local_codes(repo, p.stem):
                    rep.add("version", p.stem, "fail", "in the local index, not in the live one: not deployed")
                else:
                    rep.add("version", p.stem, "warn", "metadata only, no APK published yet")


def _local_codes(repo: Path, pkg_id: str) -> dict:
    out = {}
    meta = repo / "metadata" / f"{pkg_id}.yml"
    if meta.exists():
        m = re.search(r"^CurrentVersionCode:\s*(\d+)", meta.read_text(), re.M)
        if m:
            out["metadata"] = int(m.group(1))
    idx = repo / "repo" / "index-v2.json"
    if idx.exists():
        try:
            vers = json.loads(idx.read_text()).get("packages", {}).get(pkg_id, {}).get("versions", {})
            if vers:
                out["local index"] = max(v["manifest"]["versionCode"] for v in vers.values())
        except (json.JSONDecodeError, KeyError):
            pass
    return out


def _check_pairip(rep: Report, net: Net, apk_url: str, pkg_id: str, code: int) -> None:
    subject = f"{pkg_id} {code}"
    try:
        f = net.open_apk(apk_url)
        try:
            with zipfile.ZipFile(f) as z:
                dex = [n for n in z.namelist() if checks._DEX_RE.fullmatch(n)]
                hit = next((n for n in dex if checks.dex_has_pairip(z.read(n))), None)
        finally:
            f.close()
    except (OSError, zipfile.BadZipFile, urllib.error.URLError) as e:
        rep.add("pairip", subject, "fail", f"cannot read the APK: {e}")
        return
    if not dex:
        rep.add("pairip", subject, "fail", "no classes*.dex in the APK")
    elif hit:
        rep.add("pairip", subject, "fail", f"{hit} contains com.pairip (Play automatic protection): "
                "the app shows a licence wall outside Play")
    else:
        rep.add("pairip", subject, "ok", f"{len(dex)} dex file(s), no com.pairip")


# ---- storage budget -------------------------------------------------------------------

def parse_budget(spec: str | None) -> tuple[int, str] | None:
    """`github-pages` | `<bytes>` | `<n>[k|M|G|T][i][B]` (decimal, `i` = binary) -> (bytes, label)."""
    if spec is None or spec.strip().lower() in ("", "none"):
        return None
    s = spec.strip()
    if s.lower() in BUDGETS:
        return BUDGETS[s.lower()][0], s.lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]i?)?b?", s, re.I)
    if not m or float(m.group(1)) <= 0:
        raise ValueError(f"budget {spec!r}: give github-pages, a byte count, or e.g. 500M / 2G")
    return int(float(m.group(1)) * _UNITS[(m.group(2) or "").lower()]), s


def human(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"


def archive_url(url: str) -> str | None:
    """fdroidserver's default layout: <base>/repo next to <base>/archive."""
    return re.sub(r"/repo$", "/archive", url) if url.endswith("/repo") else None


def _archive_index(rep: Report, net: Net, url: str, key: str, runner: Callable) -> tuple[dict, str]:
    """The archive's index-v2 data when it is served and signed by the repo key, plus a note."""
    aurl = archive_url(url)
    if not aurl:
        return {}, "archive not checked (URL does not end in /repo)"
    jar = net.get(aurl + "/entry.jar")
    if jar is None:
        return {}, "no archive served"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "entry.jar"
        p.write_bytes(jar)
        try:
            akey = keys.jar_fingerprint(p, runner=runner)
        except keys.KeyReadError as e:
            rep.add("storage", "archive", "warn", f"archive/entry.jar unreadable, archive not counted: {e}")
            return {}, "archive unreadable"
    if akey != key:
        rep.add("storage", "archive", "warn", f"archive/entry.jar signer {akey[:16]}... is not the repo key, "
                "archive not counted")
        return {}, "archive signer differs"
    try:
        entry = _jar_entry_json(jar)
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return {}, "archive entry.jar holds no entry.json"
    iname = entry.get("index", {}).get("name", "/index-v2.json")
    raw = net.get(aurl + iname)
    if raw is None or hashlib.sha256(raw).hexdigest() != entry.get("index", {}).get("sha256", ""):
        rep.add("storage", "archive", "warn", f"archive{iname} missing or not the signed copy, archive not counted")
        return {}, "archive index unverified"
    return json.loads(raw), "archive counted"


def archive_older(repo: Path | None) -> int | None:
    """archive_older from the working tree's config.yml (fdroidserver's default is 3)."""
    cfg = repo / "config.yml" if repo else None
    if not cfg or not cfg.exists():
        return None
    m = re.search(r"^archive_older:\s*(\d+)", cfg.read_text(), re.M)
    return int(m.group(1)) if m else 3


def check_storage(rep: Report, net: Net, url: str, key: str, data: dict, budget: tuple[int, str] | None,
                  runner: Callable, keep: int | None = None) -> None:
    """Sum the APK bytes both signed indexes list (file.size) and compare them with the budget.
    With `keep` (archive_older from --repo) also project the steady state: every app holding
    `keep` versions the size of its newest APK in repo/."""
    archive, note = _archive_index(rep, net, url, key, runner)
    apps: dict[str, dict] = {}
    unsized = 0
    for where, idx in (("repo", data), ("archive", archive)):
        for pkg_id, pkg in idx.get("packages", {}).items():
            a = apps.setdefault(pkg_id, {"package": pkg_id, "bytes": 0, "versions": 0,
                                         "repo_bytes": 0, "archive_bytes": 0})
            for v in pkg.get("versions", {}).values():
                size = v.get("file", {}).get("size")
                if not isinstance(size, int):
                    unsized += 1
                    continue
                a["bytes"] += size
                a[f"{where}_bytes"] += size
                a["versions"] += 1
    ranked = sorted(apps.values(), key=lambda a: (-a["bytes"], a["package"]))
    total = sum(a["bytes"] for a in ranked)
    repo_b = sum(a["repo_bytes"] for a in ranked)
    arch_b = sum(a["archive_bytes"] for a in ranked)
    n = sum(a["versions"] for a in ranked)
    newest = {}
    for pkg_id, pkg in data.get("packages", {}).items():
        vers = _versions(pkg)
        if vers and isinstance(vers[0].get("file", {}).get("size"), int):
            newest[pkg_id] = vers[0]["file"]["size"]
    projected = sum(newest.values()) * keep if keep else None
    rep.storage = {"total_bytes": total, "repo_bytes": repo_b, "archive_bytes": arch_b, "apks": n,
                   "archive_older": keep, "projected_bytes": projected,
                   "archive": note, "budget_bytes": budget[0] if budget else None,
                   "budget": budget[1] if budget else None,
                   "percent": round(100 * total / budget[0], 1) if budget else None,
                   "apps": ranked}
    top = ", ".join(f"{a['package']} {human(a['bytes'])}" for a in ranked[:3] if a["bytes"])
    what = (f"{human(total)} in {n} APK(s) (repo {human(repo_b)}, archive {human(arch_b)}"
            f"{'' if note == 'archive counted' else ': ' + note})" + (f"; biggest: {top}" if top else ""))
    if unsized:
        rep.add("storage", "sizes", "warn", f"{unsized} APK(s) listed without file.size, not counted")
    if not budget:
        rep.add("storage", "total", "ok", what + "; no budget set (--budget github-pages for Pages)")
        return
    pct = total / budget[0]
    st = "fail" if pct >= 1 else "warn" if pct >= BUDGET_WARN else "ok"
    tail = {"ok": "", "warn": f": over {BUDGET_WARN:.0%}, lower archive_older or drop old versions",
            "fail": ": OVER BUDGET, the next deploy can be rejected; lower archive_older or drop old versions"}[st]
    rep.add("storage", "total", st, f"{what}; {pct * 100:.1f}% of {budget[1]} {human(budget[0])}{tail}")
    if projected and st == "ok" and projected >= budget[0] * BUDGET_WARN:
        rep.add("storage", "projected", "warn",
                f"with archive_older {keep} every app keeps {keep} version(s) in repo/: about {human(projected)}, "
                f"{100 * projected / budget[0]:.0f}% of {human(budget[0])} once each app has {keep} releases; "
                f"archive_older {_fits(sum(newest.values()), budget[0])} keeps it under {BUDGET_WARN:.0%}")


def _fits(newest_total: int, budget: int) -> int:
    """The largest archive_older whose steady state stays under the warning line (at least 1)."""
    return max(1, int(budget * BUDGET_WARN // newest_total)) if newest_total else 1


# ---- the command -------------------------------------------------------------------

def doctor(url: str, repo: Path | None = None, site: str | None = None, net: Net | None = None,
           runner: Callable | None = None, scan_apks: bool = True, all_versions: bool = False,
           budget: tuple[int, str] | None = None) -> Report:
    net = net or Net()
    runner = runner or _run
    url = url.rstrip("/")
    rep = Report(url=url, site=(site or site_root(url, _brand(repo))).rstrip("/"))
    check_tls(rep, net, url)
    key, data = check_index(rep, net, url, runner)
    if _host(rep.site)[0] != _host(url)[0]:
        check_tls(rep, net, rep.site)
    check_landing(rep, net, url, rep.site, key)
    check_apps(rep, net, url, data, repo, scan_apks=scan_apks, all_versions=all_versions)
    check_storage(rep, net, url, key, data, budget, runner, keep=archive_older(repo))
    return rep


def _brand(repo: Path | None):
    from .site.render import Brand, load_brand
    return load_brand(repo) if repo else Brand()
