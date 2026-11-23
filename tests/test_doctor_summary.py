"""actions/doctor/summary.py: job summary, annotations and the exit verdict of both doctor actions."""
import json
import subprocess
import sys
from pathlib import Path

SUMMARY = Path(__file__).resolve().parents[1] / "actions" / "doctor" / "summary.py"


def _report(tmp_path, warn=0, fail=0):
    checks = [{"name": "tls", "subject": "repo.example", "status": "ok", "detail": "valid | 80 days"}]
    checks += [{"name": "freshness", "subject": "index", "status": "warn", "detail": "old"}] * warn
    checks += [{"name": "signer", "subject": "com.x", "status": "fail", "detail": "not pinned"}] * fail
    p = tmp_path / "doctor.json"
    p.write_text(json.dumps({"url": "https://repo.example/fdroid/repo", "site": "https://repo.example",
                             "ok": fail == 0, "counts": {"ok": 1, "warn": warn, "fail": fail, "skip": 0},
                             "checks": checks}))
    return p


def _run(*args):
    return subprocess.run([sys.executable, str(SUMMARY), *map(str, args)], capture_output=True, text=True)


def test_healthy_summary(tmp_path):
    r = _run(_report(tmp_path))
    assert r.returncode == 0
    assert "fdroidkit doctor: healthy" in r.stdout
    assert "valid \\| 80 days" in r.stdout  # pipes escaped for the Markdown table


def test_fail_is_red_with_error_annotation(tmp_path):
    p = _report(tmp_path, fail=1)
    assert _run(p).returncode == 1
    r = _run(p, "--annotations")
    assert r.returncode == 1
    assert "::error title=fdroidkit doctor signer::com.x: not pinned" in r.stdout


def test_warn_only_fails_when_strict(tmp_path):
    p = _report(tmp_path, warn=1)
    assert _run(p).returncode == 0
    assert "::warning " in _run(p, "--annotations").stdout
    assert _run(p, "--fail-on-warn").returncode == 1
    assert "::error " in _run(p, "--annotations", "--fail-on-warn").stdout


def test_storage_table(tmp_path):
    p = _report(tmp_path)
    rep = json.loads(p.read_text())
    rep["storage"] = {"total_bytes": 434_100_000, "apks": 5, "budget_bytes": 10**9, "budget": "github-pages",
                      "percent": 43.4, "apps": [{"package": "io.x.big", "versions": 1, "repo_bytes": 211_500_000,
                                                 "archive_bytes": 0, "bytes": 211_500_000}]}
    p.write_text(json.dumps(rep))
    r = _run(p)
    assert r.returncode == 0
    assert "#### Storage: 434.1 MB in 5 APK(s), 43.4% of github-pages (1000.0 MB)" in r.stdout
    assert "| io.x.big | 1 | 211.5 MB | 0.0 MB | 211.5 MB |" in r.stdout
