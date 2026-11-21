import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from fdroidkit import cli
from fdroidkit.add_app import AddAppError, AddAppOptions, add_app, merge_pins, parse_signer_certs
from fdroidkit.checks import CheckFailed, parse_pins
from fdroidkit.init import InitError, InitOptions, init, is_png, placeholder_png, render_config
from fdroidkit.publish import Options, PublishError, missing_env, publish

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import make_fixtures as fx  # noqa: E402

BIN = Path(__file__).parent / "fixtures" / "bin"
PKG = "com.example.app"
needs_keytool = pytest.mark.skipif(not shutil.which("keytool"), reason="keytool (JDK) not installed")
SECRET = "correct-horse-battery-staple"


# ---- init ------------------------------------------------------------------

def test_config_holds_env_refs_not_secrets(tmp_path):
    o = InitOptions(repo=tmp_path, name="Example", url="https://f.example.com/fdroid/repo",
                    pass_env="MY_PASS", sdk_path="/opt/sdk")
    cfg = render_config(o, Path("/keys/x.p12"), "f.example.com", "CN=f.example.com, OU=F-Droid")
    assert "keystorepass: {env: MY_PASS}" in cfg and "keypass: {env: MY_PASS}" in cfg
    assert 'archive_url: "https://f.example.com/fdroid/archive"' in cfg
    assert "repo_icon: icon.png" in cfg and "archive_icon: icon.png" in cfg
    assert 'sdk_path: "/opt/sdk"' in cfg


def test_placeholder_icon_is_a_png():
    data = placeholder_png(16)
    assert is_png(data) and len(data) > 50


def test_init_without_passphrase_env_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_PASS", raising=False)
    with pytest.raises(InitError, match=r"\$NOPE_PASS is not set"):
        init(InitOptions(repo=tmp_path / "r", name="X", url="https://x/fdroid/repo", pass_env="NOPE_PASS"))
    assert not (tmp_path / "r").exists()


def test_init_rejects_bad_env_name(tmp_path):
    with pytest.raises(InitError, match="not a valid"):
        init(InitOptions(repo=tmp_path, name="X", url="https://x/fdroid/repo", pass_env="A B"))


def test_init_refuses_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_PASS", SECRET)
    (tmp_path / "config.yml").write_text("repo_name: old\n")
    with pytest.raises(InitError, match="refusing to re-init"):
        init(InitOptions(repo=tmp_path, name="X", url="https://x/fdroid/repo", pass_env="FK_PASS"))
    assert (tmp_path / "config.yml").read_text() == "repo_name: old\n"


@needs_keytool
def test_init_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FK_PASS", SECRET)
    repo, ks = tmp_path / "repo", tmp_path / "keys" / "index.p12"
    rc = cli.main(["--repo", str(repo), "init", "--name", "Example", "--url",
                   "https://f.example.com/fdroid/repo", "--pass-env", "FK_PASS", "--keystore", str(ks)])
    assert rc == 0
    out = capsys.readouterr().out
    cfg = (repo / "config.yml").read_text()
    assert SECRET not in cfg and SECRET not in out
    assert "keystorepass: {env: FK_PASS}" in cfg
    assert f'keystore: "{ks}"' in cfg and 'repo_keyalias: "f.example.com"' in cfg
    assert stat.S_IMODE(ks.stat().st_mode) == 0o600
    for d in ("metadata", "repo", "archive"):
        assert (repo / d).is_dir()
    for icon in ("icon.png", "repo/icons/icon.png", "archive/icons/icon.png"):
        assert is_png((repo / icon).read_bytes())
        assert stat.S_IMODE((repo / icon).stat().st_mode) == 0o644   # public files, not 0600
    assert "*.p12" in (repo / ".gitignore").read_text()
    fp = next(ln.split()[1] for ln in out.splitlines() if ln.startswith("fingerprint "))
    der = subprocess.run(["keytool", "-exportcert", "-keystore", str(ks), "-alias", "f.example.com",
                          "-storepass:env", "FK_PASS"], capture_output=True, check=True).stdout
    import hashlib
    assert fp == hashlib.sha256(der).hexdigest()
    assert "BACK UP" in out
    # a second init would replace the key: refused, key unchanged
    assert cli.main(["--repo", str(repo), "init", "--name", "X", "--url", "https://x/fdroid/repo",
                     "--pass-env", "FK_PASS", "--keystore", str(ks)]) == 3
    assert ks.read_bytes() and "refusing" in capsys.readouterr().err


@needs_keytool
def test_init_refuses_existing_keystore(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_PASS", SECRET)
    ks = tmp_path / "k.p12"
    ks.write_bytes(b"precious")
    with pytest.raises(InitError, match="refusing to overwrite"):
        init(InitOptions(repo=tmp_path / "r", name="X", url="https://x/fdroid/repo",
                         pass_env="FK_PASS", keystore=ks), log=lambda *_: None)
    assert ks.read_bytes() == b"precious"


def test_publish_refuses_unset_env_refs(tmp_path, monkeypatch):
    monkeypatch.delenv("FK_UNSET", raising=False)
    repo = tmp_path / "r"
    (repo / "metadata").mkdir(parents=True)
    (repo / "config.yml").write_text("keystorepass: {env: FK_UNSET}\nkeypass: {env: FK_UNSET}\n")
    (repo / "metadata" / f"{PKG}.yml").write_text(fx.metadata(fx.PLAY))
    assert missing_env(repo) == ["keystorepass ($FK_UNSET)", "keypass ($FK_UNSET)"]
    apk = fx.make_apk(tmp_path / "in.apk", "one_key", PKG, 13, "1.3.0")
    o = Options(repo=repo, package=PKG, version_code=13, version_name="1.3.0", source="file", apk=apk,
                fdroid=str(BIN / "fdroid"), build_tools=BIN, readback=False)
    with pytest.raises(PublishError, match="unset environment"):
        publish(o)
    monkeypatch.setenv("FK_UNSET", "x")
    assert missing_env(repo) == []


# ---- add-app ---------------------------------------------------------------

def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "metadata").mkdir(parents=True)
    (repo / "config.yml").write_text("repo_name: t\n")
    return repo


def _opts(repo, **kw):
    kw.setdefault("build_tools", BIN)
    return AddAppOptions(repo=repo, package=PKG, **kw)


def test_parse_signer_certs_skips_source_stamp():
    certs = parse_signer_certs(fx.KINDS["play"]["signers"])
    assert certs == [(fx.GOOGLE_DN, fx.PLAY)]
    assert [d for _, d in parse_signer_certs(fx.KINDS["play_rotated"]["signers"])] == [fx.PLAY, fx.PLAY_NEW]


def test_discover_play_signer_writes_pinned_metadata(tmp_path):
    repo = _repo(tmp_path)
    apk = fx.make_apk(tmp_path / "u.apk", "play", PKG, 13, "1.3.0")
    meta = add_app(_opts(repo, discover_apk=apk, name="Example"), log=lambda *_: None)
    text = meta.read_text()
    assert parse_pins(text) == [fx.PLAY]
    assert "Name: Example" in text and "O=Google Inc." in text
    assert (repo / "metadata" / PKG / "en-US" / "phoneScreenshots").is_dir()
    assert (repo / "metadata" / PKG / "en-US" / "full_description.txt").exists()


def test_discover_rotated_play_key_pins_both(tmp_path):
    repo = _repo(tmp_path)
    apk = fx.make_apk(tmp_path / "u.apk", "play_rotated", PKG, 13, "1.3.0")
    meta = add_app(_opts(repo, discover_apk=apk), log=lambda *_: None)
    assert parse_pins(meta.read_text()) == [fx.PLAY, fx.PLAY_NEW]


def test_upload_key_is_refused_unless_not_play(tmp_path):
    repo = _repo(tmp_path)
    apk = fx.make_apk(tmp_path / "u.apk", "upload_key", PKG, 13, "1.3.0")
    with pytest.raises(CheckFailed, match="not Play App Signing"):
        add_app(_opts(repo, discover_apk=apk), log=lambda *_: None)
    assert not (repo / "metadata" / f"{PKG}.yml").exists()
    meta = add_app(_opts(repo, discover_apk=apk, not_play=True), log=lambda *_: None)
    assert parse_pins(meta.read_text()) == [fx.STRANGER]
    assert "Self-signed" in meta.read_text()


def test_wrong_package_is_refused(tmp_path):
    repo = _repo(tmp_path)
    apk = fx.make_apk(tmp_path / "u.apk", "play", "com.other.app", 13, "1.3.0")
    with pytest.raises(CheckFailed, match="is com.other.app"):
        add_app(_opts(repo, discover_apk=apk), log=lambda *_: None)


def test_pairip_apk_is_refused_at_discovery(tmp_path):
    repo = _repo(tmp_path)
    apk = fx.make_apk(tmp_path / "u.apk", "play_pairip", PKG, 13, "1.3.0")
    with pytest.raises(CheckFailed, match="pairip"):
        add_app(_opts(repo, discover_apk=apk), log=lambda *_: None)


def test_manual_pins_and_rotation_merge(tmp_path):
    repo = _repo(tmp_path)
    meta = add_app(_opts(repo, pins=[fx.PLAY.upper()]), log=lambda *_: None)
    meta.write_text(meta.read_text() + "CurrentVersion: 1.0\nCurrentVersionCode: 3\n")
    add_app(_opts(repo, pins=[fx.PLAY, fx.PLAY_NEW]), log=lambda *_: None)
    text = meta.read_text()
    assert parse_pins(text) == [fx.PLAY, fx.PLAY_NEW]
    assert "CurrentVersionCode: 3" in text   # other fields untouched


def test_merge_pins_appends_block_when_missing():
    text, added = merge_pins("Name: X\n", [fx.PLAY])
    assert added == [fx.PLAY] and parse_pins(text) == [fx.PLAY]


@pytest.mark.parametrize("kw, msg", [
    ({}, "nothing to pin"),
    ({"pins": ["abc"]}, "64-hex"),
])
def test_add_app_setup_errors(tmp_path, kw, msg):
    with pytest.raises(AddAppError, match=msg):
        add_app(_opts(_repo(tmp_path), **kw), log=lambda *_: None)


def test_add_app_needs_init_and_valid_package(tmp_path):
    with pytest.raises(AddAppError, match="fdroidkit init"):
        add_app(_opts(tmp_path, pins=[fx.PLAY]), log=lambda *_: None)
    with pytest.raises(AddAppError, match="application id"):
        add_app(AddAppOptions(repo=_repo(tmp_path), package="nodots", pins=[fx.PLAY]), log=lambda *_: None)


def test_add_app_cli_exit_codes(tmp_path, capsys):
    repo = _repo(tmp_path)
    up = fx.make_apk(tmp_path / "u.apk", "upload_key", PKG, 13, "1.3.0")
    base = ["--repo", str(repo), "add-app", PKG, "--build-tools", str(BIN)]
    assert cli.main([*base, "--discover-signer", str(up)]) == 1
    assert "refused" in capsys.readouterr().err
    assert cli.main([*base]) == 3
    ok = fx.make_apk(tmp_path / "p.apk", "play", PKG, 13, "1.3.0")
    assert cli.main([*base, "--discover-signer", str(ok)]) == 0


# ---- add-app --locale (FK-19) ----------------------------------------------

def test_add_app_scaffolds_extra_locales(tmp_path):
    repo = _repo(tmp_path)
    add_app(_opts(repo, pins=[fx.PLAY], locales=["pl", "pt-BR"]), log=lambda *_: None)
    for loc in ("en-US", "pl", "pt-BR"):
        d = repo / "metadata" / PKG / loc
        assert (d / "short_description.txt").exists() and (d / "full_description.txt").exists()


def test_add_app_locale_alone_on_existing_app_keeps_texts(tmp_path):
    repo = _repo(tmp_path)
    meta = add_app(_opts(repo, pins=[fx.PLAY]), log=lambda *_: None)
    before = meta.read_text()
    (repo / "metadata" / PKG / "en-US" / "short_description.txt").write_text("Hello")
    add_app(_opts(repo, locales=["de"]), log=lambda *_: None)
    assert (repo / "metadata" / PKG / "de" / "short_description.txt").read_text() == ""
    assert (repo / "metadata" / PKG / "en-US" / "short_description.txt").read_text() == "Hello"
    assert meta.read_text() == before


def test_add_app_locale_needs_existing_app_or_pins(tmp_path):
    with pytest.raises(AddAppError, match="--locale alone works"):
        add_app(_opts(_repo(tmp_path), locales=["pl"]), log=lambda *_: None)


@pytest.mark.parametrize("bad", ["polish", "PL", "pl_PL", "../x", ""])
def test_add_app_rejects_bad_locale(tmp_path, bad):
    with pytest.raises(AddAppError, match="--locale"):
        add_app(_opts(_repo(tmp_path), pins=[fx.PLAY], locales=[bad]), log=lambda *_: None)


def test_add_app_cli_locale(tmp_path, capsys):
    repo = _repo(tmp_path)
    base = ["--repo", str(repo), "add-app", PKG, "--build-tools", str(BIN)]
    assert cli.main([*base, "--pin", fx.PLAY, "--locale", "pl", "--locale", "ru"]) == 0
    assert (repo / "metadata" / PKG / "ru").is_dir()
    assert "listing (pl)" in capsys.readouterr().out
