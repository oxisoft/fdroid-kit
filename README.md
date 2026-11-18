# fdroid-kit

Run your own F-Droid repository for apps that are also on Google Play.

`fdroidkit` takes the APK that Play App Signing already produced for a release, checks it hard,
adds it to your F-Droid repo, deploys it and reads it back from the live index. Users can then
install from F-Droid and update from Play, or the other way round, because both channels ship the
same Google-signed APK.

> Status: **v0.0.1, pre-release.** All five commands work and are tested; the CI actions and the
> first tagged release (v0.1) come next.

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

### init

```
export FDROID_KEYSTORE_PASS="$(head -c 32 /dev/urandom | base64)"   # keep it in your password manager / CI secrets
fdroidkit --repo /srv/fdroid init --name "Example Apps" --url https://fdroid.example.com/fdroid/repo
```

Creates the index key (RSA 4096, PKCS12, `~/.config/fdroidkit/<host>.p12` by default, outside
the repo, mode 0600) and a `config.yml` that reads the passphrase as `keystorepass: {env:
FDROID_KEYSTORE_PASS}`, so the config holds no secret and can be committed. Also writes the repo
and archive icon where fdroidserver looks for them (a missing one is silently replaced by its
placeholder), a `.gitignore` for keys and passphrase files, and prints the fingerprint. It never
re-inits a repo or overwrites a key. `--pass-env NAME` picks another variable.

### add-app

```
fdroidkit add-app com.example.app --discover-signer universal.apk
fdroidkit add-app com.example.app --pin <sha256>          # add a pin, e.g. after a key rotation
```

`--discover-signer` reads the signer from a real APK and pins it in `AllowedAPKSigningKeys`, after
checking that the APK is that package, that every signer is Play App Signing (`O=Google Inc.` in
the certificate DN; pinning your upload key by mistake would refuse every Play-signed APK) and
that it has no Play automatic protection. Take the universal APK from Play Console (App bundle
explorer). `--not-play` skips the DN check if you sign APKs yourself. On an existing app it only
adds missing pins and leaves every other field alone.

### publish

Every step is a hard stop:
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
- uses: https://github.com/oxisoft/fdroid-kit/actions/gitea@main
  env: { PLAY_SA_JSON: "${{ secrets.PLAY_SA_JSON }}" }
  with: { package: com.example.app, version-code: "42", version-name: "1.4.0", repo-path: /srv/fdroid }
```

Inputs: `source` (play | file), `apk`, `live-url`, `after-update` (one command per line),
`rsync-site`, `readback`, `dry-run`, `build-tools`, `fdroid`, `python`. Secrets only as
environment variables: `PLAY_SA_JSON` (or `PLAY_SA_FILE`) and the passphrase names your
`config.yml` reads with `{env: NAME}`. The action installs fdroidkit from its own checkout into a
throwaway venv, so the runner needs only Python 3.10+, the Android build-tools and fdroidserver.

GitHub Actions + GitHub Pages (no server at all):

```yaml
- uses: oxisoft/fdroid-kit/.github/actions/publish@main
  env:
    FDROID_KEYSTORE_B64: ${{ secrets.FDROID_KEYSTORE_B64 }}
    FDROID_KEYSTORE_PASS: ${{ secrets.FDROID_KEYSTORE_PASS }}
  with: { package: com.example.app, version-code: "42", version-name: "1.4.0", source: file, apk: app-release.apk }
```

The index key exists only under `$RUNNER_TEMP` (umask 077) for the job and is shredded in an
`if: always()` step; APKs are restored from the live site, so they never go into git. Then
`actions/deploy-pages`. Setup and limits: [docs/github-pages.md](docs/github-pages.md); a live
demo: [oxisoft/fdroid-kit-example](https://github.com/oxisoft/fdroid-kit-example).

Full examples in [examples/](examples/).

## Docs

- [Signing model](docs/signing-model.md): the app key is never in the kit, the index key is the only secret.
- [Play automatic protection](docs/play-protection.md)
- [Setup traps](docs/setup-traps.md)
- [GitHub Pages](docs/github-pages.md): repo on a hosted runner + Pages, key from secrets.

## Roadmap

- v0.1: `publish` (Play and file sources), `site` + `verify`, `init`, `add-app`, both actions.
- Later: GitHub Release source with self-signed pins, Obtainium link,
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
