#!/usr/bin/env python3
"""Turn a `fdroidkit doctor --json` report into a Markdown job summary (default) or CI
annotations (--annotations). Exit 1 when a check failed, or a warning with --fail-on-warn.
Used by both doctor actions (GitHub and Gitea/Forgejo); stdlib only."""
import json
import sys

ICON = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "skip": "skip"}


def main(argv: list[str]) -> int:
    path = argv[0]
    strict = "--fail-on-warn" in argv
    rep = json.load(open(path, encoding="utf-8"))
    c = rep.get("counts", {})
    bad = c.get("fail", 0) > 0 or (strict and c.get("warn", 0) > 0)
    if "--annotations" in argv:
        for ch in rep.get("checks", []):
            if ch["status"] in ("fail", "warn"):
                level = "error" if ch["status"] == "fail" or strict else "warning"
                print(f"::{level} title=fdroidkit doctor {ch['name']}::{ch['subject']}: {ch['detail']}")
        return 1 if bad else 0
    verdict = "RED" if bad else "healthy"
    print(f"### fdroidkit doctor: {verdict}\n")
    print(f"{rep.get('url')} (landing {rep.get('site')}): "
          f"{c.get('ok', 0)} ok, {c.get('warn', 0)} warn, {c.get('fail', 0)} fail, {c.get('skip', 0)} skipped\n")
    print("| check | subject | status | detail |\n|---|---|---|---|")
    for ch in rep.get("checks", []):
        detail = str(ch.get("detail", "")).replace("|", "\\|").replace("\n", " ")
        print(f"| {ch['name']} | {ch['subject']} | {ICON.get(ch['status'], ch['status'])} | {detail} |")
    print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
