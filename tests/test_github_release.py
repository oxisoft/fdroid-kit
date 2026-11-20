"""--source github-release and add-app --from-github against a fake GitHub API.

The release JSON is the real oxisoft/oxitalk v0.2.1 response (trimmed); its asset URLs
are pointed at a local server that serves small fixture APKs.
"""
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

from fdroidkit import cli, publish as pub
from fdroidkit.add_app import AddAppOptions, add_app
from fdroidkit.checks import CheckFailed
from fdroidkit.publish import Options, PublishError
from fdroidkit.sources import github

FIX = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIX))
import make_fixtures as fx  # noqa: E402

BIN = FIX / "bin"
PKG = "io.oxisoft.oxitalk"
APK_NAME = "oxitalk-0.2.1-android.apk"
SELF = "f" * 64


class _Handler(http.server.SimpleHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        _Handler.seen.append((self.path, self.headers.get("Authorization")))
        super().do_GET()


@pytest.fixture
def gh(tmp_path, monkeypatch):
    """Fake api.github.com + release download host. Returns (root, base, put_release)."""
    root = tmp_path / "gh"
    root.mkdir()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("FDROIDKIT_GITHUB_API", base)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _Handler.seen = []

    def put_release(kind="self_signed", code=21, name="0.2.1", tag="v0.2.1", latest=True, assets=None):
        rel = json.loads((FIX / "github_release_oxitalk_v0.2.1.json").read_text())
        rel["tag_name"] = tag
        dl = root / "dl"
        dl.mkdir(exist_ok=True)
        apk = fx.make_apk(dl / APK_NAME, kind, PKG, code, name)
        for a in rel["assets"]:
            a["browser_download_url"] = f"{base}/dl/{a['name']}"
            if a["name"] == APK_NAME:
                a["size"] = apk.stat().st_size
        if assets is not None:
            rel["assets"] = [a for a in rel["assets"] if a["name"] in assets]
        d = root / "repos" / "oxisoft" / "oxitalk" / "releases"
        (d / "tags").mkdir(parents=True, exist_ok=True)
        if latest:
            (d / "latest").write_text(json.dumps(rel))
        (d / "tags" / tag).write_text(json.dumps(rel))
        return rel

    yield root, base, put_release
    srv.shutdown()


@pytest.fixture(autouse=True)
def self_signed_kind(monkeypatch):
    monkeypatch.setitem(fx.KINDS, "self_signed", {
        "signers": f"Signer #1 certificate DN: CN=OxiSoft, O=OxiSoft, C=PL\n"
                   f"Signer #1 certificate SHA-256 digest: {SELF}\n",
        "dex": {"classes.dex": b"dex\nLio/oxisoft/Main;"}})


def make_repo(tmp_path, meta=None, cfg=None):
    repo = tmp_path / "fdroid"
    (repo / "metadata").mkdir(parents=True)
    (repo / "repo").mkdir()
    (repo / "config.yml").write_text("repo_url: https://example.invalid/fdroid/repo\n")
    if meta is not None:
        (repo / "metadata" / f"{PKG}.yml").write_text(meta)
    if cfg is not None:
        (repo / "fdroidkit.json").write_text(json.dumps(cfg))
    return repo


def opts(repo, **kw):
    kw.setdefault("version_code", None)
    kw.setdefault("version_name", None)
    return Options(repo=repo, package=PKG, source="github-release", fdroid=str(BIN / "fdroid"),
                   build_tools=BIN, readback=False, deploy=False, **kw)


def test_pick_asset_glob_from_real_release():
    rel = json.loads((FIX / "github_release_oxitalk_v0.2.1.json").read_text())
    assert github.pick_asset(rel).name == APK_NAME
    assert github.pick_asset(rel, "*.flatpak").name.endswith(".flatpak")
    with pytest.raises(github.GitHubError, match="no asset matching"):
        github.pick_asset(rel, "*.aab")
    rel["assets"].append(dict(rel["assets"][0], name="oxitalk-0.2.1-arm64.apk"))
    with pytest.raises(github.GitHubError, match="2 assets"):
        github.pick_asset(rel)


def test_check_repo():
    assert github.check_repo("https://github.com/oxisoft/oxitalk/") == "oxisoft/oxitalk"
    with pytest.raises(github.GitHubError):
        github.check_repo("oxitalk")


def test_token_never_literal(monkeypatch):
    monkeypatch.setenv("MY_GH", "t0k")
    assert github.token_from({"token": {"env": "MY_GH"}}) == "t0k"
    with pytest.raises(github.GitHubError, match="never the token"):
        github.token_from({"token": "ghp_literal"})


def test_publish_latest_reads_version_from_apk(tmp_path, gh):
    _, _, put = gh
    put()
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20, name="0.2.0"))
    assert pub.publish(opts(repo, github_repo="oxisoft/oxitalk")) == 0
    assert (repo / "repo" / f"{PKG}_21.apk").exists()
    meta = (repo / "metadata" / f"{PKG}.yml").read_text()
    assert "CurrentVersionCode: 21" in meta and "CurrentVersion: 0.2.1" in meta
    assert any(p == "/repos/oxisoft/oxitalk/releases/latest" for p, _ in _Handler.seen)


def test_publish_source_from_fdroidkit_json_and_tag(tmp_path, gh):
    _, _, put = gh
    put(code=30, name="0.3.0", tag="v0.3.0", latest=False)
    cfg = {"apps": {PKG: {"source": {"type": "github-release", "repo": "oxisoft/oxitalk",
                                      "asset": "*-android.apk"}}}}
    repo = make_repo(tmp_path, fx.metadata(SELF, code=21, name="0.2.1"), cfg)
    assert pub.publish(opts(repo, tag="v0.3.0")) == 0
    assert (repo / "repo" / f"{PKG}_30.apk").exists()


def test_publish_refuses_duplicate_after_download(tmp_path, gh):
    _, _, put = gh
    put()
    repo = make_repo(tmp_path, fx.metadata(SELF, code=21, name="0.2.1"))
    with pytest.raises(CheckFailed, match="not higher than the published 21"):
        pub.publish(opts(repo, github_repo="oxisoft/oxitalk"))
    assert not list((repo / "repo").iterdir())


def test_publish_refuses_unpinned_signer(tmp_path, gh):
    _, _, put = gh
    put(kind="stranger")
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    with pytest.raises(CheckFailed, match="not pinned"):
        pub.publish(opts(repo, github_repo="oxisoft/oxitalk"))


def test_publish_refuses_pairip(tmp_path, gh, monkeypatch):
    monkeypatch.setitem(fx.KINDS, "self_pairip", {
        "signers": f"Signer #1 certificate SHA-256 digest: {SELF}\n",
        "dex": {"classes.dex": b"..Lcom/pairip/licensecheck/LicenseClient;"}})
    _, _, put = gh
    put(kind="self_pairip")
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    with pytest.raises(CheckFailed, match="pairip"):
        pub.publish(opts(repo, github_repo="oxisoft/oxitalk"))


def test_publish_explicit_version_must_match_apk(tmp_path, gh):
    _, _, put = gh
    put()
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    with pytest.raises(CheckFailed, match="versionCode='22'"):
        pub.publish(opts(repo, github_repo="oxisoft/oxitalk", version_code=22, version_name="0.2.2"))


def test_publish_no_apk_asset(tmp_path, gh):
    _, _, put = gh
    put(assets=["SHA256SUMS"])
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    with pytest.raises(github.GitHubError, match="no asset matching"):
        pub.publish(opts(repo, github_repo="oxisoft/oxitalk"))


def test_publish_missing_repo_and_play_needs_version(tmp_path, gh):
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    with pytest.raises(PublishError, match="--github-repo"):
        pub.publish(opts(repo))
    with pytest.raises(PublishError, match="needs version_code"):
        pub.publish(Options(repo=repo, package=PKG, version_code=None, version_name=None,
                            source="file", build_tools=BIN))


def test_token_sent_when_set(tmp_path, gh, monkeypatch):
    _, _, put = gh
    put()
    monkeypatch.setenv("GITHUB_TOKEN", "secret-t")
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    rel = github.release("oxisoft/oxitalk", token=github.token_from(None))
    assert rel["tag_name"] == "v0.2.1"
    assert _Handler.seen[-1][1] == "Bearer secret-t"


def test_add_app_from_github_pins_self_signer_and_saves_source(tmp_path, gh):
    _, _, put = gh
    put()
    repo = make_repo(tmp_path)
    out = []
    meta_file = add_app(AddAppOptions(repo=repo, package=PKG, from_github="oxisoft/oxitalk",
                                      asset="*-android.apk", build_tools=BIN), log=out.append)
    meta = meta_file.read_text()
    assert f"  - {SELF}" in meta and "by --from-github" in meta
    cfg = json.loads((repo / "fdroidkit.json").read_text())
    assert cfg["apps"][PKG]["source"] == {"type": "github-release", "repo": "oxisoft/oxitalk",
                                          "asset": "*-android.apk"}
    assert any("--source github-release" in line for line in out)
    # and the saved source drives publish with no other flags
    assert pub.publish(opts(repo)) == 0
    assert (repo / "repo" / f"{PKG}_21.apk").exists()


def test_add_app_from_github_wrong_package(tmp_path, gh):
    _, _, put = gh
    put()
    repo = make_repo(tmp_path)
    with pytest.raises(CheckFailed, match="identity"):
        add_app(AddAppOptions(repo=repo, package="com.other.app", from_github="oxisoft/oxitalk",
                              build_tools=BIN), log=lambda *_: None)


def test_cli_publish_github_release_dry_run(tmp_path, gh, capsys):
    _, _, put = gh
    put()
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    rc = cli.main(["--repo", str(repo), "publish", PKG, "--source", "github-release",
                   "--github-repo", "oxisoft/oxitalk", "--build-tools", str(BIN), "--dry-run"])
    assert rc == 0
    assert "version from the APK: 0.2.1 (21)" in capsys.readouterr().out


def test_cli_publish_github_missing_release(tmp_path, gh, capsys):
    repo = make_repo(tmp_path, fx.metadata(SELF, code=20))
    rc = cli.main(["--repo", str(repo), "publish", PKG, "--source", "github-release",
                   "--github-repo", "oxisoft/nothing", "--build-tools", str(BIN), "--dry-run"])
    assert rc == 3
    assert "has no published" in capsys.readouterr().err
