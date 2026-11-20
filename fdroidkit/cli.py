"""Command line entry point: `fdroidkit <command>`.

Commands land one by one (see Roadmap in README.md). Unimplemented commands exit with
status 2 and say so. Exit codes: 0 ok, 1 a check refused the APK, 2 not implemented,
3 setup error (missing tool, metadata, config).
"""
from __future__ import annotations

import argparse
import sys

from . import __version__

NOT_YET: dict[str, str] = {}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fdroidkit",
        description="Run your own F-Droid repository: publish Play-signed APKs from CI.",
    )
    p.add_argument("--version", action="version", version=f"fdroidkit {__version__}")
    p.add_argument("--repo", default=".", help="F-Droid repo working tree (default: current dir)")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("init", help="create the repo, its index key and a secret-free config.yml")
    s.add_argument("--name", required=True, help="repo name shown in F-Droid clients")
    s.add_argument("--url", required=True, help="public repo URL, e.g. https://fdroid.example.com/fdroid/repo")
    s.add_argument("--description", default="", help="repo description")
    s.add_argument("--pass-env", default="FDROID_KEYSTORE_PASS", metavar="NAME",
                   help="environment variable holding the index key passphrase; config.yml reads it "
                        "as {env: NAME} (default: FDROID_KEYSTORE_PASS)")
    s.add_argument("--keystore", help="where to create the index key (default: ~/.config/fdroidkit/<alias>.p12, "
                   "outside the repo)")
    s.add_argument("--alias", help="key alias (default: the host of --url)")
    s.add_argument("--dname", help="key distinguished name (default: CN=<alias>, OU=F-Droid)")
    s.add_argument("--icon", help="repo icon PNG, 512 px (default: a plain placeholder to replace)")
    s.add_argument("--sdk-path", default=None, help="Android SDK for fdroidserver (default: $ANDROID_HOME if set)")
    s.add_argument("--archive-older", type=int, default=3, help="versions kept in repo/ before archive/ (default 3)")

    s = sub.add_parser("add-app", help="add metadata for a package and pin its signer")
    s.add_argument("package", help="application id, e.g. com.example.app")
    s.add_argument("--discover-signer", metavar="APK",
                   help="read the signer digest from this APK and pin it (Play signer expected)")
    s.add_argument("--from-github", metavar="OWNER/NAME",
                   help="take the APK from this repo's latest GitHub release, pin its self-signed signer "
                        "and save the source in fdroidkit.json (implies --not-play)")
    s.add_argument("--asset", default=None, metavar="GLOB", help="release asset glob for --from-github (default *.apk)")
    s.add_argument("--tag", default=None, help="release tag for --from-github (default: latest release)")
    s.add_argument("--pin", action="append", default=[], metavar="SHA256",
                   help="pin a signer certificate SHA-256 digest (repeatable, for rotated keys)")
    s.add_argument("--not-play", action="store_true",
                   help="the APK is signed by you, not by Play App Signing: skip the O=Google Inc. check")
    s.add_argument("--name", help="app name in clients (default: last part of the package)")
    s.add_argument("--license", default="Proprietary", help="License field (default: Proprietary)")
    s.add_argument("--website", default="", help="WebSite field")
    s.add_argument("--source-code", default="", help="SourceCode field")
    s.add_argument("--build-tools", help="Android build-tools dir with aapt2 + apksigner")

    s = sub.add_parser("publish", help="check an APK, add it, update the index, deploy, read back")
    s.add_argument("package")
    s.add_argument("version_code", type=int, nargs="?",
                   help="versionCode to publish (optional for github-release: read from the APK)")
    s.add_argument("version_name", nargs="?", help="versionName (optional for github-release)")
    s.add_argument("--source", choices=["play", "file", "github-release"], default="play")
    s.add_argument("--github-repo", metavar="OWNER/NAME",
                   help="GitHub repo for --source github-release (default: apps.<package>.source.repo in "
                        "fdroidkit.json). Not --repo: that is the F-Droid working tree")
    s.add_argument("--asset", metavar="GLOB", help="release asset glob (default: fdroidkit.json, else *.apk)")
    s.add_argument("--tag", help="release tag (default: the latest non-prerelease release)")
    s.add_argument("--apk", help="APK path for --source file")
    s.add_argument("--dry-run", action="store_true", help="run every check, publish nothing")
    s.add_argument("--play-sa-file", help="Play service-account JSON (default: $PLAY_SA_FILE)")
    s.add_argument("--fdroid", help="fdroid binary (default: fdroid on PATH, then ~/.local/bin/fdroid)")
    s.add_argument("--build-tools", help="Android build-tools dir with aapt2 + apksigner "
                   "(default: newest under $ANDROID_HOME, config.yml sdk_path or ~/android)")
    s.add_argument("--live-url", help="public repo URL for the read-back (default: repo_url in config.yml)")
    s.add_argument("--after-update", action="append", default=[], metavar="CMD",
                   help="shell command run in the repo after `fdroid update`, e.g. a landing page render (repeatable)")
    s.add_argument("--rsync-site", metavar="DEST", help="rsync <repo>/site/ to DEST after deploy (landing page)")
    s.add_argument("--no-readback", action="store_true", help="skip the live index read-back")
    s.add_argument("--no-deploy", action="store_true",
                   help="skip `fdroid deploy` (static hosts that take an artifact, e.g. GitHub Pages)")

    s = sub.add_parser("site", help="render the landing page from the signed index")
    s.add_argument("--out", default="site", help="output directory, relative to the repo (default: site)")

    s = sub.add_parser("verify", help="check that every fingerprint on the landing page matches the index key")
    s.add_argument("--site", default="site", help="landing page directory, relative to the repo (default: site)")
    s.add_argument("--live", nargs="?", const="config", metavar="URL",
                   help="take the key from the live entry.jar (default URL: repo_url in config.yml)")

    s = sub.add_parser("doctor", help="audit a live repo: TLS, index signature, landing page, pins, pairip, versions")
    s.add_argument("--url", help="public repo URL (default: repo_url in config.yml of --repo)")
    s.add_argument("--repo", dest="doctor_repo", metavar="DIR",
                   help="local repo working tree: check signers against its pins and versions against its "
                        "metadata (without it only the live repo is read)")
    s.add_argument("--site-url", help="landing page URL (default: fdroidkit.json site_url, else the repo URL "
                   "without /fdroid/repo)")
    s.add_argument("--no-apk", action="store_true", help="skip the APK pairip scan (no APK bytes read)")
    s.add_argument("--all-versions", action="store_true", help="scan every served APK, not only the newest per app")
    s.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "init":
        return _init(args)
    if args.command == "add-app":
        return _add_app(args)
    if args.command == "publish":
        return _publish(args)
    if args.command == "site":
        return _site(args)
    if args.command == "verify":
        return _verify(args)
    if args.command == "doctor":
        return _doctor(args)
    print(f"fdroidkit {args.command}: not implemented yet (planned for {NOT_YET.get(args.command, 'later')}).",
          file=sys.stderr)
    return 2


def _init(args) -> int:
    import os
    from pathlib import Path

    from .init import InitError, InitOptions, init

    o = InitOptions(
        repo=Path(args.repo), name=args.name, url=args.url, description=args.description,
        pass_env=args.pass_env, keystore=Path(args.keystore) if args.keystore else None,
        alias=args.alias, dname=args.dname, icon=Path(args.icon) if args.icon else None,
        sdk_path=args.sdk_path or os.environ.get("ANDROID_HOME") or None,
        archive_older=args.archive_older,
    )
    try:
        init(o)
    except (InitError, OSError) as e:
        print(f"fdroidkit init: {e}", file=sys.stderr)
        return 3
    return 0


def _add_app(args) -> int:
    from pathlib import Path

    from .add_app import AddAppError, AddAppOptions, add_app
    from .checks import CheckFailed
    from .publish import PublishError

    o = AddAppOptions(
        repo=Path(args.repo), package=args.package,
        discover_apk=Path(args.discover_signer) if args.discover_signer else None,
        pins=args.pin, not_play=args.not_play, name=args.name, license=args.license,
        website=args.website, source_code=args.source_code,
        build_tools=Path(args.build_tools) if args.build_tools else None,
        from_github=args.from_github, asset=args.asset, tag=args.tag,
    )
    try:
        add_app(o)
    except CheckFailed as e:
        print(f"fdroidkit add-app: refused: {e}", file=sys.stderr)
        return 1
    except (AddAppError, PublishError, RuntimeError, OSError) as e:
        print(f"fdroidkit add-app: {e}", file=sys.stderr)
        return 3
    return 0


def _publish(args) -> int:
    from pathlib import Path

    from .checks import CheckFailed
    from .publish import Options, PublishError, publish

    opts = Options(
        repo=Path(args.repo), package=args.package, version_code=args.version_code,
        version_name=args.version_name, source=args.source,
        apk=Path(args.apk) if args.apk else None, dry_run=args.dry_run,
        fdroid=args.fdroid, build_tools=Path(args.build_tools) if args.build_tools else None,
        live_url=args.live_url, after_update=args.after_update, rsync_site=args.rsync_site,
        readback=not args.no_readback, deploy=not args.no_deploy, play_sa_file=args.play_sa_file,
        github_repo=args.github_repo, asset=args.asset, tag=args.tag,
    )
    try:
        return publish(opts)
    except CheckFailed as e:
        print(f"fdroidkit publish: refused: {e}", file=sys.stderr)
        return 1
    except (PublishError, RuntimeError) as e:
        print(f"fdroidkit publish: {e}", file=sys.stderr)
        return 3


def _site(args) -> int:
    from pathlib import Path

    from .site.keys import KeyReadError
    from .site.render import SiteError, render
    from .site.verify import verify

    try:
        res = render(Path(args.repo), Path(args.out))
        rep = verify(Path(args.repo), Path(res.out).resolve())  # res.out already includes the repo
    except (SiteError, KeyReadError, OSError) as e:
        print(f"fdroidkit site: {e}", file=sys.stderr)
        return 3
    print(f"rendered {len(res.apps)} app(s) into {res.out}: {', '.join(res.apps)}")
    print(f"fingerprint {res.fingerprint}, QR {'yes' if res.qr else 'no'}")
    print("\n".join(rep.lines()))
    return 0 if rep.ok else 1


def _verify(args) -> int:
    from pathlib import Path

    from .publish import read_config
    from .site.keys import KeyReadError
    from .site.verify import verify

    live = args.live
    if live == "config":
        live = read_config(Path(args.repo)).get("repo_url")
        if not live:
            print("fdroidkit verify: --live needs a URL or repo_url in config.yml", file=sys.stderr)
            return 3
    try:
        rep = verify(Path(args.repo), Path(args.site), live_address=live)
    except (KeyReadError, OSError) as e:
        print(f"fdroidkit verify: {e}", file=sys.stderr)
        return 3
    print("\n".join(rep.lines()))
    return 0 if rep.ok else 1


def _doctor(args) -> int:
    import urllib.error
    from pathlib import Path

    from .doctor import DoctorError, doctor
    from .publish import read_config
    from .site.keys import KeyReadError
    from .site.render import SiteError

    repo = Path(args.doctor_repo) if args.doctor_repo else None
    url = args.url or (read_config(repo).get("repo_url") if repo else None)
    if not url:
        print("fdroidkit doctor: give --url, or --repo with repo_url in its config.yml", file=sys.stderr)
        return 3
    try:
        rep = doctor(url, repo=repo, site=args.site_url, scan_apks=not args.no_apk,
                     all_versions=args.all_versions)
    except (DoctorError, KeyReadError, SiteError, urllib.error.URLError, OSError) as e:
        print(f"fdroidkit doctor: {e}", file=sys.stderr)
        return 3
    print(rep.to_json() if args.json else "\n".join(rep.lines()))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
