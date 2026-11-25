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
import urllib.parse
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Callable

from . import keys

ICON_PX = 192  # rows show icons at 84 px, 192 covers a 2x screen
SCHEMA_CATEGORY = {"Games": "GameApplication", "Time": "ProductivityApplication",
                   "Security": "SecurityApplication", "Internet": "BrowserApplication"}
HEX = re.compile(r"^#[0-9A-Fa-f]{3,8}$")
RGB = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")
FALLBACK_BADGE = "#FF6A00"
LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


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
    # appId -> {"category": str, "tags": [str], "title_badge": {"text": str, "color": "#RRGGBB"}},
    # or a path (relative to the repo) to a JSON file of that shape; keys starting with "_" are
    # comments. After load_brand every title_badge is {"text", "color" (#rrggbb, lower case)}.
    badges: dict | str = field(default_factory=dict)
    template: str = ""  # custom index.html template, relative to the repo
    # false: site.css holds only the title badge rules (a custom template brings its own theme
    # and links it after site.css); true: the kit's full stylesheet plus the badge rules.
    base_css: bool = True
    # "Add to Obtainium" per app: "direct" (obtainium:// link, no third party), "redirect"
    # (Obtainium's own redirect page, falls back to a "get Obtainium" prompt) or false (off).
    obtainium: str | bool = "direct"
    # Listing languages, first = the root page; every other one gets /<locale>/ (lower case).
    # Fields a locale lacks fall back to en-US, then to any language the index has.
    locales: list = field(default_factory=lambda: ["en-US"])


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
    if not isinstance(b.base_css, bool):
        raise SiteError(f"{p}: site.base_css must be true or false, got {b.base_css!r}")
    if b.obtainium is True:
        b.obtainium = "direct"
    if b.obtainium not in (False, "direct", "redirect"):
        raise SiteError(f'{p}: site.obtainium must be "direct", "redirect" or false, got {b.obtainium!r}')
    if isinstance(b.locales, str):
        b.locales = [b.locales]
    if not b.locales or not all(isinstance(x, str) and LOCALE.match(x) for x in b.locales):
        raise SiteError(f'{p}: site.locales must be a list of locales like ["en-US", "pl"], got {b.locales!r}')
    if len({x.lower() for x in b.locales}) != len(b.locales):
        raise SiteError(f"{p}: site.locales lists a locale twice: {b.locales!r}")
    b.badges = load_badges(repo, b.badges, b.accent, str(p))
    return b


def _hex6(color: str) -> str:
    color = color.lower()
    return "#" + "".join(c * 2 for c in color[1:]) if len(color) == 4 else color


def load_badges(repo: Path, badges, accent: str, where: str) -> dict:
    """site.badges as a dict or a JSON file path; validated, title badges normalised."""
    if isinstance(badges, str):
        bp = repo / badges
        try:
            badges = json.loads(bp.read_text())
        except FileNotFoundError as e:
            raise SiteError(f"{where}: site.badges file {bp} does not exist") from e
        except json.JSONDecodeError as e:
            raise SiteError(f"{bp}: {e}") from e
        where = str(bp)
    if not isinstance(badges, dict):
        raise SiteError(f"{where}: badges must be an object keyed by applicationId")
    default = _hex6(accent) if RGB.match(accent) else FALLBACK_BADGE.lower()
    out = {}
    for app_id, b in badges.items():
        if app_id.startswith("_"):
            continue  # "_comment" and friends
        if not isinstance(b, dict):
            raise SiteError(f"{where}: badges for {app_id} must be an object, got {b!r}")
        b = dict(b)
        tags = b.get("tags", [])
        if not isinstance(tags, list):
            raise SiteError(f"{where}: {app_id}: tags must be a list, got {tags!r}")
        tb = b.pop("title_badge", None)
        if isinstance(tb, str):
            tb = {"text": tb}
        if tb:
            if not isinstance(tb, dict):
                raise SiteError(f"{where}: {app_id}: title_badge must be text or {{\"text\", \"color\"}}")
            text = str(tb.get("text", "")).strip()
            color = str(tb.get("color") or default).strip()
            if not RGB.match(color):
                raise SiteError(f"{where}: {app_id}: title_badge color must be #RGB or #RRGGBB, got {color!r}")
            if text:
                b["title_badge"] = {"text": text, "color": _hex6(color)}
        out[app_id] = b
    return out


def _luminance(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


DARK_TEXT, LIGHT_TEXT = "#0a0a0a", "#ffffff"


def badge_text_color(color: str) -> str:
    """Dark or light text, whichever has the higher WCAG contrast ratio on the badge colour."""
    bg = _luminance(color)
    dark = (bg + 0.05) / (_luminance(DARK_TEXT) + 0.05)
    light = (_luminance(LIGHT_TEXT) + 0.05) / (bg + 0.05)
    return DARK_TEXT if dark >= light else LIGHT_TEXT


TBADGE_BASE = (".tbadge { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; border: 1px solid; "
               "border-radius: 4px; padding: 0 6px; vertical-align: middle; }\n")


def badge_css(brand: Brand) -> str:
    """One class per title badge colour: the page runs under style-src 'self', no style=."""
    colors = sorted({b["title_badge"]["color"] for b in brand.badges.values() if b.get("title_badge")})
    if not colors:
        return ""
    return "\n/* title badges, generated from site.badges */\n" + "".join(
        f".tbadge.tb-{c[1:]} {{ background: {c}; border-color: {c}; color: {badge_text_color(c)}; }}\n"
        for c in colors)


def pick(d, locale: str = "en-US"):
    """index-v2 localises almost everything: the locale asked for, then the same language in
    another region (pl for pl-PL and back), then en-US, then whatever exists."""
    if not isinstance(d, dict):
        return d
    if d.get(locale):
        return d[locale]
    lang = locale.split("-")[0].lower()
    for k, v in d.items():
        if v and k.split("-")[0].lower() == lang:
            return v
    return d.get("en-US") or next((v for v in d.values() if v), None)


def index_locales(data: dict) -> set[str]:
    """Every locale the signed index carries a name, summary or description in."""
    found = set()
    for pkg in data.get("packages", {}).values():
        md = pkg.get("metadata", {})
        for k in ("name", "summary", "description"):
            if isinstance(md.get(k), dict):
                found.update(x for x, v in md[k].items() if v)
    return found


def locale_path(locale: str) -> str:
    return locale.lower()


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


OBTAINIUM_REDIRECT = "https://apps.obtainium.imranr.dev/redirect?r="


def obtainium_link(address: str, app_id: str, name: str, author: str, mode: str) -> str:
    """Obtainium deep link that imports one app with the F-Droid third-party repo source.

    A bare obtainium://add/<repo url> is not enough: that source has neverAutoSelect, so
    Obtainium would not pick it for an unknown host and would not know which app of the
    repo is meant. obtainium://app/<config> sets both (wiki.obtainium.imranr.dev/deep_links/;
    source id = the Dart class name FDroidRepo, app picked by appIdOrName / ?appId=).
    """
    config = {"id": app_id, "url": f"{address}?appId={app_id}", "author": author, "name": name,
              "overrideSource": "FDroidRepo",
              "additionalSettings": json.dumps({"appIdOrName": app_id}, separators=(",", ":"))}
    link = "obtainium://app/" + urllib.parse.quote(
        json.dumps(config, separators=(",", ":"), ensure_ascii=False), safe="")
    return OBTAINIUM_REDIRECT + urllib.parse.quote(link, safe="") if mode == "redirect" else link


def group_fp(fp: str) -> str:
    return " ".join(fp[i:i + 4] for i in range(0, len(fp), 4))


def _apps(repo: Path, data: dict, brand: Brand, out: Path, address: str = "",
          author: str = "", locale: str = "en-US") -> list[dict]:
    icon_dir = out / "appicons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    apps = []
    packages = data.get("packages", {})
    for app_id, pkg in sorted(packages.items(),
                              key=lambda kv: (pick(kv[1]["metadata"].get("name"), locale) or kv[0]).lower()):
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
        raw_desc = pick(md.get("description"), locale) or ""
        fd_cat = (md.get("categories") or [""])[0]
        b = brand.badges.get(app_id, {})
        phone = pick(md.get("screenshots", {}).get("phone"), locale) or []
        name = pick(md.get("name"), locale) or app_id
        apps.append({
            "id": app_id,
            "name": name,
            "summary": pick(md.get("summary"), locale) or "",
            # Descriptions are hard-wrapped in metadata: unwrap, keep blank-line paragraphs.
            "description": [" ".join(p.split()) for p in re.split(r"\n\s*\n", raw_desc) if p.strip()],
            "icon": icon,
            "version": latest["manifest"].get("versionName", "?") if latest else "?",
            "size": human_size(latest["file"]["size"]) if latest else "",
            "apk": "fdroid/repo/" + latest["file"]["name"].lstrip("/") if latest else "",
            "website": pick(md.get("webSite"), locale) or "",
            "shots": ["fdroid/repo" + s["name"] for s in phone],
            "catLabel": b.get("category") or fd_cat,
            "tags": [str(t) for t in b.get("tags", []) if t],
            "titleBadge": b.get("title_badge"),
            "category": SCHEMA_CATEGORY.get(fd_cat, ""),
            "obtainium": (obtainium_link(address, app_id, name, author, brand.obtainium)
                          if brand.obtainium and address and latest else ""),
        })
    return apps


def _app_rows(apps: list[dict]) -> str:
    e = html.escape
    rows = []
    for n, a in enumerate(apps):
        lazy = ' loading="lazy"' if n else ""  # only the first row is reliably above the fold
        img = (f'<img src="{e(a.get("href_icon", a["icon"]))}" alt="" width="84" height="84"{lazy} decoding="async">'
               if a["icon"] else "")
        badges = ""
        if a["catLabel"] or a["tags"]:
            badges = ('<div class="badges">'
                      + (f'<span class="badge cat">{e(a["catLabel"])}</span>' if a["catLabel"] else "")
                      + "".join(f'<span class="badge">{e(t)}</span>' for t in a["tags"]) + "</div>")
        tb = a.get("titleBadge")
        tbadge = f' <span class="tbadge tb-{tb["color"][1:]}">{e(tb["text"])}</span>' if tb else ""
        dl = (f'<a class="applink" href="{e(a.get("href_apk", a["apk"]))}" aria-label="Download APK for {e(a["name"])}">'
              "Download APK</a>") if a["apk"] else ""
        if a.get("obtainium"):
            dl = ('<div class="appactions">' + dl
                  + f'<a class="applink alt" href="{e(a["obtainium"])}" '
                  f'aria-label="Add {e(a["name"])} to Obtainium">Add to Obtainium</a></div>')
        desc = "".join(f"<p>{e(p)}</p>" for p in a["description"])
        more = f"<details><summary>More</summary>{desc}</details>" if desc else ""
        rows.append(f'''      <div class="app" id="{e(a["id"])}" data-app="{e(a["id"])}">
        {img}
        <div class="appmeta">
          <strong>{e(a["name"])}{tbadge}</strong>
          <p>{e(a["summary"])}</p>
          {badges}
          <span class="appver">{e(a["version"])} &middot; {e(a["size"])}</span>
          {more}
        </div>
        {dl}
      </div>''')
    return "\n".join(rows)


def app_data(apps: list[dict]) -> str:
    """Per-app JSON for a custom template's script (`<script id="appdata"
    type="application/json">$appdata</script>`, read by a same-origin site.js under a strict
    CSP): keyed by applicationId, page-relative paths, safe inside a script element."""
    out = {}
    for a in apps:
        tb = a.get("titleBadge")
        out[a["id"]] = {
            "id": a["id"], "name": a["name"], "summary": a["summary"], "description": a["description"],
            "icon": a.get("href_icon", a["icon"]), "version": a["version"], "size": a["size"],
            "apk": a.get("href_apk", a["apk"]), "website": a["website"],
            "shots": [a.get("base", "") + s for s in a["shots"]], "catLabel": a["catLabel"], "tags": a["tags"],
            "titleBadge": dict(tb, cls=f"tb-{tb['color'][1:]}") if tb else None,
            "obtainium": a["obtainium"],
        }
    # \u003c keeps "</script>" and "<!--" out of the element and is still plain JSON
    return json.dumps(out, ensure_ascii=False).replace("<", "\\u003c")


def structured_data(root: str, title: str, description: str, brand: Brand, apps: list[dict],
                    page_url: str = "", locale: str = "en-US") -> dict:
    org = None
    if brand.org_name:
        org = {"@type": "Organization", "@id": f"{(brand.org_url or root).rstrip('/')}/#organization",
               "name": brand.org_name}
        if brand.org_url:
            org["url"] = brand.org_url
    page = page_url or f"{root}/"
    items = []
    for a in apps:
        app = {"@type": "MobileApplication", "@id": f"{page}#{a['id']}", "name": a["name"],
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
               "description": description, "inLanguage": locale}
    if org:
        website["publisher"] = {"@id": org["@id"]}
    graph = [website,
             {"@type": "ItemList", "@id": f"{page}#apps", "name": "Apps in the repository",
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
    pages: list = field(default_factory=lambda: ["index.html"])


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
    out.mkdir(parents=True, exist_ok=True)
    have = index_locales(data)
    for loc in brand.locales[1:]:
        lang = loc.split("-")[0].lower()
        if not any(h.split("-")[0].lower() == lang for h in have):
            log(f"fdroidkit site: no listing in {loc} in the index, /{locale_path(loc)}/ shows the en-US texts "
                f"(add metadata/<package>/{loc}/ and run `fdroid update`)")

    add_url = f"{address}?fingerprint={fp}"
    svg = qr_svg(add_url)
    if svg:
        (out / "qr.svg").write_text(svg)
    else:
        log("fdroidkit site: no QR (pip install 'fdroidkit[site]' for the qrcode package)")
    has_logo = False
    repo_icon = pick(meta.get("icon"))
    if isinstance(repo_icon, dict):
        src = repo / "repo" / repo_icon["name"].lstrip("/")
        if src.exists():
            write_icon(src, out / "icon.png")
            has_logo = True
    updated = datetime.datetime.fromtimestamp(meta.get("timestamp", 0) / 1000,
                                              datetime.timezone.utc).date().isoformat()
    pages = [(loc, "" if n == 0 else locale_path(loc) + "/") for n, loc in enumerate(brand.locales)]
    urls = [(loc, f"{root}/{sub}") for loc, sub in pages]
    first_apps: list[dict] = []
    for n, (loc, sub) in enumerate(pages):
        apps = _page(repo, out, sub, loc, brand, data, meta, address, fp, root, add_url, bool(svg),
                     has_logo, updated, urls)
        if n == 0:
            first_apps = apps
            title = brand.title or pick(meta.get("name")) or "F-Droid repository"
            description = brand.description or " ".join((pick(meta.get("description")) or "").split())
            (out / "llms.txt").write_text(llms_txt(title, description, address, fp, root, apps, brand))
    e = html.escape
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{e(u)}</loc><lastmod>{updated}</lastmod></url>\n" for _, u in urls)
        + "</urlset>\n")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {root}/sitemap.xml\n")
    for p in [out, *out.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)  # rsync carries modes to the web root
    return Result(out=out, fingerprint=fp, apps=[a["name"] for a in first_apps], qr=bool(svg),
                  pages=[str(Path(sub) / "index.html") for _, sub in pages])


def _page(repo: Path, out: Path, sub: str, locale: str, brand: Brand, data: dict, meta: dict,
          address: str, fp: str, root: str, add_url: str, qr: bool, has_logo: bool, updated: str,
          urls: list[tuple[str, str]]) -> list[dict]:
    """One landing page: the root (sub "") or /<locale>/ (sub "pl/"), assets shared at the root."""
    e = html.escape
    up = "../" * sub.count("/")
    title = brand.title or pick(meta.get("name"), locale) or "F-Droid repository"
    description = brand.description or " ".join((pick(meta.get("description"), locale) or "").split())
    author = brand.org_name or title
    apps = _apps(repo, data, brand, out, address, author, locale)
    for a in apps:  # page-relative paths, absolute ones in JSON-LD use root
        a["href_icon"] = up + a["icon"] if a["icon"] else ""
        a["href_apk"] = up + a["apk"] if a["apk"] else ""
        a["base"] = up
    obtainium_note = ""
    if any(a["obtainium"] for a in apps):
        obtainium_note = ('<p class="alt-client">No F-Droid client? <a href="https://obtainium.imranr.dev/">'
                          'Obtainium</a> can track each app straight from this repository: tap '
                          '<em>Add to Obtainium</em> next to an app below.</p>')
    qr_html = (f'<a class="qr" href="{e(add_url)}"><img src="{up}qr.svg" width="200" '
               'height="200" alt="QR code to add this repository in F-Droid"></a>') if qr else ""
    logo_html = (f'<img class="logo" src="{up}icon.png" alt="" width="64" height="64">'
                 if has_logo else "")
    alternates = switch = ""
    if len(urls) > 1:
        alternates = "\n".join(f'  <link rel="alternate" hreflang="{e(loc)}" href="{e(u)}">' for loc, u in urls)
        alternates += f'\n  <link rel="alternate" hreflang="x-default" href="{e(urls[0][1])}">'
        here = f"{root}/{sub}"
        switch = ('<nav class="langs" aria-label="Language">' + " ".join(
            f'<a href="{e(u)}" hreflang="{e(loc)}" lang="{e(loc)}"'
            + (' aria-current="page"' if u == here else "") + f">{e(loc)}</a>" for loc, u in urls) + "</nav>")
    count = len(apps)
    links = " &middot; ".join(f'<a href="{e(x["url"])}">{e(x["label"])}</a>' for x in brand.footer_links)
    org = f'<a href="{e(brand.org_url)}">{e(brand.org_name)}</a>' if brand.org_url else e(brand.org_name)
    canonical = f"{root}/{sub}"
    ld = json.dumps(structured_data(root, title, description, brand, apps, canonical, locale),
                    ensure_ascii=False).replace("</", "<\\/")
    page = string.Template(_template(repo, brand, "index.html")).safe_substitute(
        title=e(title), description=e(description), canonical=e(canonical),
        logo=logo_html, address=e(address), add_url=e(add_url), qr=qr_html,
        fingerprint=fp, fingerprint_grouped=group_fp(fp),
        app_heading=f"{count} app{'s' if count != 1 else ''} in the repository",
        apps=_app_rows(apps), obtainium_note=obtainium_note, org=org, footer_links=links, updated=updated,
        jsonld=ld, appdata=app_data(apps), lang=e(locale), base=up, alternates=alternates, lang_switch=switch)
    (out / sub).mkdir(parents=True, exist_ok=True)
    (out / sub / "index.html").write_text(page)
    if not sub:
        if brand.base_css:
            css = string.Template(_template(repo, brand, "site.css")).safe_substitute(accent=brand.accent) + badge_css(brand)
        else:
            css = "/* fdroidkit: site.base_css is false, title badge rules only */\n" + TBADGE_BASE + badge_css(brand)
        (out / "site.css").write_text(css)
    return apps
