# Changelog

## Unreleased
- **`publish` works** (`--source play|file`, `--dry-run`): ported from the script that ships
  OxiSoft's apps to fdroid.oxisoft.io, every path a parameter. Play source waits for Play to
  generate the universal APK. Public files written under umask 022 (a 0600 entry.jar is a 403).
- Fixture tests: generated one-key, rotated-key, pairip and stranger APKs, stand-in aapt2 /
  apksigner / fdroid, deploy into a local web root and read-back over HTTP.

## 0.0.1 (unreleased)
- Repo skeleton: package layout, CLI surface (`init`, `add-app`, `publish`, `site`, `verify`),
  APK checks (identity, signer pins incl. rotated keys, versionCode, Play automatic protection)
  with tests, Gitea and GitHub composite actions, docs, examples, CI.
