"""`fdroidkit init`: a new repo with its index key and a config.yml that holds no secret.

`fdroid init` writes the keystore passphrase into config.yml and ignores a passphrase on
stdin, so the kit does the same steps itself:

  1. refuse to touch a repo that already has a config.yml (it would clobber the key)
  2. take the passphrase from an environment variable, never from argv or a prompt
  3. generate the index key with keytool (RSA 4096, PKCS12, same as fdroidserver), the
     passphrase passed as `-storepass:env`, so it never shows in a process list
  4. write config.yml with `keystorepass: {env: NAME}` (fdroidserver reads it lazily
     from the environment), the repo + archive icon set, archive_older
  5. .gitignore for keys and passphrase files, metadata/ repo/ archive/ folders
  6. print the index key fingerprint and the backup reminder
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PASS_ENV = "FDROID_KEYSTORE_PASS"
ICON_NAME = "icon.png"

GITIGNORE = """\
# fdroidkit: never commit the index key or its passphrase (docs/signing-model.md)
*.p12
*.jks
*.keystore
keystore*
*.pass
# fdroidserver work dirs
tmp/
.cache/
unsigned/
"""

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InitError(Exception):
    """Setup problem; nothing secret in the message."""


@dataclass
class InitOptions:
    repo: Path
    name: str
    url: str
    description: str = ""
    pass_env: str = DEFAULT_PASS_ENV
    keystore: Path | None = None
    alias: str | None = None
    dname: str | None = None
    icon: Path | None = None
    sdk_path: str | None = None
    archive_older: int = 3
    keytool: str | None = None


@dataclass
class InitResult:
    config: Path
    keystore: Path
    fingerprint: str
    icon_placeholder: bool


def _yaml_str(s: str) -> str:
    """A YAML double-quoted scalar (JSON strings are valid YAML)."""
    import json
    return json.dumps(s, ensure_ascii=False)


def _block(s: str, indent: str = "    ") -> str:
    lines = s.strip("\n").splitlines() or [""]
    return "|\n" + "".join(f"{indent}{ln}\n" if ln.strip() else "\n" for ln in lines)


def placeholder_png(size: int = 512, rgb: tuple[int, int, int] = (0x1F, 0x6F, 0xEB)) -> bytes:
    """A plain square PNG, stdlib only. Better than fdroidserver's broken default icon."""
    row = b"\x00" + bytes(rgb) * size
    raw = row * size

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def is_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def default_alias(url: str) -> str:
    host = re.sub(r"^[a-z]+://", "", url).split("/")[0].split(":")[0]
    return host or "fdroid"


def render_config(o: InitOptions, keystore: Path, alias: str, dname: str) -> str:
    base = o.url.rstrip("/")
    archive_url = re.sub(r"/repo$", "/archive", base) if base.endswith("/repo") else base + "-archive"
    desc = o.description or f"{o.name} apps."
    out = [
        "# Written by fdroidkit init. Holds NO secret: the passphrases come from the",
        f"# environment ({o.pass_env}), so this file can be committed.",
        "# Reference: https://gitlab.com/fdroid/fdroidserver/-/blob/master/examples/config.yml",
    ]
    if o.sdk_path:
        out.append(f"sdk_path: {_yaml_str(o.sdk_path)}")
    out += [
        "",
        f"repo_url: {_yaml_str(base)}",
        f"repo_name: {_yaml_str(o.name)}",
        f"repo_icon: {ICON_NAME}",
        f"repo_description: {_block(desc)}",
        f"archive_older: {o.archive_older}",
        f"archive_url: {_yaml_str(archive_url)}",
        f"archive_name: {_yaml_str(o.name + ' Archive')}",
        f"archive_icon: {ICON_NAME}",
        f"archive_description: {_block('Earlier versions of ' + o.name + ' apps.')}",
        "# Index signing key (signs the index, never the apps). Back it up: losing it",
        "# makes every user re-add the repo under a new fingerprint.",
        f"keystore: {_yaml_str(str(keystore))}",
        f"repo_keyalias: {_yaml_str(alias)}",
        f"keydname: {_yaml_str(dname)}",
        f"keystorepass: {{env: {o.pass_env}}}",
        f"keypass: {{env: {o.pass_env}}}",
        "",
        "# Deploy target, e.g. rsync over an ssh config alias (a user@host:port form",
        "# does not work here, see docs/setup-traps.md):",
        "# serverwebroot:",
        "#   - url: myhost:/var/www/fdroid.example.com/fdroid",
        "",
    ]
    return "\n".join(out)


def find_keytool(explicit: str | None) -> str:
    cands = [explicit, shutil.which("keytool")]
    jh = os.environ.get("JAVA_HOME")
    if jh:
        cands.append(str(Path(jh) / "bin" / "keytool"))
    for c in filter(None, cands):
        if Path(c).exists() or shutil.which(c):
            return c
    raise InitError("keytool not found (install a JDK, set JAVA_HOME or use --keytool)")


def cert_fingerprint(keytool: str, keystore: Path, alias: str, pass_env: str) -> str:
    """SHA-256 of the DER certificate: the fingerprint F-Droid clients pin."""
    r = subprocess.run([keytool, "-exportcert", "-keystore", str(keystore), "-alias", alias,
                        "-storetype", "pkcs12", "-storepass:env", pass_env],
                       capture_output=True)
    if r.returncode != 0 or len(r.stdout) < 20:
        raise InitError(f"keytool -exportcert failed: {r.stderr.decode(errors='replace').strip()[-500:]}")
    return hashlib.sha256(r.stdout).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def init(o: InitOptions, log=print) -> InitResult:
    repo = o.repo.resolve()
    cfg = repo / "config.yml"
    if cfg.exists():
        raise InitError(f"{cfg} exists: refusing to re-init (it would replace the index key)")
    if not _ENV_NAME_RE.match(o.pass_env):
        raise InitError(f"--pass-env {o.pass_env!r} is not a valid environment variable name")
    pw = os.environ.get(o.pass_env, "")
    if not pw:
        raise InitError(
            f"${o.pass_env} is not set. Generate a passphrase, keep it in your password manager or CI "
            f"secrets, then export it, e.g.: export {o.pass_env}=\"$(head -c 32 /dev/urandom | base64)\"")
    if len(pw) < 6:
        raise InitError(f"${o.pass_env} is shorter than 6 characters (keytool refuses it)")
    if not re.match(r"^https?://", o.url):
        raise InitError(f"--url must be http(s): {o.url}")
    if o.icon is not None and not is_png(Path(o.icon).read_bytes()[:8]):
        raise InitError(f"--icon {o.icon} is not a PNG")

    alias = o.alias or default_alias(o.url)
    dname = o.dname or f"CN={alias}, OU=F-Droid"
    keystore = (o.keystore or Path.home() / ".config" / "fdroidkit" / f"{alias}.p12").expanduser().absolute()
    if keystore.exists():
        raise InitError(f"{keystore} exists: refusing to overwrite an index key (pass another --keystore)")
    keytool = find_keytool(o.keytool)

    old = os.umask(0o077)
    try:
        keystore.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [keytool, "-genkeypair", "-keystore", str(keystore), "-alias", alias,
             "-keyalg", "RSA", "-keysize", "4096", "-sigalg", "SHA256withRSA", "-validity", "10000",
             "-storetype", "pkcs12", "-storepass:env", o.pass_env, "-keypass:env", o.pass_env,
             "-dname", dname, "-J-Duser.language=en"],
            capture_output=True, text=True, env={**os.environ, "LC_ALL": "C.UTF-8"})
        if r.returncode != 0:
            raise InitError(f"keytool -genkeypair failed: {(r.stderr or r.stdout).strip()[-500:]}")
        keystore.chmod(0o600)
    finally:
        os.umask(old)

    # Everything below is served or committed: public modes (the entry.jar 403 trap).
    os.umask(0o022)
    try:
        repo.mkdir(parents=True, exist_ok=True)
        for d in ("metadata", "repo/icons", "archive/icons"):
            (repo / d).mkdir(parents=True, exist_ok=True)
        icon = Path(o.icon).read_bytes() if o.icon else placeholder_png()
        # fdroidserver reads repo_icon/archive_icon relative to the repo ROOT and copies it
        # into repo/icons/ on every update; a missing root file is silently replaced by
        # its own placeholder (the "placeholder repo icon" trap), so the root copy is the one.
        for d in (".", "repo/icons", "archive/icons"):
            (repo / d / ICON_NAME).write_bytes(icon)
        cfg.write_text(render_config(o, keystore, alias, dname))
        cfg.chmod(0o600)  # no secret inside, but fdroidserver warns on every run otherwise
        gi = repo / ".gitignore"
        existing = gi.read_text() if gi.exists() else ""
        if "fdroidkit: never commit" not in existing:
            gi.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + GITIGNORE)
        fp = cert_fingerprint(keytool, keystore, alias, o.pass_env)
    finally:
        os.umask(old)

    log(f"repo {repo}: config.yml (no secret, passphrase from ${o.pass_env}), metadata/, repo/, archive/")
    log(f"index key {keystore} (0600), alias {alias}")
    log(f"fingerprint {fp}")
    if _inside(keystore, repo):
        log("WARNING: the index key sits inside the repo tree; .gitignore covers it, keep it out of any upload")
    if not o.icon:
        log(f"repo icon: a plain placeholder at {ICON_NAME} (repo root); replace it with your own 512 px PNG")
    log("BACK UP the keystore and the passphrase now: losing either makes every user re-add the repo.")
    log("next: fdroidkit add-app <package> --discover-signer <apk>")
    return InitResult(cfg, keystore, fp, not o.icon)
