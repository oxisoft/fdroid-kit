import json
import shutil
import sys
from pathlib import Path

import pytest

from fdroidkit import cli, publish as pub
from fdroidkit import refresh as rf
from fdroidkit.publish import PublishError
from fdroidkit.refresh import RefreshOptions

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import make_fixtures as fx  # noqa: E402
from test_publish import BIN, PKG, make_repo, opts, webroot  # noqa: E402,F401


def ropts(repo, **kw):
    kw.setdefault("readback_delay", 0)
    return RefreshOptions(repo=repo, fdroid=str(BIN / "fdroid"), **kw)


def published(tmp_path, webroot):
    """A repo with com.example.app 13 published and deployed to the fake web root."""
    root, url = webroot
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    assert pub.publish(opts(repo, apk, live_url=url)) == 0
    (repo / "fdroid-calls.log").unlink()
    return repo, root, url


def test_refresh_reindexes_and_deploys_without_new_apk(tmp_path, webroot):
    repo, root, url = published(tmp_path, webroot)
    meta = repo / "metadata" / f"{PKG}.yml"
    before = meta.read_text()
    old_ts = json.loads((root / "fdroid" / "repo" / "index-v2.json").read_text())["repo"]["timestamp"]
    marker = repo / "site-rendered"
    assert rf.refresh(ropts(repo, live_url=url, after_update=[f"touch {marker}"])) == 0
    assert (repo / "fdroid-calls.log").read_text().split() == ["update", "deploy"]
    assert meta.read_text() == before  # no version bump
    assert sorted(p.name for p in (repo / "repo").glob("*.apk")) == [f"{PKG}_13.apk"]
    live = json.loads((root / "fdroid" / "repo" / "index-v2.json").read_text())
    assert live["repo"]["timestamp"] >= old_ts
    assert marker.exists()


def test_refresh_refuses_when_a_published_app_lost_its_apk(tmp_path, webroot):
    repo, _, url = published(tmp_path, webroot)
    (repo / "repo" / f"{PKG}_13.apk").unlink()
    with pytest.raises(PublishError, match=r"com.example.app \(13\).*--restore"):
        rf.refresh(ropts(repo, live_url=url))
    assert not (repo / "fdroid-calls.log").exists()


def test_refresh_restore_fetches_apks_from_the_live_index(tmp_path, webroot):
    repo, root, url = published(tmp_path, webroot)
    served = (root / "fdroid" / "repo" / f"{PKG}_13.apk").read_bytes()
    (repo / "repo" / f"{PKG}_13.apk").unlink()
    assert rf.refresh(ropts(repo, live_url=url, restore=True, deploy=False, readback=False)) == 0
    assert (repo / "repo" / f"{PKG}_13.apk").read_bytes() == served
    assert (repo / "fdroid-calls.log").read_text().split() == ["update"]
    assert not list((repo / "repo").glob("*.part"))


def test_refresh_dry_run_runs_nothing(tmp_path, webroot):
    repo, _, url = published(tmp_path, webroot)
    assert rf.refresh(ropts(repo, live_url=url, dry_run=True)) == 0
    assert not (repo / "fdroid-calls.log").exists()


def test_refresh_readback_waits_for_the_new_timestamp(tmp_path, webroot):
    repo, root, url = published(tmp_path, webroot)
    # deploy that never lands: live index stays old
    o = ropts(repo, live_url=url, deploy=False, readback_attempts=2)
    (root / "fdroid" / "repo" / "index-v2.json").write_text(json.dumps({"repo": {"timestamp": 1}}))
    with pytest.raises(PublishError, match="read-back failed: live index timestamp 1"):
        rf.refresh(o, sleep=lambda s: None)


def test_restore_first_publish_is_not_an_error(tmp_path, webroot):
    _, url = webroot
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    assert rf.restore(repo, url) == []


def test_restore_skips_path_names(tmp_path, webroot):
    root, url = webroot
    d = root / "fdroid" / "repo"
    d.mkdir(parents=True)
    (d / "index-v2.json").write_text(json.dumps({"packages": {"x": {"versions": {
        "a": {"file": {"name": "/../evil.apk"}}, "b": {"file": {"name": "/sub/x.apk"}}}}}}))
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY))
    assert rf.restore(repo, url) == []


def test_unbacked_ignores_unpublished_metadata(tmp_path):
    repo = make_repo(tmp_path, fx.metadata(fx.PLAY).replace("CurrentVersionCode: 12", "CurrentVersionCode: 0"))
    assert "CurrentVersionCode: 0" in (repo / "metadata" / f"{PKG}.yml").read_text()
    assert rf.unbacked_apps(repo) == []


def test_cli_refresh_and_restore(tmp_path, webroot, capsys):
    repo, _, url = published(tmp_path, webroot)
    shutil.move(repo / "repo" / f"{PKG}_13.apk", tmp_path / "away.apk")
    assert cli.main(["--repo", str(repo), "refresh", "--fdroid", str(BIN / "fdroid"), "--live-url", url,
                     "--no-readback"]) == 3
    assert "--restore" in capsys.readouterr().err
    assert cli.main(["--repo", str(repo), "restore", "--live-url", url]) == 0
    assert cli.main(["--repo", str(repo), "refresh", "--fdroid", str(BIN / "fdroid"), "--live-url", url]) == 0
