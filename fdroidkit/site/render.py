"""Render the landing page from the signed index.

Rules:
- The page lists only what the signed index serves, never a hand-kept app list. The
  index-v2.json is used only when its SHA-256 matches the one in entry.json.
- The fingerprint and the add-repo QR come from the certificate that signed the published
  entry.jar, never from a value typed into a config file.
- The template is neutral; the brand (title, accent colour, links, footer) comes from
  `fdroidkit.json` in the repo. Output passes a strict CSP: no inline script or style.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import json
import re
import shutil
import string
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Callable

from . import keys

ICON_PX = 192  # rows show icons at 84 px, 192 covers a 2x screen
SCHEMA_CATEGORY = {"Games": "GameApplication", "Time": "ProductivityApplication",
                   "Security": "SecurityApplication", "Internet": "BrowserApplication"}
HEX = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


class SiteError(RuntimeError):
    pass


@dataclass
class Brand:
    """Branding from `<repo>/fdroidkit.json` key "site"; everything optional."""
    title: str = ""
    description: str = ""
    site_url: str = ""
    accent: str = "#2F6FEB"
    org_name: str = ""
    org_url: str = ""
    footer_links: list = field(default_factory=list)  # [{"label": ..., "url": ...}]
    badges: dict = field(default_factory=dict)  # appId -> {"category": str, "tags": [str]}
    template: str = ""  # custom index.html template, relative to the repo


def load_brand(repo: Path) -> Brand:
    p = repo / "fdroidkit.json"
    if not p.exists():
        return Brand()
    try:
        raw = json.loads(p.read_text()).get("site", {})
    except json.JSONDecodeError as e:
        raise SiteError(f"{p}: {e}") from e
    b = Brand(**{k: v for k, v in raw.items() if k in Brand.__dataclass_fields__})
    if not HEX.match(b.accent):
        raise SiteError(f"{p}: site.accent must be a hex colour like #2F6FEB, got {b.accent!r}")
    return b


def pick(d, locale: str = "en-US"):
    """index-v2 localises almost everything; take en-US or whatever exists."""
    if not isinstance(d, dict):
        return d
    return d.get(locale) or (next(iter(d.values())) if d else None)


def human_size(n: int) -> str:
    mb = n / 1048576
    return f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB"


def load_signed_index(repo: Path) -> dict:
    """repo/index-v2.json, refused unless entry.json (signed in entry.jar) vouches for it."""
    rd = repo / "repo"
    index, entry = rd / "index-v2.json", rd / "entry.json"
    if not index.exists():
        raise SiteError(f"no index at {index}: run `fdroid update` first")
    if not entry.exists():
        raise SiteError(f"no {entry}: the index is unsigned, run `fdroid update` with a repo key")
    raw = index.read_bytes()
    want = json.loads(entry.read_text()).get("index", {}).get("sha256", "")
    got = hashlib.sha256(raw).hexdigest()
    if want != got:
        raise SiteError(f"{index} does not match entry.json (sha256 {got} != {want or 'none'}): "
                        "the index is not the signed one, run `fdroid update` again")
    return json.loads(raw)


def site_root(address: str, brand: Brand) -> str:
    if brand.site_url:
        return brand.site_url.rstrip("/")
    return re.sub(r"/fdroid/repo/?$", "", address.rstrip("/")) or address.rstrip("/")


def write_icon(src: Path, dst: Path) -> None:
    """Copy the index icon next to the page, downscaled when Pillow is installed."""
    try:
        from PIL import Image
    except ImportError:
        shutil.copyfile(src, dst)
        return
    with Image.open(src) as im:
        im = im.convert("RGBA")
        if im.width > ICON_PX:
            im = im.resize((ICON_PX, ICON_PX), Image.LANCZOS)
        im.save(dst, "PNG", optimize=True)


def qr_svg(payload: str) -> str | None:
    """Add-repo QR as SVG (pip install 'fdroidkit[site]'); None without the qrcode package."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return None
    img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


def group_fp(fp: str) -> str:
    return " ".join(fp[i:i + 4] for i in range(0, len(fp), 4))


def _apps(repo: Path, data: dict, brand: Brand, out: Path) -> list[dict]:
    icon_dir = out / "appicons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    apps = []
    packages = data.get("packages", {})
    for app_id, pkg in sorted(packages.items(),
                              key=lambda kv: (pick(kv[1]["metadata"].get("name")) or kv[0]).lower()):
        md = pkg["metadata"]
        versions = sorted(pkg.get("versions", {}).values(),
                          key=lambda v: v["manifest"].get("versionCode", 0), reverse=True)
        latest = versions[0] if versions else None
        icon = ""
        meta_icon = pick(md.get("icon"))
        if isinstance(meta_icon, dict):
            src = repo / "repo" / meta_icon["name"].lstrip("/")
            if src.exists():
                write_icon(src, icon_dir / f"{app_id}.png")
                icon = f"appicons/{app_id}.png"
        raw_desc = pick(md.get("description")) or ""
        fd_cat = (md.get("categories") or [""])[0]
        b = brand.badges.get(app_id, {})
        phone = pick(md.get("screenshots", {}).get("phone")) or []
        apps.append({
            "id": app_id,
            "name": pick(md.get("name")) or app_id,
            "summary": pick(md.get("summary")) or "",
            # Descriptions are hard-wrapped in metadata: unwrap, keep blank-line paragraphs.
            "description": [" ".join(p.split()) for p in re.split(r"\n\s*\n", raw_desc) if p.strip()],
            "icon": icon,
            "version": latest["manifest"].get("versionName", "?") if latest else "?",
            "size": human_size(latest["file"]["size"]) if latest else "",
            "apk": "fdroid/repo/" + latest["file"]["name"].lstrip("/") if latest else "",
            "website": pick(md.get("webSite")) or "",
            "shots": ["fdroid/repo" + s["name"] for s in phone],
            "catLabel": b.get("category") or fd_cat,
            "tags": [t for t in b.get("tags", []) if t],
            "category": SCHEMA_CATEGORY.get(fd_cat, ""),
        })
    return apps


def _app_rows(apps: list[dict]) -> str:
    e = html.escape
    rows = []
    for n, a in enumerate(apps):
        lazy = ' loading="lazy"' if n else ""  # only the first row is reliably above the fold
        img = (f'<img src="{e(a["icon"])}" alt="" width="84" height="84"{lazy} decoding="async">'
               if a["icon"] else "")
        badges = ""
        if a["catLabel"] or a["tags"]:
            badges = ('<div class="badges">'
                      + (f'<span class="badge cat">{e(a["catLabel"])}</span>' if a["catLabel"] else "")
                      + "".join(f'<span class="badge">{e(t)}</span>' for t in a["tags"]) + "</div>")
        dl = (f'<a class="applink" href="{e(a["apk"])}" aria-label="Download APK for {e(a["name"])}">'
              "Download APK</a>") if a["apk"] else ""
        desc = "".join(f"<p>{e(p)}</p>" for p in a["description"])
        more = f"<details><summary>More</summary>{desc}</details>" if desc else ""
        rows.append(f'''      <div class="app" id="{e(a["id"])}">
        {img}
        <div class="appmeta">
          <strong>{e(a["name"])}</strong>
          <p>{e(a["summary"])}</p>
          {badges}
          <span class="appver">{e(a["version"])} &middot; {e(a["size"])}</span>
          {more}
        </div>
        {dl}
      </div>''')
    return "\n".join(rows)


def structured_data(root: str, title: str, description: str, brand: Brand, apps: list[dict]) -> dict:
    org = None
    if brand.org_name:
        org = {"@type": "Organization", "@id": f"{(brand.org_url or root).rstrip('/')}/#organization",
               "name": brand.org_name}
        if brand.org_url:
            org["url"] = brand.org_url
    items = []
    for a in apps:
        app = {"@type": "MobileApplication", "@id": f"{root}/#{a['id']}", "name": a["name"],
               "description": a["summary"], "operatingSystem": "Android",
               "softwareVersion": a["version"], "fileSize": a["size"],
               "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}
        if org:
            app["author"] = app["publisher"] = {"@id": org["@id"]}
        if a["category"]:
            app["applicationCategory"] = a["category"]
        if a["tags"]:
            app["keywords"] = ", ".join(a["tags"])
        if a["icon"]:
            app["image"] = f"{root}/{a['icon']}"
        if a["apk"]:
            app["downloadUrl"] = f"{root}/{a['apk']}"
        if a["website"]:
            app["url"] = a["website"]
        if a["shots"]:
            app["screenshot"] = [f"{root}/{s}" for s in a["shots"]]
        items.append(app)
    website = {"@type": "WebSite", "@id": f"{root}/#website", "url": f"{root}/", "name": title,
               "description": description, "inLanguage": "en"}
    if org:
        website["publisher"] = {"@id": org["@id"]}
    graph = [website,
             {"@type": "ItemList", "@id": f"{root}/#apps", "name": "Apps in the repository",
              "numberOfItems": len(items),
              "itemListElement": [{"@type": "ListItem", "position": i + 1, "item": a}
                                  for i, a in enumerate(items)]}]
    if org:
        graph.insert(0, org)
    return {"@context": "https://schema.org", "@graph": graph}


def llms_txt(title: str, description: str, address: str, fp: str, root: str, apps: list[dict],
             brand: Brand) -> str:
    lines = [f"# {title}", ""]
    if description:
        lines += [f"> {description}", ""]
    lines += ["An F-Droid repository. F-Droid is a free, open-source app store for Android; "
              "it never asks you to sign in.", "",
              f"- Repository address: {address}",
              f"- Index signing fingerprint (SHA-256): {fp}", "", "## Apps", ""]
    for a in apps:
        lines.append(f"- [{a['name']}]({a['website'] or root + '/'}): {a['summary']} "
                     f"Version {a['version']}, {a['size']}.")
    lines += ["", "## Adding the repository by hand", "",
              "1. Open F-Droid and go to Settings, Repositories", "2. Tap + to add a new one",
              f"3. Enter {address}", "4. Check the fingerprint matches, then confirm",
              "5. Pull to refresh; the apps appear in the list", ""]
    if brand.footer_links:
        lines += ["## Links", ""] + [f"- [{x['label']}]({x['url']})" for x in brand.footer_links] + [""]
    return "\n".join(lines)


def _template(repo: Path, brand: Brand, name: str) -> str:
    if name == "index.html" and brand.template:
        return (repo / brand.template).read_text()
    return resources.files("fdroidkit.site").joinpath("templates").joinpath(name).read_text()


@dataclass
class Result:
    out: Path
    fingerprint: str
    apps: list
    qr: bool


def render(repo: Path, out: Path, runner: Callable | None = None, log: Callable = print) -> Result:
    repo, out = Path(repo), Path(out)
    if not out.is_absolute():
        out = repo / out
    brand = load_brand(repo)
    data = load_signed_index(repo)
    meta = data.get("repo", {})
    address = (meta.get("address") or "").rstrip("/")
    if not address:
        raise SiteError("index has no repo address (set repo_url in config.yml)")
    fp = keys.jar_fingerprint(repo / "repo" / "entry.jar", runner=runner)
    root = site_root(address, brand)
    title = brand.title or pick(meta.get("name")) or "F-Droid repository"
    description = brand.description or " ".join((pick(meta.get("description")) or "").split())
    out.mkdir(parents=True, exist_ok=True)
    apps = _apps(repo, data, brand, out)
    e = html.escape

    add_url = f"{address}?fingerprint={fp}"
    svg = qr_svg(add_url)
    if svg:
        (out / "qr.svg").write_text(svg)
        qr_html = (f'<a class="qr" href="{e(add_url)}"><img src="qr.svg" width="200" '
                   'height="200" alt="QR code to add this repository in F-Droid"></a>')
    else:
        log("fdroidkit site: no QR (pip install 'fdroidkit[site]' for the qrcode package)")
        qr_html = ""
    logo_html = ""
    repo_icon = pick(meta.get("icon"))
    if isinstance(repo_icon, dict):
        src = repo / "repo" / repo_icon["name"].lstrip("/")
        if src.exists():
            write_icon(src, out / "icon.png")
            logo_html = '<img class="logo" src="icon.png" alt="" width="64" height="64">'

    count = len(apps)
    links = " &middot; ".join(f'<a href="{e(x["url"])}">{e(x["label"])}</a>' for x in brand.footer_links)
    org = f'<a href="{e(brand.org_url)}">{e(brand.org_name)}</a>' if brand.org_url else e(brand.org_name)
    updated = datetime.datetime.fromtimestamp(meta.get("timestamp", 0) / 1000,
                                              datetime.timezone.utc).date().isoformat()
    ld = json.dumps(structured_data(root, title, description, brand, apps),
                    ensure_ascii=False).replace("</", "<\\/")
    page = string.Template(_template(repo, brand, "index.html")).safe_substitute(
        title=e(title), description=e(description), canonical=e(root + "/"),
        logo=logo_html, address=e(address), add_url=e(add_url), qr=qr_html,
        fingerprint=fp, fingerprint_grouped=group_fp(fp),
        app_heading=f"{count} app{'s' if count != 1 else ''} in the repository",
        apps=_app_rows(apps), org=org, footer_links=links, updated=updated, jsonld=ld)
    (out / "index.html").write_text(page)
    css = string.Template(_template(repo, brand, "site.css")).safe_substitute(accent=brand.accent)
    (out / "site.css").write_text(css)
    (out / "llms.txt").write_text(llms_txt(title, description, address, fp, root, apps, brand))
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{e(root)}/</loc><lastmod>{updated}</lastmod></url>\n</urlset>\n")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {root}/sitemap.xml\n")
    for p in [out, *out.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)  # rsync carries modes to the web root
    return Result(out=out, fingerprint=fp, apps=[a["name"] for a in apps], qr=bool(svg))
