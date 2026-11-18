"""Command line entry point: `fdroidkit <command>`.

Commands land one by one (see Roadmap in README.md). Unimplemented commands exit with
status 2 and say so. Exit codes: 0 ok, 1 a check refused the APK, 2 not implemented,
3 setup error (missing tool, metadata, config).
"""
from __future__ import annotations

import argparse
import sys

from . import __version__

NOT_YET = {
    "init": "0.1",
    "add-app": "0.1",
    "site": "0.1",
    "verify": "0.1",
}


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

    s = sub.add_parser("add-app", help="add metadata for a package and pin its signer")
    s.add_argument("package", help="application id, e.g. com.example.app")
    s.add_argument("--discover-signer", metavar="APK",
                   help="read the signer digest from this APK and pin it (Play signer expected)")
    s.add_argument("--pin", action="append", default=[], metavar="SHA256",
                   help="pin a signer certificate SHA-256 digest (repeatable, for rotated keys)")

    s = sub.add_parser("publish", help="check an APK, add it, update the index, deploy, read back")
    s.add_argument("package")
    s.add_argument("version_code", type=int)
    s.add_argument("version_name")
    s.add_argument("--source", choices=["play", "file", "github-release"], default="play")
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

    s = sub.add_parser("site", help="render the landing page from the signed index")
    s.add_argument("--out", default="site", help="output directory")

    sub.add_parser("verify", help="check that every fingerprint on the landing page matches the index key")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "publish":
        return _publish(args)
    print(f"fdroidkit {args.command}: not implemented yet (planned for {NOT_YET[args.command]}).",
          file=sys.stderr)
    return 2


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
        readback=not args.no_readback, play_sa_file=args.play_sa_file,
    )
    try:
        return publish(opts)
    except CheckFailed as e:
        print(f"fdroidkit publish: refused: {e}", file=sys.stderr)
        return 1
    except (PublishError, RuntimeError) as e:
        print(f"fdroidkit publish: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
