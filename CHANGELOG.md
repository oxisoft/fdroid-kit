# Changelog

## Unreleased

- **`fdroidkit doctor`**: one command that audits a live repo: TLS expiry, entry.jar signature,
  index-v2.json vs the signed entry.json, index-v1.jar signer, index age, landing page
  fingerprints, add-repo link and QR, signer pins (with `--repo`), Play automatic protection in
  the newest APK of each app (HTTP Range, no full download) and versionCode lag. Table or
  `--json`, exit 1 on any failure.
- Node 24 ready: the GitHub action and examples use `actions/setup-python@v7`,
  `actions/checkout@v7`, `actions/upload-pages-artifact@v5` (with `include-hidden-files`, so
  `.nojekyll` still ships) and `actions/deploy-pages@v5`. GitHub deprecated the Node 20 versions.

## 0.1.0 (2026-11-19)

First release. Everything you need to run an F-Droid repo for apps that are also on Google Play:

- Five commands: `init` (index key + a secret-free `config.yml`), `add-app` (metadata + Play
  signer pin), `publish` (Play or file source, hard checks, update, deploy, live read-back),
  `site` (landing page with fingerprint and add-repo QR from the signed index) and `verify`.
- Two CI targets: a Gitea / Forgejo composite action for your own server, and a GitHub Actions
  + GitHub Pages action that needs no server at all.
- Pin actions to `@v0.1.0`; the docs and examples do.

Details of the work that went in:
- **GitHub Actions + GitHub Pages target** (`.github/actions/publish`): index key from the
  `FDROID_KEYSTORE_B64` / `FDROID_KEYSTORE_PASS` secrets, written under `$RUNNER_TEMP` with umask 077
  and shredded in an `if: always()` step; APKs restored from the live index (none in git); landing
  page rendered and verified; Pages artifact uploaded. `examples/github-pages.yml` publishes,
  deploys and checks the live site; demo at oxisoft/fdroid-kit-example. Kit CI runs the action on a
  fixture. `publish --no-deploy` for hosts that take an artifact.
- Signer parsing understands Android build-tools 37 (`V3.0 Signer: certificate ...`; found on the
  GitHub runner, which ships 37.0.0): before, every APK was refused with "no signer".
- `site` with a relative `--repo` no longer fails its own check ("NO fingerprint found"): it
  scanned `<repo>/<repo>/site`.
- `verify` compares the QR by its modules, not bytes: the same QR serialises differently on
  Python 3.12 and 3.13, so a page built on one failed the live check run on the other.
- **Gitea / Forgejo action works** (`actions/gitea`): installs fdroidkit from the action's own
  checkout into a throwaway venv and runs `publish` with every option as an input. Secrets only as
  env: `PLAY_SA_JSON` goes to a 0600 temp file that is removed afterwards. Kit CI runs it on a
  fixture repo (dry run, then a publish with the stand-in fdroid).
- **`init` and `add-app` work**. `init` creates the index key with keytool (passphrase only from
  an environment variable, never argv or a prompt) and a `config.yml` with `{env: NAME}`
  passphrases, so it can be committed; repo and archive icons land where fdroidserver reads them.
  `add-app --discover-signer` pins the signer read from a real APK after an identity, Play
  signer (`O=Google Inc.`) and pairip check; `--pin` adds a rotated key without touching the rest.
- `publish` stops before `fdroid update` when config.yml reads an unset environment variable
  (fdroidserver would crash with a TypeError deep in keytool).
- **`site` and `verify` work**: a neutral, CSP-clean landing page from the signed index
  (index-v2.json is refused unless entry.json vouches for it), fingerprint and add-repo QR (SVG)
  from the certificate that signed entry.jar, brand via `fdroidkit.json`, llms.txt, sitemap,
  JSON-LD. `verify` checks every printed fingerprint and the QR against the local or live key.
- **`publish` works** (`--source play|file`, `--dry-run`): ported from the script that ships
  OxiSoft's apps to fdroid.oxisoft.io, every path a parameter. Play source waits for Play to
  generate the universal APK. Public files written under umask 022 (a 0600 entry.jar is a 403).
- Fixture tests: generated one-key, rotated-key, pairip and stranger APKs, stand-in aapt2 /
  apksigner / fdroid, deploy into a local web root and read-back over HTTP.

- Repo skeleton (0.0.1, never tagged): package layout, CLI surface (`init`, `add-app`, `publish`, `site`, `verify`),
  APK checks (identity, signer pins incl. rotated keys, versionCode, Play automatic protection)
  with tests, Gitea and GitHub composite actions, docs, examples, CI.
