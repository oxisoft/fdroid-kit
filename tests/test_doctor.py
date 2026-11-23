"""fdroidkit doctor: every check, passing and failing, against an in-memory live repo."""
import hashlib
import http.server
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from fdroidkit import cli, doctor as dr
from fdroidkit.site.render import qr_svg

URL = "https://repo.example.com/fdroid/repo"
SITE = "https://repo.example.com"
FP = "D1902661481C51BEE42B17080BC9D452B87E6754362CC17AF152975AD5A639F5"
OTHER = "AA" * 32
SIGNER = "b3afc17d1126ca4c149eba45eba38558fe518119feb8d98f86f4b54c75593567"
ROTATED = "cc" * 32
ADD = f"{URL}?fingerprint={FP}"


def keytool_out(fp):
    colon = ":".join(fp[i:i + 2] for i in range(0, 64, 2))
    return f"Signer #1:\n\nCertificate #1:\nOwner: CN=example\n\t SHA256: {colon}\n"


def jar_with(entry: dict, marker: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("entry.json", json.dumps(entry))
        z.writestr("META-INF/MARK", marker)
    return b.getvalue()


def apk(dex: bytes = b"dex\n035 Lcom/example/Main;") -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")
        z.writestr("classes.dex", dex)
    return b.getvalue()


def version(code, signers, size=1000, pkg="com.example.app"):
    return {"file": {"name": f"/{pkg}_{code}.apk", "size": size},
            "manifest": {"versionCode": code, "signer": {"sha256": signers}}}


class FakeNet(dr.Net):
    def __init__(self, files: dict, tls_days=60.0):
        self.files, self.days = files, tls_days

    def get(self, url):
        return self.files.get(url)

    def open_apk(self, url):
        if url not in self.files:
            raise OSError(f"404 {url}")
        return io.BytesIO(self.files[url])

    def tls_days(self, host, port=443):
        if isinstance(self.days, Exception):
            raise self.days
        return self.days


def runner_for(jars: dict):
    """keytool/jarsigner fake: the jar's MARK member names its signer."""
    def run(cmd):
        path = cmd[-1]
        with zipfile.ZipFile(path) as z:
            mark = z.read("META-INF/MARK").decode()
        if cmd[0] == "jarsigner":
            if mark == "tampered":
                raise dr.keys.KeyReadError("jarsigner exited 1: digest mismatch")
            return "jar verified.\n"
        return keytool_out(jars.get(mark, FP))
    return run


def live_repo(versions=None, page_fp=FP, add=ADD, qr=True, ts=None, index_bytes=None, v1_mark="key"):
    versions = versions or {"com.example.app": [version(5, [SIGNER]), version(4, [SIGNER])]}
    index = {"repo": {"address": URL}, "packages": {
        p: {"versions": {str(v["manifest"]["versionCode"]): v for v in vs}} for p, vs in versions.items()}}
    raw = json.dumps(index).encode()
    entry = {"timestamp": int((ts or time.time()) * 1000),
             "index": {"name": "/index-v2.json", "sha256": hashlib.sha256(raw).hexdigest()}}
    page = f'<p>Fingerprint <code>{page_fp}</code></p><a class="qr" href="{add}"><img src="qr.svg"></a>'
    files = {f"{URL}/entry.jar": jar_with(entry, "key"), f"{URL}/index-v2.json": index_bytes or raw,
             f"{SITE}/": page.encode()}
    if v1_mark:
        files[f"{URL}/index-v1.jar"] = jar_with({}, v1_mark)
    if qr and qr_svg(add):
        files[f"{SITE}/qr.svg"] = qr_svg(add).encode()
    for vs in versions.values():
        for v in vs:
            files[URL + v["file"]["name"]] = apk()
    return files


def run(files, runner=None, **kw):
    net = kw.pop("net", None) or FakeNet(files)
    return dr.doctor(URL, net=net, runner=runner or runner_for({"other": OTHER}), **kw)


def status(rep, name, subject=None):
    hits = [c for c in rep.checks if c.name == name and (subject is None or c.subject == subject)]
    assert hits, f"no {name} {subject} in {[(c.name, c.subject) for c in rep.checks]}"
    return hits[0].status


def local_repo(tmp_path, pins=(SIGNER,), code=5, local_index_code=None, extra_meta=()):
    md = tmp_path / "metadata"
    md.mkdir()
    pin_lines = "".join(f"  - {p}\n" for p in pins)
    (md / "com.example.app.yml").write_text(f"AllowedAPKSigningKeys:\n{pin_lines}CurrentVersionCode: {code}\n")
    for m in extra_meta:
        (md / f"{m}.yml").write_text("AllowedAPKSigningKeys:\n  - " + SIGNER + "\n")
    if local_index_code:
        (tmp_path / "repo").mkdir()
        (tmp_path / "repo" / "index-v2.json").write_text(json.dumps({"packages": {
            "com.example.app": {"versions": {"x": version(local_index_code, [SIGNER])}}}}))
    return tmp_path


# ---- healthy --------------------------------------------------------------------

def test_healthy_repo_all_ok(tmp_path):
    rep = run(live_repo(), repo=local_repo(tmp_path))
    assert rep.ok, rep.lines()
    assert {c.status for c in rep.checks} <= {"ok", "skip"}
    assert status(rep, "pairip") == "ok"
    assert "healthy" in rep.lines()[-1]
    data = json.loads(rep.to_json())
    assert data["ok"] and data["counts"]["fail"] == 0


# ---- tls --------------------------------------------------------------------------

def test_tls_expiring_fails():
    rep = run(None, net=FakeNet(live_repo(), tls_days=10))
    assert status(rep, "tls") == "fail" and not rep.ok


def test_tls_invalid_cert_fails():
    rep = run(None, net=FakeNet(live_repo(), tls_days=OSError("certificate verify failed")))
    assert status(rep, "tls") == "fail"


def test_http_repo_fails():
    files = {k.replace("https://", "http://"): v for k, v in live_repo().items()}
    rep = dr.doctor(URL.replace("https://", "http://"), net=FakeNet(files), runner=runner_for({}))
    assert status(rep, "tls") == "fail"


# ---- index ------------------------------------------------------------------------

def test_no_entry_jar_is_a_setup_error():
    with pytest.raises(dr.DoctorError):
        run({})


def test_tampered_entry_jar_fails():
    files = live_repo()
    entry = json.loads(zipfile.ZipFile(io.BytesIO(files[f"{URL}/entry.jar"])).read("entry.json"))
    files[f"{URL}/entry.jar"] = jar_with(entry, "tampered")
    rep = run(files)
    assert status(rep, "index", "entry.jar") == "fail"


def test_index_not_matching_signed_entry_fails():
    rep = run(live_repo(index_bytes=b'{"packages": {}}'))
    assert status(rep, "index", "index-v2.json") == "fail"


def test_index_v1_other_signer_fails_and_missing_warns():
    assert status(run(live_repo(v1_mark="other")), "index", "index-v1.jar") == "fail"
    rep = run(live_repo(v1_mark=None))
    assert status(rep, "index", "index-v1.jar") == "warn" and rep.ok


def test_stale_index_warns_only():
    rep = run(live_repo(ts=time.time() - 200 * 86400))
    assert status(rep, "freshness") == "warn" and rep.ok


# ---- landing ----------------------------------------------------------------------

def test_wrong_fingerprint_on_page_fails():
    assert status(run(live_repo(page_fp=OTHER)), "landing", "fingerprint") == "fail"


def test_add_link_wrong_fingerprint_or_address_fails():
    assert status(run(live_repo(add=f"{URL}?fingerprint={OTHER}")), "landing", "add-repo link") == "fail"
    assert status(run(live_repo(add=f"https://evil.example/repo?fingerprint={FP}")),
                  "landing", "add-repo link") == "fail"


def test_fdroidrepos_scheme_is_the_same_address():
    add = "fdroidrepos://repo.example.com/fdroid/repo?fingerprint=" + FP
    assert status(run(live_repo(add=add)), "landing", "add-repo link") == "ok"


def test_qr_mismatch_and_missing_fail():
    if qr_svg("x") is None:
        pytest.skip("qrcode not installed")
    files = live_repo()
    files[f"{SITE}/qr.svg"] = qr_svg(f"{URL}?fingerprint={OTHER}").encode()
    assert status(run(files), "landing", "qr.svg") == "fail"
    files = live_repo(qr=False)
    assert status(run(files), "landing", "qr.svg") == "fail"


def test_missing_landing_page_fails():
    files = live_repo()
    del files[f"{SITE}/"]
    assert status(run(files), "landing") == "fail"


# ---- signer pins -------------------------------------------------------------------

def test_unpinned_signer_fails(tmp_path):
    rep = run(live_repo(), repo=local_repo(tmp_path, pins=(ROTATED,)))
    assert status(rep, "signer") == "fail"


def test_rotation_passes_when_both_pinned(tmp_path):
    files = live_repo({"com.example.app": [version(5, [ROTATED]), version(4, [SIGNER])]})
    rep = run(files, repo=local_repo(tmp_path, pins=(SIGNER, ROTATED)))
    assert status(rep, "signer") == "ok" and "rotation" in rep.checks[[c.name for c in rep.checks].index("signer")].detail


def test_signer_change_without_repo_warns():
    files = live_repo({"com.example.app": [version(5, [ROTATED]), version(4, [SIGNER])]})
    assert status(run(files), "signer") == "warn"


def test_served_app_without_metadata_fails(tmp_path):
    files = live_repo({"com.example.app": [version(5, [SIGNER])], "com.other": [
        {"file": {"name": "/com.other_1.apk"}, "manifest": {"versionCode": 1, "signer": {"sha256": [SIGNER]}}}]})
    files[f"{URL}/com.other_1.apk"] = apk()
    assert status(run(files, repo=local_repo(tmp_path)), "signer", "com.other") == "fail"


# ---- pairip ------------------------------------------------------------------------

def test_pairip_apk_fails():
    files = live_repo()
    files[f"{URL}/com.example.app_5.apk"] = apk(b"dex Lcom/pairip/licensecheck/LicenseClient;")
    rep = run(files)
    assert status(rep, "pairip") == "fail" and not rep.ok


def test_only_newest_scanned_unless_all_versions():
    files = live_repo()
    files[f"{URL}/com.example.app_4.apk"] = apk(b"Lcom/pairip/x;")
    assert run(files).ok
    rep = run(files, all_versions=True)
    assert [c.status for c in rep.checks if c.name == "pairip"] == ["ok", "fail"]


def test_missing_apk_and_no_dex_fail():
    files = live_repo()
    del files[f"{URL}/com.example.app_5.apk"]
    assert status(run(files), "pairip") == "fail"
    files = live_repo()
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")
    files[f"{URL}/com.example.app_5.apk"] = b.getvalue()
    assert status(run(files), "pairip") == "fail"


def test_no_apk_skips_scan():
    assert status(run(live_repo(), scan_apks=False), "pairip") == "skip"


# ---- versions ---------------------------------------------------------------------

def test_metadata_ahead_of_live_fails(tmp_path):
    rep = run(live_repo(), repo=local_repo(tmp_path, code=6))
    assert status(rep, "version", "com.example.app") == "fail"


def test_local_index_ahead_of_live_fails(tmp_path):
    rep = run(live_repo(), repo=local_repo(tmp_path, code=5, local_index_code=7))
    assert status(rep, "version", "com.example.app") == "fail"


def test_metadata_only_app_warns(tmp_path):
    rep = run(live_repo(), repo=local_repo(tmp_path, extra_meta=["com.notyet"]))
    assert status(rep, "version", "com.notyet") == "warn" and rep.ok


# ---- range reader -----------------------------------------------------------------

class RangeHandler(http.server.SimpleHTTPRequestHandler):
    body = apk(b"x" * 200000 + b"Lcom/example;")

    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        b = self.body
        if rng:
            a, z = rng.split("=")[1].split("-")
            a, z = int(a), int(z)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{z}/{len(b)}")
            chunk = b[a:z + 1]
        else:
            self.send_response(200)
            chunk = b
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *a):
        pass


def test_range_reader_reads_dex_without_full_download():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        f = dr.HttpRangeFile.open(f"http://127.0.0.1:{srv.server_port}/a.apk")
        assert isinstance(f, dr.HttpRangeFile)
        with zipfile.ZipFile(f) as z:
            assert b"Lcom/example;" in z.read("classes.dex")
    finally:
        srv.shutdown()


# ---- storage ---------------------------------------------------------------------------

ARCH = URL.removesuffix("/repo") + "/archive"
MB = 10**6


def with_archive(files, versions, mark="key", index_bytes=None):
    """Add a signed archive (entry.jar + index-v2.json) next to the repo."""
    index = {"packages": {p: {"versions": {str(v["manifest"]["versionCode"]): v for v in vs}}
                          for p, vs in versions.items()}}
    raw = json.dumps(index).encode()
    entry = {"index": {"name": "/index-v2.json", "sha256": hashlib.sha256(raw).hexdigest()}}
    files[f"{ARCH}/entry.jar"] = jar_with(entry, mark)
    files[f"{ARCH}/index-v2.json"] = index_bytes or raw
    return files


def sized(pkg, sizes_mb, first_code=10):
    return [version(first_code - i, [SIGNER], size=int(mb * MB), pkg=pkg) for i, mb in enumerate(sizes_mb)]


def test_storage_without_budget_reports_total_only():
    rep = run(live_repo({"com.example.app": sized("com.example.app", [300, 200])}))
    assert status(rep, "storage", "total") == "ok"
    assert rep.storage["total_bytes"] == 500 * MB and rep.storage["budget_bytes"] is None
    assert rep.storage["archive"] == "no archive served"


def test_storage_under_budget_ok_and_counts_archive():
    files = with_archive(live_repo({"com.example.app": sized("com.example.app", [100])}),
                         {"com.example.app": sized("com.example.app", [100, 100], first_code=9)})
    rep = run(files, budget=dr.parse_budget("github-pages"))
    assert status(rep, "storage", "total") == "ok" and rep.ok
    st = rep.storage
    assert (st["repo_bytes"], st["archive_bytes"], st["apks"]) == (100 * MB, 200 * MB, 3)
    assert st["percent"] == 30.0 and st["budget"] == "github-pages"
    assert "30.0% of github-pages 1.0 GB" in [c.detail for c in rep.checks if c.name == "storage"][0]


def test_storage_warns_at_80_percent_and_ranks_apps():
    files = live_repo({"com.big": sized("com.big", [500, 200]), "com.small": sized("com.small", [150])})
    rep = run(files, budget=dr.parse_budget("github-pages"))
    assert status(rep, "storage", "total") == "warn" and rep.ok
    data = json.loads(rep.to_json())["storage"]
    assert [a["package"] for a in data["apps"]] == ["com.big", "com.small"]
    assert data["apps"][0]["bytes"] == 700 * MB and data["apps"][0]["versions"] == 2


def test_storage_fails_over_budget():
    files = with_archive(live_repo({"com.example.app": sized("com.example.app", [400])}),
                         {"com.example.app": sized("com.example.app", [400, 300], first_code=9)})
    rep = run(files, budget=dr.parse_budget("github-pages"))
    assert status(rep, "storage", "total") == "fail" and not rep.ok
    assert any("OVER BUDGET" in c.detail for c in rep.checks if c.name == "storage")


def test_storage_ignores_archive_with_another_signer_or_stale_index():
    files = with_archive(live_repo({"com.example.app": sized("com.example.app", [100])}),
                         {"com.example.app": sized("com.example.app", [900], first_code=9)}, mark="other")
    rep = run(files, budget=dr.parse_budget("github-pages"))
    assert status(rep, "storage", "archive") == "warn" and rep.storage["archive_bytes"] == 0
    files = with_archive(live_repo({"com.example.app": sized("com.example.app", [100])}),
                         {"com.example.app": sized("com.example.app", [900], first_code=9)},
                         index_bytes=b'{"packages": {}}')
    rep = run(files)
    assert status(rep, "storage", "archive") == "warn" and rep.storage["archive"] == "archive index unverified"


def test_storage_projects_archive_older_from_repo(tmp_path):
    (tmp_path / "config.yml").write_text("repo_url: x\narchive_older: 3\n")
    files = live_repo({"com.big": sized("com.big", [200]), "com.small": sized("com.small", [100])})
    rep = run(files, repo=None, budget=dr.parse_budget("github-pages"))
    assert not [c for c in rep.checks if c.subject == "projected"]
    rep = dr.doctor(URL, net=FakeNet(files), runner=runner_for({}), budget=dr.parse_budget("github-pages"))
    dr.check_storage(rep, FakeNet(files), URL, FP, json.loads(files[f"{URL}/index-v2.json"]),
                     dr.parse_budget("github-pages"), runner_for({}), keep=dr.archive_older(tmp_path))
    proj = [c for c in rep.checks if c.subject == "projected"]
    assert proj and proj[0].status == "warn" and "archive_older 2 keeps it under 80%" in proj[0].detail
    assert rep.storage["projected_bytes"] == 900 * MB and rep.storage["archive_older"] == 3
    (tmp_path / "config.yml").write_text("repo_url: x\n")
    assert dr.archive_older(tmp_path) == 3 and dr.archive_older(None) is None


def test_storage_missing_size_warns():
    v = version(5, [SIGNER])
    del v["file"]["size"]
    rep = run(live_repo({"com.example.app": [v]}))
    assert status(rep, "storage", "sizes") == "warn"


@pytest.mark.parametrize("spec,want", [("github-pages", 10**9), ("123456", 123456), ("500M", 500 * MB),
                                       ("2G", 2 * 10**9), ("1GiB", 2**30), ("1.5gb", 1_500_000_000)])
def test_parse_budget(spec, want):
    assert dr.parse_budget(spec)[0] == want


def test_parse_budget_none_and_bad():
    assert dr.parse_budget(None) is None and dr.parse_budget("none") is None
    for bad in ("2x", "-1", "0", "lots"):
        with pytest.raises(ValueError):
            dr.parse_budget(bad)


# ---- cli -----------------------------------------------------------------------------

def test_cli_needs_a_url(capsys):
    assert cli.main(["doctor"]) == 3


def test_cli_json_and_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(dr, "Net", lambda: FakeNet(live_repo()))
    monkeypatch.setattr(dr, "_run", runner_for({}))
    assert cli.main(["doctor", "--url", URL, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    monkeypatch.setattr(dr, "Net", lambda: FakeNet(live_repo(page_fp=OTHER)))
    assert cli.main(["doctor", "--url", URL]) == 1
    assert "UNHEALTHY" in capsys.readouterr().out


def test_cli_budget(monkeypatch, capsys):
    big = {"com.example.app": sized("com.example.app", [600, 500])}
    monkeypatch.setattr(dr, "Net", lambda: FakeNet(live_repo(big)))
    monkeypatch.setattr(dr, "_run", runner_for({}))
    assert cli.main(["doctor", "--url", URL, "--no-apk"]) == 0
    capsys.readouterr()
    assert cli.main(["doctor", "--url", URL, "--no-apk", "--budget", "github-pages", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["storage"]["percent"] == 110.0
    assert cli.main(["doctor", "--url", URL, "--budget", "2x"]) == 2
