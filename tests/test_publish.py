import functools
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

from fdroidkit import cli, publish as pub
from fdroidkit.checks import CheckFailed
from fdroidkit.publish import Options, PublishError
from fdroidkit.sources import play

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import make_fixtures as fx  # noqa: E402

BIN = Path(__file__).parent / "fixtures" / "bin"
PKG = "com.example.app"


@pytest.fixture
def webroot(tmp_path, monkeypatch):
    root = tmp_path / "www"
    root.mkdir()
    monkeypatch.setenv("FAKE_WEBROOT", str(root))
    handler = functools.partial(_QuietHandler, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield root, f"http://127.0.0.1:{srv.server_address[1]}/fdroid/repo"
    srv.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def make_repo(tmp_path, meta):
    repo = tmp_path / "fdroid"
    (repo / "metadata").mkdir(parents=True)
    (repo / "repo").mkdir()
    (repo / "metadata" / f"{PKG}.yml").write_text(meta)
    return repo


def opts(repo, apk, code=13, name="1.3.0", **kw):
    kw.setdefault("readback_delay", 0)
    return Options(repo=repo, package=PKG, version_code=code, version_name=name, source="file",
                   apk=apk, fdroid=str(BIN / "fdroid"), build_tools=BIN, **kw)


def test_publish_one_key_end_to_end(tmp_path, webroot):
    root, url = webroot
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    hook_marker = repo / "site-rendered"
    assert pub.publish(opts(repo, apk, live_url=url, after_update=[f"touch {hook_marker}"])) == 0
    meta = (repo / "metadata" / f"{PKG}.yml").read_text()
    assert "CurrentVersionCode: 13" in meta and "CurrentVersion: 1.3.0" in meta
    assert (repo / "repo" / f"{PKG}_13.apk").exists()
    assert (root / "fdroid" / "repo" / f"{PKG}_13.apk").exists()
    assert hook_marker.exists()
    assert (repo / "fdroid-calls.log").read_text().split() == ["update", "deploy"]


def test_publish_no_deploy_skips_fdroid_deploy(tmp_path, webroot):
    root, _ = webroot
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    assert pub.publish(opts(repo, apk, deploy=False, readback=False)) == 0
    assert (repo / "repo" / f"{PKG}_13.apk").exists()
    assert not (root / "fdroid" / "repo" / f"{PKG}_13.apk").exists()
    assert (repo / "fdroid-calls.log").read_text().split() == ["update"]


def test_publish_rotated_key_needs_both_pins(tmp_path, webroot):
    _, url = webroot
    apk = fx.make_apk(tmp_path / "in.apk", "rotated", PKG, 13, "1.3.0")
    only_old = make_repo(tmp_path / "a", fx.metadata(fx.PLAY))
    with pytest.raises(CheckFailed, match="not pinned"):
        pub.publish(opts(only_old, apk, live_url=url))
    assert not (only_old / "repo" / f"{PKG}_13.apk").exists()
    both = make_repo(tmp_path / "b", fx.metadata(fx.PLAY, fx.PLAY_NEW))
    assert pub.publish(opts(both, apk, live_url=url)) == 0


def test_pairip_refused_and_nothing_written(tmp_path):
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    before = (repo / "metadata" / f"{PKG}.yml").read_text()
    apk = fx.make_apk(tmp_path / "in.apk", "pairip", PKG, 13, "1.3.0")
    with pytest.raises(CheckFailed, match="classes2.dex contains com.pairip"):
        pub.publish(opts(repo, apk, live_url="http://unused"))
    assert (repo / "metadata" / f"{PKG}.yml").read_text() == before
    assert not list((repo / "repo").iterdir())
    assert not (repo / "fdroid-calls.log").exists()


def test_stranger_signer_refused(tmp_path):
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    apk = fx.make_apk(tmp_path / "in.apk", "stranger", PKG, 13, "1.3.0")
    with pytest.raises(CheckFailed, match="not pinned"):
        pub.publish(opts(repo, apk, live_url="http://unused"))


def test_source_stamp_is_never_a_pin(tmp_path):
    # Pinning only the Source Stamp digest must not let the APK through.
    repo = make_repo(tmp_path, fx.metadata(fx.STAMP))
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    with pytest.raises(CheckFailed, match="not pinned"):
        pub.publish(opts(repo, apk, dry_run=True))


@pytest.mark.parametrize("code,name,match", [(12, "1.2.0", "not higher"), (13, "1.3.1", "identity")])
def test_version_and_identity_refused(tmp_path, code, name, match):
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    with pytest.raises(CheckFailed, match=match):
        pub.publish(opts(repo, apk, code=code, name=name, dry_run=True))


def test_version_code_counts_apks_already_in_repo(tmp_path):
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY, code=None))
    (repo / "repo" / f"{PKG}_20.apk").write_bytes(b"old")
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    with pytest.raises(CheckFailed, match="published 20"):
        pub.publish(opts(repo, apk, dry_run=True))


def test_dry_run_publishes_nothing(tmp_path, capsys):
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    before = (repo / "metadata" / f"{PKG}.yml").read_text()
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    assert pub.publish(opts(repo, apk, dry_run=True)) == 0
    assert "dry run: all checks passed" in capsys.readouterr().out
    assert (repo / "metadata" / f"{PKG}.yml").read_text() == before
    assert not list((repo / "repo").iterdir())


def test_new_app_without_current_version_gets_one(tmp_path, webroot):
    _, url = webroot
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY, code=None))
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 1, "1.0.0")
    assert pub.publish(opts(repo, apk, code=1, name="1.0.0", live_url=url)) == 0
    assert "CurrentVersionCode: 1\n" in (repo / "metadata" / f"{PKG}.yml").read_text()


def test_readback_fails_when_live_index_lacks_version(tmp_path, webroot, monkeypatch):
    root, url = webroot
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    monkeypatch.setenv("FAKE_WEBROOT", str(tmp_path / "elsewhere"))  # deploy misses the web root
    with pytest.raises(PublishError, match="read-back failed"):
        pub.publish(opts(repo, apk, live_url=url, readback_attempts=2))


def test_live_url_from_config(tmp_path, webroot):
    _, url = webroot
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    (repo / "config.yml").write_text(f"repo_name: Example\nrepo_url: {url}\n")
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    assert pub.publish(opts(repo, apk)) == 0


def test_missing_metadata(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    with pytest.raises(PublishError, match="add-app"):
        pub.publish(opts(repo, tmp_path / "x.apk", dry_run=True))


def test_no_pins(tmp_path):
    repo = make_repo(tmp_path, "Categories:\n  - Games\n")
    with pytest.raises(CheckFailed, match="no AllowedAPKSigningKeys"):
        pub.publish(opts(repo, tmp_path / "x.apk", dry_run=True))


def test_public_files_written_world_readable(tmp_path, webroot):
    import os
    _, url = webroot
    old = os.umask(0o077)
    try:
        repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
        apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
        pub.publish(opts(repo, apk, live_url=url))
        assert (repo / "repo" / f"{PKG}_13.apk").stat().st_mode & 0o044 == 0o044
    finally:
        os.umask(old)


# ---- CLI ----

def test_cli_exit_codes(tmp_path, capsys):
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    good = fx.make_apk(tmp_path / "good.apk", "one_key", PKG, 13, "1.3.0")
    bad = fx.make_apk(tmp_path / "bad.apk", "pairip", PKG, 13, "1.3.0")
    base = ["--repo", str(repo), "publish", PKG, "13", "1.3.0", "--source", "file",
            "--build-tools", str(BIN), "--dry-run"]
    assert cli.main(base + ["--apk", str(good)]) == 0
    assert cli.main(base + ["--apk", str(bad)]) == 1
    assert "refused" in capsys.readouterr().err
    assert cli.main(base + ["--apk", str(tmp_path / "missing.apk")]) == 3


def test_cli_play_needs_service_account(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PLAY_SA_FILE", raising=False)
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    assert cli.main(["--repo", str(repo), "publish", PKG, "13", "1.3.0", "--build-tools", str(BIN),
                     "--dry-run"]) == 3
    assert "PLAY_SA_FILE" in capsys.readouterr().err


# ---- Play source ----

class _Req:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeApi:
    def __init__(self, answers):
        self.answers = list(answers)

    def generatedapks(self):
        return self

    def list(self, packageName, versionCode):
        return _Req(self.answers.pop(0))


def test_play_waits_for_generated_universal_apk():
    api = _FakeApi([RuntimeError("404"), {"generatedApks": [{"generatedSplitApks": []}]},
                    {"generatedApks": [{"generatedUniversalApk": {"downloadId": "dl-1"}}]}])
    sleeps = []
    assert play.find_universal(api, PKG, 13, sleep=sleeps.append) == {"downloadId": "dl-1"}
    assert len(sleeps) == 2


def test_play_gives_up():
    api = _FakeApi([{}] * 3)
    with pytest.raises(RuntimeError, match="no universal APK"):
        play.find_universal(api, PKG, 13, attempts=3, sleep=lambda s: None)


def test_bump_metadata_keeps_other_lines():
    meta = fx.metadata(fx.PLAY)
    out = pub.bump_metadata(meta, 14, "1.4.0")
    assert out.replace("1.4.0", "1.2.0").replace("14", "12") == meta
    assert json.dumps(out)  # plain text, no YAML round-trip
