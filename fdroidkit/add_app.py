"""`fdroidkit add-app`: metadata skeleton for a package, with its signer pinned.

The pin (`AllowedAPKSigningKeys`) is what makes `publish` and `fdroid update` refuse an
APK signed by anyone else. `--discover-signer <apk>` reads it from a real APK instead of
asking the developer to copy a digest by hand, and checks first that the APK is the
package asked for and that every signer is Google's Play App Signing key (DN
`O=Google Inc.`): pinning the upload key by mistake would refuse every Play-signed APK,
and pinning a stranger's key would accept theirs. `--not-play` skips the DN check for
apps whose developer signs the APK.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import checks
from .checks import CheckFailed

PLAY_DN_MARK = "O=Google Inc."
_PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SIGNER_LINE = re.compile(
    # Same shapes as checks._SIGNER_RE: "Signer #1 certificate ..." (build-tools <= 36) and
    # "V3.0 Signer: certificate ..." / "V3.1 Signer: (minSdkVersion=33, ...) certificate ..."
    # (build-tools 37; the range stays in the label so DN and digest pair per signer).
    r"^((?:V\d+(?:\.\d+)? )?Signer(?: #\d+| \([^)\n]*\))?(?:: \([^)\n]*\))?):? certificate (DN|SHA-256 digest): (.+)$", re.M)
_BADGING_PKG = re.compile(r"name='([^']+)'")

LISTING_FILES = {
    "short_description.txt": "",
    "full_description.txt": "",
}


class AddAppError(Exception):
    """Setup problem (bad package name, missing tool, existing metadata)."""


@dataclass
class AddAppOptions:
    repo: Path
    package: str
    discover_apk: Path | None = None
    pins: list[str] = field(default_factory=list)
    not_play: bool = False
    name: str | None = None
    license: str = "Proprietary"
    website: str = ""
    source_code: str = ""
    build_tools: Path | None = None
    from_github: str | None = None
    asset: str | None = None
    tag: str | None = None


def parse_signer_certs(apksigner_output: str) -> list[tuple[str, str]]:
    """(DN, SHA-256) per signer; Source Stamp lines start differently and are skipped."""
    by_label: dict[str, dict[str, str]] = {}
    for label, kind, val in _SIGNER_LINE.findall(apksigner_output):
        by_label.setdefault(label, {})[kind] = val.strip()
    out: dict[str, str] = {}
    for v in by_label.values():
        if "SHA-256 digest" in v:
            out.setdefault(v["SHA-256 digest"], v.get("DN", ""))
    return [(dn, digest) for digest, dn in out.items()]


def check_play_signers(certs: list[tuple[str, str]]) -> None:
    if not certs:
        raise CheckFailed("signer: apksigner reported no signer")
    for dn, digest in certs:
        if PLAY_DN_MARK not in dn:
            raise CheckFailed(
                f"signer {digest[:16]}... has DN '{dn or '?'}', not Play App Signing ({PLAY_DN_MARK}). "
                "This is probably the upload key or a local build: take the universal APK Play "
                "generates (App bundle explorer), or pass --not-play if you sign APKs yourself")


def discover(apk: Path, package: str, bt: Path, runner: Callable, not_play: bool) -> list[str]:
    badging = runner([bt / "aapt2", "dump", "badging", apk]).splitlines()
    first = badging[0] if badging else ""
    m = _BADGING_PKG.search(first)
    if not m:
        raise CheckFailed(f"identity: aapt2 gave no package name for {apk}")
    if m.group(1) != package:
        raise CheckFailed(f"identity: {apk} is {m.group(1)}, not {package}")
    certs = parse_signer_certs(runner([bt / "apksigner", "verify", "--print-certs", apk]))
    if not not_play:
        check_play_signers(certs)
    elif not certs:
        raise CheckFailed("signer: apksigner reported no signer")
    checks.check_no_pairip(apk)
    return [d for _, d in certs]


def render_metadata(o: AddAppOptions, pins: list[str], source_note: str) -> str:
    name = o.name or o.package.rsplit(".", 1)[-1].capitalize()
    lines = [
        "# Written by fdroidkit add-app. Fields: https://f-droid.org/docs/Build_Metadata_Reference/",
        "Categories:",
        "  - System",
        f"License: {o.license}",
    ]
    if o.website:
        lines.append(f"WebSite: {o.website}")
    if o.source_code:
        lines.append(f"SourceCode: {o.source_code}")
    lines += [
        "",
        f"Name: {name}",
        f"AutoName: {name}",
        "",
        f"# {source_note}",
        "# Every APK signer must be listed here or `fdroidkit publish` and `fdroid update` refuse",
        "# the APK. After a signing-key rotation, add the new digest next to the old one.",
        "AllowedAPKSigningKeys:",
        *[f"  - {p}" for p in pins],
        "",
    ]
    return "\n".join(lines)


def merge_pins(meta: str, new: list[str]) -> tuple[str, list[str]]:
    have = checks.parse_pins(meta)
    add = [p for p in new if p not in have]
    if not add:
        return meta, []
    block = "".join(f"  - {p}\n" for p in add)
    m = re.search(r"^AllowedAPKSigningKeys:\s*\n((?:\s*-\s*[0-9a-f]{64}\s*\n)*)", meta, re.M)
    if m:
        meta = meta[:m.end()] + block + meta[m.end():]
    else:
        meta = meta.rstrip("\n") + "\nAllowedAPKSigningKeys:\n" + block
    return meta, add


def add_app(o: AddAppOptions, runner: Callable | None = None, log=print) -> Path:
    from .publish import find_build_tools, metadata_path, run

    runner = runner or run
    repo = o.repo.resolve()
    if not (repo / "config.yml").exists():
        raise AddAppError(f"no config.yml in {repo} (run `fdroidkit init` first, or pass --repo)")
    if not _PKG_RE.match(o.package):
        raise AddAppError(f"{o.package!r} is not an application id like com.example.app")
    pins = [p.lower() for p in o.pins]
    bad = [p for p in pins if not _HEX64.match(p)]
    if bad:
        raise AddAppError(f"--pin needs a 64-hex SHA-256 digest, got {bad[0]!r}")
    note = "Pins given with --pin."
    if o.from_github and o.discover_apk:
        raise AddAppError("--from-github and --discover-signer both name an APK: pass one")
    if o.from_github:
        import tempfile

        from .sources import github
        src = {"type": "github-release", "repo": github.check_repo(o.from_github)}
        if o.asset:
            src["asset"] = o.asset
        bt = find_build_tools(o.build_tools, repo)
        with tempfile.TemporaryDirectory(prefix="fdroidkit-") as td:
            apk = Path(td) / "release.apk"
            a = github.fetch(src["repo"], apk, pattern=o.asset or "*.apk", tag=o.tag,
                             token=github.token_from(None), log=log)
            found = discover(apk, o.package, bt, runner, True)
        pins += [p for p in found if p not in pins]
        note = f"Self-signed APK signer, read from {a.name} ({src['repo']} {a.tag}) by --from-github."
        log(f"signer{'s' if len(found) > 1 else ''} found: {', '.join(found)}")
        log(f"{github.save_source_config(repo, o.package, src)}: apps.{o.package}.source = {src}")
    elif o.discover_apk:
        apk = Path(o.discover_apk)
        if not apk.is_file():
            raise AddAppError(f"--discover-signer: no file {apk}")
        bt = find_build_tools(o.build_tools, repo)
        found = discover(apk, o.package, bt, runner, o.not_play)
        pins += [p for p in found if p not in pins]
        note = ("Self-signed APK signer, read from " if o.not_play else
                "Play App Signing key (DN O=Google Inc.), read from ") + apk.name + " by --discover-signer."
        log(f"signer{'s' if len(found) > 1 else ''} found: {', '.join(found)}")
    if not pins:
        raise AddAppError("nothing to pin: pass --discover-signer <apk> or --pin <sha256>")

    meta_file = metadata_path(repo, o.package)
    if meta_file.exists():
        meta, added = merge_pins(meta_file.read_text(), pins)
        if not added:
            log(f"{meta_file}: every signer already pinned, nothing changed")
            return meta_file
        meta_file.write_text(meta)
        log(f"{meta_file}: pinned {len(added)} more signer(s) (key rotation); other fields untouched")
        return meta_file

    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(render_metadata(o, pins, note))
    listing = repo / "metadata" / o.package / "en-US"
    (listing / "phoneScreenshots").mkdir(parents=True, exist_ok=True)
    for fn, body in LISTING_FILES.items():
        f = listing / fn
        if not f.exists():
            f.write_text(body)
    log(f"wrote {meta_file} with {len(pins)} pinned signer(s)")
    log(f"listing: fill {listing}/short_description.txt, full_description.txt, add icon.png and phoneScreenshots/")
    if o.from_github:
        log(f"next: fdroidkit publish {o.package} --source github-release --dry-run")
    else:
        log(f"next: fdroidkit publish {o.package} <versionCode> <versionName> --dry-run")
    return meta_file
