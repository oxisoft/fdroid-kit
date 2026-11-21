# fdroid-kit

Run your own F-Droid repository for apps that are also on Google Play.

`fdroidkit` takes the APK that Play App Signing already produced for a release, checks it hard,
adds it to your F-Droid repo, deploys it and reads it back from the live index. Users can then
install from F-Droid and update from Play, or the other way round, because both channels ship the
same Google-signed APK.

> Status: **v0.1.4**. Six commands (with `doctor`) and two CI actions, tested on real Play-signed
> and self-signed (GitHub Release) APKs. See the [CHANGELOG](CHANGELOG.md).

## Quickstart

On your own server (Gitea / Forgejo runner, nginx serving the repo). Needs Python 3.10+, a JDK
(`keytool`), the Android build-tools and [fdroidserver](https://f-droid.org/docs/Installing_the_Server_and_Repo_Tools/).

```
pip install "fdroidkit[site] @ git+https://github.com/oxisoft/fdroid-kit@v0.1.4"
export FDROID_KEYSTORE_PASS="$(head -c 32 /dev/urandom | base64)"   # store it in your password manager
fdroidkit --repo fdroid init --name "Example Apps" --url https://fdroid.example.com/fdroid/repo
fdroidkit --repo fdroid add-app com.example.app --discover-signer universal.apk
fdroidkit --repo fdroid publish com.example.app 42 1.4.0 --source file --apk universal.apk --dry-run
fdroidkit --repo fdroid publish com.example.app 42 1.4.0 --source file --apk universal.apk
fdroidkit --repo fdroid site
```

`universal.apk` is the Play-signed universal APK from Play Console (App bundle explorer). What you
should see, in short:

```
init      fingerprint 2c3374e1...   BACK UP the keystore and the passphrase now
add-app   signer found: b3afc17d...  wrote metadata/com.example.app.yml with 1 pinned signer(s)
publish   signer pinned OK (1 signer) / no pairip licence check / dry run: all checks passed
publish   + fdroid update ... published com.example.app 1.4.0 (42)
site      qr.svg: ok / verdict: all match
```

Set `serverwebroot` in `fdroid/config.yml` so `publish` deploys and reads the live index back
(without it, add `--no-deploy --no-readback` for a local try). Then automate it with the
[Gitea / Forgejo action](#in-ci).

On GitHub with no server: copy [examples/github-pages.yml](examples/github-pages.yml) into a new
repo, run `init` once locally, put the key into the `FDROID_KEYSTORE_B64` and `FDROID_KEYSTORE_PASS`
secrets, commit `config.yml` and `metadata/`, enable Pages (source: GitHub Actions) and run the
workflow. Step by step: [docs/github-pages.md](docs/github-pages.md); working demo:
[oxisoft/fdroid-kit-example](https://github.com/oxisoft/fdroid-kit-example).

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
fdroidkit publish com.example.app --source github-release --github-repo owner/name [--dry-run]
fdroidkit site          # landing page with fingerprint and add-repo QR, from the signed index
fdroidkit verify        # every fingerprint on the page matches the index key
fdroidkit doctor --url https://fdroid.example.com/fdroid/repo   # audit the live repo
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

```
fdroidkit add-app io.oxisoft.oxitalk --from-github oxisoft/oxitalk --asset '*-android.apk'
```

`--from-github` is the same for apps you sign yourself and attach to GitHub Releases
(IzzyOnDroid style): it downloads the APK of the latest release (or `--tag`), pins its signer
without the Play DN check, and saves the source in `fdroidkit.json`:

```json
{"apps": {"io.oxisoft.oxitalk": {"source": {"type": "github-release", "repo": "oxisoft/oxitalk",
                                            "asset": "*-android.apk"}}}}
```

Optional `"token": {"env": "NAME"}` names the variable holding a GitHub token (default
`$GITHUB_TOKEN`, only sent when set): public repos need none, a token lifts the anonymous rate
limit and reads private repos. A literal token in the file is refused.

`--locale pl` (repeatable) also scaffolds `metadata/<package>/pl/` next to `en-US/` for a
translated listing. On an app that is already set up, `--locale` alone only adds the folder.

### publish

Every step is a hard stop:
versionCode higher than the published one, identity (aapt2), every signer pinned
(apksigner, rotation aware, Source Stamp ignored), no `com.pairip` in any dex; then copy in,
bump the metadata, `fdroid update`, `fdroid deploy`, and read the versionCode back from the
live `index-v2.json`. `--dry-run` stops after the checks.

`--source github-release` takes the release asset matching `--asset` (default `*.apk`, exactly
one must match) from the latest non-prerelease release or `--tag`; repo and asset default to
`fdroidkit.json`. The version comes from the APK itself, so versionCode and versionName are
optional; the no-downgrade, no-duplicate check runs after the download. The pinned signer is
what makes this safe: a release asset proves nothing about who built it. The repo flag is
`--github-repo`, because `--repo` already names the F-Droid working tree.

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

Each app also gets an **Add to Obtainium** link, for people who use
[Obtainium](https://obtainium.imranr.dev/) instead of an F-Droid client. It imports the app with
Obtainium's F-Droid third-party repo source, so updates come from your signed index. Set
`"obtainium": "redirect"` to send visitors without Obtainium to its install prompt (Obtainium's own
redirect page, a third party), or `false` to hide the link.

**Translated listings.** The index carries a name, summary and description per language
(`metadata/<package>/<locale>/`). List the languages in `site.locales`, first one on the root page:

```json
{"site": {"locales": ["en-US", "pl", "ru"]}}
```

Every other locale gets its own page (`/pl/`, `/ru/`, lower case) with `<html lang>`, a canonical
URL, `hreflang` alternates (plus `x-default`), a plain-link language switcher (no script, CSP
safe) and an entry in sitemap.xml. A field a language lacks falls back to the same language in
another region (`pl-PL` for `pl`), then to en-US. A locale the index has no text in at all still
renders, in en-US, with a warning. The page's own words (headings, steps) stay English; only the
listings are translated. Icons, APKs and the QR stay at the root, linked relative from each page.
A custom `template` should use `$lang`, `${base}` before root asset paths, `$alternates` in the
head and `$lang_switch` in the body. Default: en-US only, one page, as before.

### doctor

```
fdroidkit doctor --url https://fdroid.example.com/fdroid/repo [--repo /srv/fdroid] [--json]
```

Audits the repo your users actually reach, not your working tree: the TLS certificate (more than
14 days left), the `entry.jar` signature, the served index-v2.json against the signed entry.json,
the index-v1.jar signer, the index age, every fingerprint on the landing page, the add-repo link
and qr.svg, each app's signers (pinned in `--repo` metadata, same across versions), Play automatic
protection in the newest APK of each app (only the dex entries are fetched, by HTTP Range), and
whether the live versionCode lags the local metadata or index. Exit 0 healthy (warnings allowed),
1 any failure, 3 the repo could not be read. Details: [docs/doctor.md](docs/doctor.md).

## In CI

Gitea / Forgejo:

```yaml
- uses: https://github.com/oxisoft/fdroid-kit/actions/gitea@v0.1.4
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
- uses: oxisoft/fdroid-kit/.github/actions/publish@v0.1.4
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
- [doctor](docs/doctor.md): what each live check reads and how to fix a failure.

## Roadmap

- v0.1: `publish` (Play and file sources), `site` + `verify`, `init`, `add-app`, both actions.
- v0.1.1: `doctor` (live repo audit), Obtainium link.
- v0.1.2: GitHub Release source with self-signed pins (`--source github-release`, `add-app --from-github`).
- v0.1.3: fix for rotated Play signing keys on build-tools 37.
- v0.1.4: translated listings on the landing page (`site.locales`, `add-app --locale`).

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
