# fdroid-kit

Run your own F-Droid repository for apps that are also on Google Play.

`fdroidkit` takes the APK that Play App Signing already produced for a release, checks it hard,
adds it to your F-Droid repo, deploys it and reads it back from the live index. Users can then
install from F-Droid and update from Play, or the other way round, because both channels ship the
same Google-signed APK.

> Status: **v0.0.1, skeleton.** The command line surface is fixed and the APK checks are in place
> and tested; the commands land in v0.1. Not ready for production use yet.

## Why another F-Droid tool

Existing templates assume you sign the APK yourself. If your app is on Play with Play App Signing,
a self-signed F-Droid build strands users: Android refuses to update across signing keys. fdroidkit
is built around the Play-signed universal APK and the traps that come with it:

- **Signer pins**: every signer in the APK must be pinned in the metadata, including rotated keys.
- **Play automatic protection guard**: Play can inject a licence wall (`com.pairip`) that breaks
  every install not made by Play. A test launch proves nothing, so fdroidkit reads the dex files.
- **Monotonic versionCode** and a **live read-back** of `index-v2.json` after every deploy.
- **Gitea / Forgejo Actions** as well as GitHub Actions.

It wraps [fdroidserver](https://gitlab.com/fdroid/fdroidserver), it does not replace it.

## Commands (v0.1)

```
fdroidkit init --name "Example Apps" --url https://fdroid.example.com/fdroid/repo
fdroidkit add-app com.example.app --discover-signer app.apk
fdroidkit publish com.example.app 42 1.4.0 --source play [--dry-run]
fdroidkit site          # landing page with fingerprint and add-repo QR, from the signed index
fdroidkit verify        # every fingerprint on the page matches the index key
```

### publish

Works today (`init` and `add-app` follow). Every step is a hard stop:
versionCode higher than the published one, identity (aapt2), every signer pinned
(apksigner, rotation aware, Source Stamp ignored), no `com.pairip` in any dex; then copy in,
bump the metadata, `fdroid update`, `fdroid deploy`, and read the versionCode back from the
live `index-v2.json`. `--dry-run` stops after the checks.

```
fdroidkit --repo /srv/fdroid publish com.example.app 42 1.4.0 --source play --play-sa-file sa.json
fdroidkit --repo /srv/fdroid publish com.example.app 42 1.4.0 --source file --apk app.apk \
    --after-update "fdroidkit site" --rsync-site host:/var/www/fdroid/
```

Tools are found on their own: build-tools from `--build-tools`, `$ANDROID_HOME`, `sdk_path` in
config.yml or `~/android`; `fdroid` on PATH; the live URL from `repo_url`. Exit codes: 0 ok,
1 the APK was refused, 3 setup error. `--source play` needs `pip install 'fdroidkit[play]'`.

### site and verify

```
fdroidkit --repo /srv/fdroid site            # writes /srv/fdroid/site/
fdroidkit --repo /srv/fdroid verify --live   # key from the live entry.jar
```

`site` renders a static landing page (index.html, site.css, qr.svg, app icons, llms.txt,
sitemap.xml, robots.txt) from `repo/index-v2.json`, and only after checking its SHA-256 against
the signed `entry.json`: the page cannot list an app the repo does not serve. The fingerprint and
the add-repo QR come from the certificate that signed `entry.jar` (read with `keytool`), never from
a config value, so no keystore password is needed. The page passes a strict CSP (no inline script
or style). `site` ends with a `verify`.

`verify` finds every SHA-256 fingerprint on the page and in llms.txt, compares each with the key
(local `repo/entry.jar`, or `--live [URL]` for the deployed one) and regenerates the QR from the
page's add-repo link to compare it with qr.svg. Exit 1 on any mismatch: run it before every deploy.

Branding is optional, in `<repo>/fdroidkit.json` under `site` (title, description, site_url,
accent, org_name, org_url, footer_links, per-app badges, a custom `template`): see
[examples/fdroidkit.json](examples/fdroidkit.json). The QR needs `pip install 'fdroidkit[site]'`
(qrcode; Pillow also downscales the icons).

## In CI

Gitea / Forgejo:

```yaml
- uses: https://g.oxisoft.io/oxisoft/fdroid-kit/actions/gitea@v0.1.0
  with: { package: com.example.app, version-code: "42", version-name: "1.4.0", repo-path: /srv/fdroid }
```

GitHub:

```yaml
- uses: oxisoft/fdroid-kit/.github/actions/publish@v0.1.0
  with: { package: com.example.app, version-code: "42", version-name: "1.4.0" }
```

Full examples in [examples/](examples/).

## Docs

- [Signing model](docs/signing-model.md): the app key is never in the kit, the index key is the only secret.
- [Play automatic protection](docs/play-protection.md)
- [Setup traps](docs/setup-traps.md)

## Roadmap

- v0.1: `publish` (Play and file sources), `site` + `verify`, `init`, `add-app`, both actions.
- Later: GitHub Release source with self-signed pins, GitHub Pages deploy, Obtainium link,
  a `doctor` command that audits a live repo.

## In production

Extracted from [fdroid.oxisoft.io](https://fdroid.oxisoft.io), the F-Droid repo of
[OxiSoft](https://oxisoft.io)'s apps, which will run on this kit.

## Development

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

Source: [g.oxisoft.io/oxisoft/fdroid-kit](https://g.oxisoft.io/oxisoft/fdroid-kit), mirrored to
[github.com/oxisoft/fdroid-kit](https://github.com/oxisoft/fdroid-kit). Issues and pull requests
are welcome on GitHub.

## License

MIT, see [LICENSE](LICENSE).
