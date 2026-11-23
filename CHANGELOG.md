# Changelog

## 0.1.7 (2026-11-23)

- **`doctor` checks the storage budget.** It sums the APK sizes that the signed indexes of
  `repo/` and `archive/` list (no download) and compares them with `--budget`: `github-pages`
  (the 1 GB Pages site limit), a byte count, or `500M` / `2G`. Warning from 80%, failure from
  100%; without a budget only the total is reported. With `--repo` it also reads
  `archive_older` and warns when the steady state (every app holding that many versions) would
  pass the budget, naming the `archive_older` that fits. `--json` carries a `storage` block with
  the biggest apps first, the job summary shows them as a table, and both doctor actions take a
  `budget` input. On the demo repo: 434 MB today, about 1.3 GB at `archive_older: 3`.

## 0.1.6 (2026-11-23)

- **Community health files and a migration guide.** `CONTRIBUTING.md` (dev setup, fixture and
  no-real-keys rules, PR checklist), `SECURITY.md` (private reports via GitHub security
  advisories; scope: index key handling, secrets in the actions, check bypasses), issue forms
  (bug report asks for the fdroidkit and fdroidserver versions and `doctor --json`) and a PR
  template. `docs/migrating.md` moves a hand-run fdroidserver repo to the kit and keeps its index
  key, tested end to end on a repo made with `fdroid init`. `docs/signing-model.md` no longer
  claims a keystore-in-tree guard that `publish` does not have.

- **`doctor` as a reusable action, for a scheduled audit of your own repo.**
  `.github/actions/doctor` (GitHub: job summary, JSON report artifact, annotations) and
  `actions/doctor` (Gitea / Forgejo) run `fdroidkit doctor` and fail the run on a failed check
  (`fail-on-warn` for warnings too). Read only, no secrets. Example:
  `examples/doctor-weekly.yml`.
- **Weekly canary against the newest upstream tools.** `canary.yml` installs the newest
  fdroidserver from PyPI and the newest Android build-tools, runs the tests, builds and signs a
  real APK (one key, then a rotated key with a v3.1 lineage) to check the signer parser against
  the real apksigner, publishes it end to end through the real `fdroid update`, and runs doctor on
  fdroid.oxisoft.io and the fdroid-kit-example demo. build-tools 37 broke signer parsing once
  (0.1.3) and was found by a release; now a red canary finds it first.

## 0.1.5 (2026-11-21)

- **`fdroidkit refresh`: metadata-only changes reach the live index.** A new translation, an
  edited description or new screenshots used to stay invisible until some app shipped a new
  APK, because `publish` refuses a duplicate versionCode and was the only path that ran
  `fdroid update` + deploy. `refresh` rebuilds the index, runs the site hooks, deploys and reads
  the new index timestamp back from the live repo; no APK added, no version bump. It refuses to
  run when a published app has no APK locally (the new index would drop it); `--restore` fetches
  the missing APKs from the live index first.
- `fdroidkit restore`: just the download step, now shared with the GitHub Pages action.
- Both actions take `source: none` for a refresh; `package` is optional then.

## 0.1.4 (2026-11-21)

- **Translated listings on the landing page.** `site.locales` in `fdroidkit.json` (default
  `["en-US"]`, so existing sites do not change) renders one page per extra language (`/pl/`,
  `/ru/`) from the localized names, summaries and descriptions in the signed index, with
  `html lang`, canonical, `hreflang` + `x-default`, a no-script language switcher and every page
  in sitemap.xml. Missing fields fall back per field: same language other region, then en-US.
  `verify` checks the fingerprints on every page.
- `add-app --locale xx` (repeatable) scaffolds `metadata/<package>/<xx>/`; alone it adds a
  language to an app that is already set up.

## 0.1.3 (2026-11-21)

- **Fix: rotated Play signing keys on Android build-tools 37.** After a key rotation apksigner 37
  prints `V3.1 Signer: (minSdkVersion=33, ...) certificate ...`, with the colon before the SDK
  range. 0.1.2 and earlier did not match that line, so `publish` refused such APKs with
  "apksigner reported no signer" on GitHub's runners (build-tools 37). Found publishing Habit
  Observer into the demo; tests now carry the verbatim output.

## 0.1.2 (2026-11-21)

- **GitHub Release source** for apps you sign yourself (IzzyOnDroid style):
  `fdroidkit publish <package> --source github-release --github-repo owner/name [--asset GLOB]
  [--tag TAG]` downloads the release APK (latest non-prerelease by default), reads versionCode and
  versionName from it, refuses a downgrade or duplicate, checks identity, the pinned signer and
  pairip, then publishes like the other sources. `add-app --from-github owner/name` pins the
  self-signed signer from the release APK and saves the source in `fdroidkit.json`
  (`apps.<package>.source`), so later publishes need only the package. Optional token via
  `{"env": NAME}` (default `$GITHUB_TOKEN`), never required for public repos.
- Both actions take `source: github-release` with `github-repo`, `asset` and `tag` inputs;
  `version-code` and `version-name` are optional for that source.
- Docs: GitHub Pages limits for big APKs (the 100 MB file limit is git only; the 1 GB site cap,
  10 minute deploy and 100 GB/month bandwidth are what count). Actions and examples pin `@v0.1.2`.

## 0.1.1 (2026-11-20)

- **Add to Obtainium** on the landing page: every app gets a link that imports it into
  [Obtainium](https://obtainium.imranr.dev/) with the F-Droid third-party repo source and the
  app id preset (`obtainium://app/<config>`, per the Obtainium deep link docs), plus a one-line
  note under the add-repo QR. `site.obtainium` in fdroidkit.json: `"direct"` (default, no third
  party), `"redirect"` (Obtainium's redirect page, which offers to install Obtainium) or `false`.
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
