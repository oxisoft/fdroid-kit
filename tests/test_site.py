import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

import pytest

from fdroidkit import cli
from fdroidkit.site import keys, render as rnd, verify as vfy

FP = "D1902661481C51BEE42B17080BC9D452B87E6754362CC17AF152975AD5A639F5"
OTHER = "AA" * 32


def keytool_out(fp):
    colon = ":".join(fp[i:i + 2] for i in range(0, 64, 2))
    return f"Signer #1:\n\nCertificate #1:\nOwner: CN=example\n\t SHA1: 00\n\t SHA256: {colon}\n"


def fake_runner(fp=FP):
    calls = []

    def run(cmd):
        calls.append(cmd)
        assert cmd[0] == "keytool" and "-jarfile" in cmd
        return keytool_out(fp)
    run.calls = calls
    return run


def png(path: Path):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    raw = b"\x00\xff\xff\xff\xff"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_repo(tmp_path, apps=None, tamper=False, brand=None):
    repo = tmp_path / "fdroid"
    rd = repo / "repo"
    (rd / "icons").mkdir(parents=True)
    png(rd / "icons" / "repo.png")
    apps = apps if apps is not None else {
        "com.example.zeta": ("Zeta </script> app", "Does <b>things</b>", 42, "1.4.0"),
        "com.example.alpha": ("Alpha", "First app", 7, "0.7"),
    }
    packages = {}
    for app_id, (name, summary, code, vname) in apps.items():
        icon = f"/{app_id}/en-US/icon.png"
        (rd / app_id / "en-US").mkdir(parents=True)
        png(rd / app_id / "en-US" / "icon.png")
        packages[app_id] = {
            "metadata": {"name": {"en-US": name}, "summary": {"en-US": summary},
                         "description": {"en-US": "Line one\nwrapped.\n\nSecond \"para\"."},
                         "icon": {"en-US": {"name": icon}}, "categories": ["Games"],
                         "webSite": f"https://{app_id}.example"},
            "versions": {
                "a": {"file": {"name": f"/{app_id}_{code}.apk", "size": 3 * 1048576},
                      "manifest": {"versionCode": code, "versionName": vname}},
                "b": {"file": {"name": f"/{app_id}_1.apk", "size": 1048576},
                      "manifest": {"versionCode": 1, "versionName": "0.0.1"}},
            },
        }
    index = {"repo": {"name": {"en-US": "Example Apps"},
                      "description": {"en-US": "Apps by\nExample."},
                      "icon": {"en-US": {"name": "/icons/repo.png"}},
                      "address": "https://fdroid.example.com/fdroid/repo", "timestamp": 1795009296000},
             "packages": packages}
    raw = json.dumps(index).encode()
    (rd / "index-v2.json").write_bytes(raw)
    sha = hashlib.sha256(raw if not tamper else raw + b" ").hexdigest()
    (rd / "entry.json").write_text(json.dumps({"index": {"name": "/index-v2.json", "sha256": sha}}))
    (rd / "entry.jar").write_bytes(b"PK fake")
    if brand is not None:
        (repo / "fdroidkit.json").write_text(json.dumps({"site": brand}))
    return repo


def test_parse_sha256_rejects_two_signers():
    assert keys.parse_sha256(keytool_out(FP)) == FP
    with pytest.raises(keys.KeyReadError, match="2 signers"):
        keys.parse_sha256(keytool_out(FP) + keytool_out(OTHER))
    with pytest.raises(keys.KeyReadError):
        keys.parse_sha256("nothing here")


def test_jar_fingerprint_falls_back_to_index_v1(tmp_path):
    (tmp_path / "index-v1.jar").write_bytes(b"PK")
    run = fake_runner()
    assert keys.jar_fingerprint(tmp_path / "entry.jar", runner=run) == FP
    assert run.calls[0][-1].endswith("index-v1.jar")
    with pytest.raises(keys.KeyReadError, match="fdroid update"):
        keys.jar_fingerprint(tmp_path / "nope" / "entry.jar", runner=run)


def test_render_lists_only_signed_index_apps(tmp_path):
    repo = make_repo(tmp_path)
    res = rnd.render(repo, Path("site"), runner=fake_runner())
    page = (repo / "site" / "index.html").read_text()
    assert res.apps == ["Alpha", "Zeta </script> app"]  # sorted by name
    assert "2 apps in the repository" in page
    assert page.index("Alpha") < page.index("Zeta")
    # newest version only, sizes and download links from the index
    assert "1.4.0 &middot; 3.0 MB" in page and "0.0.1" not in page
    assert 'href="fdroid/repo/com.example.zeta_42.apk"' in page
    # escaping: summary HTML and a script-closing name stay inert
    assert "<b>things</b>" not in page and "&lt;b&gt;things&lt;/b&gt;" in page
    assert "Zeta </script>" not in page
    # descriptions unwrapped into paragraphs
    assert "<p>Line one wrapped.</p><p>Second &quot;para&quot;.</p>" in page
    # strict CSP: no inline executable script or style
    assert not re.search(r"<script(?![^>]*application/(ld\+)?json)", page)
    assert "<style" not in page and " style=" not in page and "onclick" not in page
    assert (repo / "site" / "appicons" / "com.example.alpha.png").exists()
    assert (repo / "site" / "icon.png").exists()


def test_fingerprint_and_qr_come_from_the_jar(tmp_path):
    repo = make_repo(tmp_path)
    rnd.render(repo, Path("site"), runner=fake_runner())
    site = repo / "site"
    page = (site / "index.html").read_text()
    assert f"https://fdroid.example.com/fdroid/repo?fingerprint={FP}" in page
    assert rnd.group_fp(FP) in page
    assert FP in (site / "llms.txt").read_text()
    assert (site / "qr.svg").read_text().startswith("<")
    rep = vfy.verify(repo, Path("site"), runner=fake_runner())
    assert rep.ok and rep.qr == "ok" and rep.values == {FP: True}


def test_tampered_index_is_refused(tmp_path):
    repo = make_repo(tmp_path, tamper=True)
    with pytest.raises(rnd.SiteError, match="not the signed one"):
        rnd.render(repo, Path("site"), runner=fake_runner())


def test_unsigned_index_is_refused(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "repo" / "entry.json").unlink()
    with pytest.raises(rnd.SiteError, match="unsigned"):
        rnd.render(repo, Path("site"), runner=fake_runner())


def test_brand_config(tmp_path):
    repo = make_repo(tmp_path, brand={
        "title": "Acme Store", "accent": "#FF2A3D", "org_name": "Acme", "org_url": "https://acme.example",
        "site_url": "https://apps.acme.example/",
        "footer_links": [{"label": "Privacy", "url": "https://acme.example/privacy"}],
        "badges": {"com.example.alpha": {"category": "Puzzle", "tags": ["offline", ""]}},
        "unknown_key": "ignored"})
    rnd.render(repo, Path("site"), runner=fake_runner())
    site = repo / "site"
    page = (site / "index.html").read_text()
    assert "<title>Acme Store</title>" in page
    assert '<link rel="canonical" href="https://apps.acme.example/">' in page
    assert '<span class="badge cat">Puzzle</span><span class="badge">offline</span>' in page
    assert 'href="https://acme.example/privacy">Privacy</a>' in page
    assert "--accent: #FF2A3D;" in (site / "site.css").read_text()
    ld = json.loads(re.search(r'application/ld\+json">(.*?)</script>', page, re.S).group(1))
    org = ld["@graph"][0]
    assert org["@type"] == "Organization" and org["name"] == "Acme"
    app = ld["@graph"][2]["itemListElement"][0]["item"]
    assert app["downloadUrl"] == "https://apps.acme.example/fdroid/repo/com.example.alpha_7.apk"
    assert app["publisher"] == {"@id": org["@id"]}
    assert "https://apps.acme.example/sitemap.xml" in (site / "robots.txt").read_text()


def test_bad_accent_is_refused(tmp_path):
    repo = make_repo(tmp_path, brand={"accent": "red;}body{display:none"})
    with pytest.raises(rnd.SiteError, match="hex colour"):
        rnd.render(repo, Path("site"), runner=fake_runner())


def test_verify_catches_a_stale_fingerprint(tmp_path):
    repo = make_repo(tmp_path)
    rnd.render(repo, Path("site"), runner=fake_runner())
    llms = repo / "site" / "llms.txt"
    llms.write_text(llms.read_text().replace(FP, OTHER))
    rep = vfy.verify(repo, Path("site"), runner=fake_runner())
    assert not rep.ok and rep.values == {FP: True, OTHER: False}
    assert "MISMATCH, fix before deploying" in "\n".join(rep.lines())


def test_verify_catches_a_stale_qr(tmp_path):
    repo = make_repo(tmp_path)
    rnd.render(repo, Path("site"), runner=fake_runner())
    (repo / "site" / "qr.svg").write_text(rnd.qr_svg("https://evil.example/repo?fingerprint=" + FP))
    rep = vfy.verify(repo, Path("site"), runner=fake_runner())
    assert rep.qr == "mismatch" and not rep.ok


def test_verify_empty_site_fails(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "site").mkdir()
    rep = vfy.verify(repo, Path("site"), runner=fake_runner())
    assert not rep.ok and "NO fingerprint" in rep.lines()[-1]


def test_cli_site_and_verify(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(keys, "_keytool", fake_runner())
    assert cli.main(["--repo", str(repo), "site"]) == 0
    assert "rendered 2 app(s)" in capsys.readouterr().out
    assert cli.main(["--repo", str(repo), "verify"]) == 0
    assert "verdict: all match" in capsys.readouterr().out
    monkeypatch.setattr(keys, "_keytool", fake_runner(OTHER))
    assert cli.main(["--repo", str(repo), "verify"]) == 1
    (repo / "repo" / "entry.json").unlink()
    assert cli.main(["--repo", str(repo), "site"]) == 3


def test_cli_site_with_a_relative_repo(tmp_path, monkeypatch, capsys):
    # `--repo fdroid` (as in CI): render() returns fdroid/site, verify() must not
    # prefix the repo a second time (it scanned fdroid/fdroid/site and found nothing).
    repo = make_repo(tmp_path)
    monkeypatch.setattr(keys, "_keytool", fake_runner())
    monkeypatch.chdir(repo.parent)
    assert cli.main(["--repo", repo.name, "site"]) == 0
    assert "verdict: all match" in capsys.readouterr().out


def test_qr_check_ignores_svg_serialisation():
    from fdroidkit.site import verify as v
    a = '<svg><rect x="0" /><path d="M2,2H3V3H2z" /></svg>'
    b = '<svg><rect x="0"/><path d="M2,2H3V3H2z"/></svg>'
    assert v._qr_modules(a) == v._qr_modules(b) == ["M2,2H3V3H2z"]
    assert v._qr_modules(a) != v._qr_modules('<svg><path d="M2,2H4V3H2z"/></svg>')


def _obtainium_config(href):
    import urllib.parse
    assert href.startswith("obtainium://app/")
    return json.loads(urllib.parse.unquote(href[len("obtainium://app/"):]))


def test_obtainium_link_per_app(tmp_path):
    import html as h
    repo = make_repo(tmp_path, brand={"org_name": "Acme"})
    rnd.render(repo, Path("site"), runner=fake_runner())
    page = (repo / "site" / "index.html").read_text()
    links = [h.unescape(x) for x in re.findall(r'class="applink alt" href="([^"]+)"', page)]
    assert len(links) == 2
    cfg = _obtainium_config(links[0])  # Alpha sorts first
    assert cfg["id"] == "com.example.alpha" and cfg["name"] == "Alpha" and cfg["author"] == "Acme"
    assert cfg["url"] == "https://fdroid.example.com/fdroid/repo?appId=com.example.alpha"
    assert cfg["overrideSource"] == "FDroidRepo"
    assert json.loads(cfg["additionalSettings"]) == {"appIdOrName": "com.example.alpha"}
    # the JSON is fully encoded: no raw ?, / or quotes that would split the deep link
    assert re.fullmatch(r"obtainium://app/[A-Za-z0-9%._~-]+", links[0])
    assert 'class="alt-client"' in page and "Add to Obtainium" in page
    assert " style=" not in page and "onclick" not in page


def test_obtainium_redirect_and_opt_out(tmp_path):
    import html as h
    import urllib.parse
    repo = make_repo(tmp_path, brand={"obtainium": "redirect"})
    rnd.render(repo, Path("site"), runner=fake_runner())
    page = (repo / "site" / "index.html").read_text()
    href = h.unescape(re.search(r'class="applink alt" href="([^"]+)"', page).group(1))
    assert href.startswith(rnd.OBTAINIUM_REDIRECT)
    inner = urllib.parse.unquote(href[len(rnd.OBTAINIUM_REDIRECT):])
    assert _obtainium_config(inner)["id"] == "com.example.alpha"

    repo2 = make_repo(tmp_path / "off", brand={"obtainium": False})
    rnd.render(repo2, Path("site"), runner=fake_runner())
    page2 = (repo2 / "site" / "index.html").read_text()
    assert "Obtainium" not in page2 and "$obtainium_note" not in page2
    assert vfy.verify(repo2, Path("site"), runner=fake_runner()).ok


def test_bad_obtainium_value_is_refused(tmp_path):
    repo = make_repo(tmp_path, brand={"obtainium": "yes please"})
    with pytest.raises(rnd.SiteError, match="site.obtainium"):
        rnd.render(repo, Path("site"), runner=fake_runner())


# ---- localized listings (FK-19) --------------------------------------------

def localize(repo, by_app):
    """Add locales to the signed index: {app_id: {locale: {field: text}}}, re-signing entry.json."""
    rd = repo / "repo"
    data = json.loads((rd / "index-v2.json").read_text())
    for app_id, locs in by_app.items():
        md = data["packages"][app_id]["metadata"]
        for loc, fields in locs.items():
            for k, v in fields.items():
                md[k][loc] = v
    raw = json.dumps(data).encode()
    (rd / "index-v2.json").write_bytes(raw)
    (rd / "entry.json").write_text(json.dumps({"index": {"name": "/index-v2.json",
                                                          "sha256": hashlib.sha256(raw).hexdigest()}}))


def test_pick_falls_back_per_field():
    d = {"en-US": "en", "pl": "pl", "pt-BR": "br"}
    assert rnd.pick(d, "pl") == "pl"
    assert rnd.pick(d, "pl-PL") == "pl"          # same language, other region
    assert rnd.pick(d, "pt") == "br"
    assert rnd.pick(d, "de") == "en"             # missing: en-US
    assert rnd.pick({"fr": "fr"}, "de") == "fr"  # no en-US: whatever exists
    assert rnd.pick({"en-US": "en", "pl": ""}, "pl") == "en"  # empty text counts as missing
    assert rnd.pick("plain", "pl") == "plain"


def test_default_is_one_english_page(tmp_path):
    repo = make_repo(tmp_path)
    res = rnd.render(repo, Path("site"), runner=fake_runner())
    page = (repo / "site" / "index.html").read_text()
    assert res.pages == ["index.html"]
    assert '<html lang="en-US">' in page and "hreflang" not in page and 'class="langs"' not in page
    assert 'href="site.css"' in page and 'href="fdroid/repo/com.example.zeta_42.apk"' in page
    assert not (repo / "site" / "pl").exists()


def test_extra_locale_page_with_fallback(tmp_path):
    repo = make_repo(tmp_path, brand={"locales": ["en-US", "pl"]})
    localize(repo, {"com.example.alpha": {"pl": {"summary": "Pierwsza aplikacja",
                                                 "description": "Opis\npo polsku."}}})
    logs = []
    res = rnd.render(repo, Path("site"), runner=fake_runner(), log=logs.append)
    site = repo / "site"
    en, pl = (site / "index.html").read_text(), (site / "pl" / "index.html").read_text()
    assert res.pages == ["index.html", "pl/index.html"]
    assert "First app" in en and "Pierwsza aplikacja" not in en
    assert '<html lang="pl">' in pl
    assert "Pierwsza aplikacja" in pl and "<p>Opis po polsku.</p>" in pl
    assert "Does &lt;b&gt;things&lt;/b&gt;" in pl        # zeta has no pl: en-US summary
    assert "<p>Line one wrapped.</p>" in pl              # and en-US description
    # assets stay at the root, linked relative from the subpage
    for ref in ('href="../site.css"', 'src="../qr.svg"', 'src="../appicons/com.example.alpha.png"',
                'href="../fdroid/repo/com.example.zeta_42.apk"', 'src="../icon.png"'):
        assert ref in pl, ref
    assert not (site / "pl" / "appicons").exists() and not (site / "pl" / "site.css").exists()
    # hreflang both ways, x-default, canonical per page, switcher without script
    for page in (en, pl):
        assert '<link rel="alternate" hreflang="en-US" href="https://fdroid.example.com/">' in page
        assert '<link rel="alternate" hreflang="pl" href="https://fdroid.example.com/pl/">' in page
        assert 'hreflang="x-default" href="https://fdroid.example.com/"' in page
        assert '<nav class="langs"' in page
        assert not re.search(r"<script(?![^>]*application/(ld\+)?json)", page) and " style=" not in page
    assert '<link rel="canonical" href="https://fdroid.example.com/pl/">' in pl
    assert 'href="https://fdroid.example.com/pl/" hreflang="pl" lang="pl" aria-current="page"' in pl
    ld = json.loads(re.search(r'application/ld\+json">(.*?)</script>', pl, re.S).group(1))
    site_node = next(n for n in ld["@graph"] if n["@type"] == "WebSite")
    assert site_node["inLanguage"] == "pl"
    sm = (site / "sitemap.xml").read_text()
    assert "<loc>https://fdroid.example.com/</loc>" in sm and "<loc>https://fdroid.example.com/pl/</loc>" in sm
    assert not logs or not any("no listing in pl" in x for x in logs)
    # fingerprints on every page are checked, QR too
    rep = vfy.verify(repo, Path("site"), runner=fake_runner())
    assert rep.ok and rep.qr == "ok" and "pl/index.html" in rep.files


def test_locale_missing_from_index_warns_and_falls_back(tmp_path):
    repo = make_repo(tmp_path, brand={"locales": ["en-US", "de"]})
    logs = []
    rnd.render(repo, Path("site"), runner=fake_runner(), log=logs.append)
    de = (repo / "site" / "de" / "index.html").read_text()
    assert "First app" in de and '<html lang="de">' in de
    assert any("no listing in de" in x for x in logs)


def test_non_english_root_locale(tmp_path):
    repo = make_repo(tmp_path, brand={"locales": ["pl", "en-US"]})
    localize(repo, {"com.example.alpha": {"pl": {"summary": "Pierwsza"}}})
    rnd.render(repo, Path("site"), runner=fake_runner())
    root = (repo / "site" / "index.html").read_text()
    assert '<html lang="pl">' in root and "Pierwsza" in root
    assert "First app" in (repo / "site" / "en-us" / "index.html").read_text()


@pytest.mark.parametrize("bad", [[], ["en-US", "en-us"], ["english"], "x y", [3]])
def test_bad_locales_are_refused(tmp_path, bad):
    repo = make_repo(tmp_path, brand={"locales": bad})
    with pytest.raises(rnd.SiteError, match="site.locales"):
        rnd.render(repo, Path("site"), runner=fake_runner())


def _ld_apps(page):
    ld = json.loads(re.search(r'application/ld\+json">(.*?)</script>', page, re.S).group(1))
    items = next(g for g in ld["@graph"] if g["@type"] == "ItemList")["itemListElement"]
    return {i["item"]["name"]: i["item"] for i in items}


def test_title_badge_and_keywords(tmp_path):
    repo = make_repo(tmp_path, brand={"accent": "#2F6FEB", "badges": {
        "com.example.alpha": {"title_badge": {"text": "BETA <x>", "color": "#FF6A00"},
                              "tags": ["offline", "no ads"]},
        "com.example.zeta": {"title_badge": "ALPHA"},  # text only: accent colour
    }})
    rnd.render(repo, Path("site"), runner=fake_runner())
    page = (repo / "site" / "index.html").read_text()
    css = (repo / "site" / "site.css").read_text()
    assert '<strong>Alpha <span class="tbadge tb-ff6a00">BETA &lt;x&gt;</span></strong>' in page
    assert '<span class="tbadge tb-2f6feb">ALPHA</span></strong>' in page
    assert " style=" not in page and "<style" not in page  # strict CSP: colours live in site.css
    assert ".tbadge.tb-ff6a00 { background: #ff6a00; border-color: #ff6a00; color: #0a0a0a; }" in css
    assert ".tbadge.tb-2f6feb { background: #2f6feb; border-color: #2f6feb; color: #ffffff; }" in css
    apps = _ld_apps(page)
    assert apps["Alpha"]["keywords"] == "offline, no ads"
    assert "keywords" not in apps["Zeta </script> app"]


@pytest.mark.parametrize("color,fg", [("#FFF", "#0a0a0a"), ("#000000", "#ffffff"), ("#FFC400", "#0a0a0a"),
                                      ("#FF2A3D", "#0a0a0a"), ("#1A1A2E", "#ffffff"), ("#2F6FEB", "#ffffff")])
def test_badge_text_contrast(color, fg):
    c = rnd._hex6(color)
    assert rnd.badge_text_color(c) == fg
    lum = rnd._luminance(c)
    other = rnd.LIGHT_TEXT if fg == rnd.DARK_TEXT else rnd.DARK_TEXT
    ratio = lambda a, b: (max(a, b) + 0.05) / (min(a, b) + 0.05)
    assert ratio(lum, rnd._luminance(fg)) >= ratio(lum, rnd._luminance(other))


@pytest.mark.parametrize("bad", ["orange", "#FF6A0", "#FF6A00CC", "#GGGGGG", "#fff;}body{display:none"])
def test_bad_title_badge_color_is_refused(tmp_path, bad):
    repo = make_repo(tmp_path, brand={"badges": {"com.example.alpha": {"title_badge": {"text": "BETA", "color": bad}}}})
    with pytest.raises(rnd.SiteError, match=r"com.example.alpha: title_badge color must be #RGB or #RRGGBB"):
        rnd.render(repo, Path("site"), runner=fake_runner())


def test_badges_from_an_app_tags_file_unchanged(tmp_path):
    """A separate badges file in the app_tags.json shape (with a "_comment" key) loads as is."""
    repo = make_repo(tmp_path, apps={"io.oxisoft.oxitalk": ("OxiTalk", "Chat", 5, "0.2.1"),
                                     "io.oxisoft.sudoku": ("Sudoku", "Puzzle", 9, "1.0")},
                     brand={"badges": "app_tags.json"})
    (repo / "app_tags.json").write_text((Path(__file__).parent / "fixtures" / "app_tags.json").read_text())
    rnd.render(repo, Path("site"), runner=fake_runner())
    page = (repo / "site" / "index.html").read_text()
    assert '<strong>OxiTalk <span class="tbadge tb-ff6a00">BETA</span></strong>' in page
    assert '<span class="badge cat">Communication</span><span class="badge">Matrix</span>' in page
    assert "_comment" not in page and "<strong>Sudoku</strong>" in page
    assert _ld_apps(page)["Sudoku"]["keywords"] == "puzzle, logic, neon, daily challenge, offline"
    assert ".tbadge.tb-ff6a00" in (repo / "site" / "site.css").read_text()


def test_missing_badges_file_is_refused(tmp_path):
    repo = make_repo(tmp_path, brand={"badges": "nope.json"})
    with pytest.raises(rnd.SiteError, match="nope.json does not exist"):
        rnd.render(repo, Path("site"), runner=fake_runner())


def test_no_title_badges_no_generated_css(tmp_path):
    repo = make_repo(tmp_path, brand={"badges": {"com.example.alpha": {"tags": ["x"]}}})
    rnd.render(repo, Path("site"), runner=fake_runner())
    assert ".tbadge.tb-" not in (repo / "site" / "site.css").read_text()


def test_appdata_for_a_custom_template(tmp_path):
    repo = make_repo(tmp_path, brand={"template": "tpl.html", "locales": ["en-US", "pl"],
                                      "badges": {"com.example.zeta": {"title_badge": "BETA", "tags": ["x"]}}})
    (repo / "tpl.html").write_text('<html lang="$lang"><body>$apps\n'
                                   '<script id="appdata" type="application/json">$appdata</script>'
                                   '<script src="${base}site.js"></script></body></html>')
    rnd.render(repo, Path("site"), runner=fake_runner())
    for sub, up in (("", ""), ("pl/", "../")):
        page = (repo / "site" / sub / "index.html").read_text()
        assert 'data-app="com.example.alpha"' in page and 'data-app="com.example.zeta"' in page
        raw = re.search(r'id="appdata" type="application/json">(.*?)</script>', page, re.S).group(1)
        assert "</" not in raw  # the app name holds "</script>": escaped, still plain JSON
        data = json.loads(raw)
        z = data["com.example.zeta"]
        assert z["name"] == "Zeta </script> app" and z["description"] == ["Line one wrapped.", 'Second "para".']
        assert z["icon"] == f"{up}appicons/com.example.zeta.png" and z["apk"] == f"{up}fdroid/repo/com.example.zeta_42.apk"
        assert z["titleBadge"]["text"] == "BETA" and z["titleBadge"]["cls"].startswith("tb-") and z["tags"] == ["x"]
        assert data["com.example.alpha"]["titleBadge"] is None


def test_default_template_rows_carry_data_app(tmp_path):
    repo = make_repo(tmp_path)
    rnd.render(repo, Path("site"), runner=fake_runner())
    page = (repo / "site" / "index.html").read_text()
    assert '<div class="app" id="com.example.alpha" data-app="com.example.alpha">' in page
    assert "$appdata" not in page
