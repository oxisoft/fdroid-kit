"""Check every fingerprint printed on the landing page against the index signing key.

A wrong fingerprint on a public page is worse than none: it trains users to accept a
mismatch. So this is checked mechanically, never by eye, before every deploy.
"""
from __future__ import annotations

import re
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import keys

FP_RE = re.compile(r"[0-9A-Fa-f]{2}(?:[ :]?[0-9A-Fa-f]{2}){31}")
SCANNED = ("*.html", "*.txt")


@dataclass
class Report:
    key: str
    key_source: str
    files: dict = field(default_factory=dict)  # name -> number of fingerprints
    values: dict = field(default_factory=dict)  # normalised value -> match?
    qr: str = "skipped"  # ok | mismatch | missing | skipped

    @property
    def ok(self) -> bool:
        return bool(self.values) and all(self.values.values()) and self.qr != "mismatch"

    def lines(self) -> list[str]:
        out = [f"key ({self.key_source}): {self.key}"]
        out += [f"{name:<24} {n} fingerprint(s)" for name, n in self.files.items()]
        out += [f"  {v} {'MATCH' if ok else '*** MISMATCH ***'}" for v, ok in self.values.items()]
        out.append(f"qr.svg: {self.qr}")
        if not self.values:
            out.append("verdict: NO fingerprint found on the site, render it with `fdroidkit site`")
        else:
            out.append("verdict: " + ("all match" if self.ok else "MISMATCH, fix before deploying"))
        return out


def _live_jar(address: str, dest: Path) -> Path:
    url = address.rstrip("/") + "/entry.jar"
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (https repo URL)
        dest.write_bytes(r.read())
    return dest


def verify(repo: Path, site: Path, live_address: str | None = None,
           runner: Callable | None = None) -> Report:
    repo, site = Path(repo), Path(site)
    if not site.is_absolute():
        site = repo / site
    if live_address:
        with tempfile.TemporaryDirectory() as td:
            key = keys.jar_fingerprint(_live_jar(live_address, Path(td) / "entry.jar"), runner=runner)
        source = live_address.rstrip("/") + "/entry.jar"
    else:
        key = keys.jar_fingerprint(repo / "repo" / "entry.jar", runner=runner)
        source = "repo/entry.jar"
    rep = Report(key=key, key_source=source)
    for pattern in SCANNED:
        for p in sorted(site.rglob(pattern)):
            text = p.read_text(errors="replace").replace("<br>", " ")
            hits = FP_RE.findall(text)
            rep.files[str(p.relative_to(site))] = len(hits)
            for h in hits:
                v = re.sub(r"[^0-9A-Fa-f]", "", h).upper()
                rep.values[v] = v == key
    rep.qr = _check_qr(site, key)
    return rep


def _check_qr(site: Path, key: str) -> str:
    """Regenerate the QR from the page's add-repo link and compare it with qr.svg."""
    from .render import qr_svg

    svg, page = site / "qr.svg", site / "index.html"
    if not svg.exists():
        return "missing" if page.exists() and "qr.svg" in page.read_text() else "skipped"
    m = re.search(r'class="qr" href="([^"]+)"', page.read_text()) if page.exists() else None
    if not m:
        return "mismatch"
    import html
    add_url = html.unescape(m.group(1))
    if not add_url.endswith("fingerprint=" + key):
        return "mismatch"
    fresh = qr_svg(add_url)
    if fresh is None:
        return "skipped"
    return "ok" if _qr_modules(fresh) == _qr_modules(svg.read_text()) else "mismatch"


def _qr_modules(svg: str) -> list[str]:
    """The drawn modules (path data) of a QR SVG. Bytes differ between Python versions
    (ElementTree writes `"/>` on 3.12, `" />` on 3.13), the modules do not."""
    return re.findall(r'\sd="([^"]*)"', svg)
