# Changelog

## Unreleased
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

## 0.0.1 (unreleased)
- Repo skeleton: package layout, CLI surface (`init`, `add-app`, `publish`, `site`, `verify`),
  APK checks (identity, signer pins incl. rotated keys, versionCode, Play automatic protection)
  with tests, Gitea and GitHub composite actions, docs, examples, CI.
