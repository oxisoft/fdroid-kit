# doctor: audit a live repo

```
fdroidkit doctor --url https://fdroid.example.com/fdroid/repo
fdroidkit doctor --repo /srv/fdroid            # URL from repo_url in config.yml
fdroidkit doctor --url ... --repo /srv/fdroid --json
```

`doctor` reads what your users' F-Droid clients read. Run it after every deploy, from a cron job,
or in CI after `publish`. Without `--repo` it needs nothing but the URL; with `--repo` it also
compares the live repo with your working tree.

## The checks

- **tls**: the repo host (and the landing host, when different) presents a certificate the system
  trusts, valid for more than 14 days. F-Droid clients refuse a broken certificate silently.
- **index / entry.jar**: `jarsigner -verify` accepts the jar (a warning when no JDK is installed);
  its signer is the repo key every other check compares with.
- **index / index-v2.json**: the served file has the SHA-256 that the signed entry.json inside
  entry.jar names. A mismatch means a stale CDN copy or a half-finished upload: clients reject it.
- **index / index-v1.jar**: signed by the same key. Missing is a warning (clients before F-Droid
  1.17 cannot read the repo).
- **freshness**: when the index was signed. Older than 90 days is a warning, never a failure: a
  quiet repo is not a broken one. The version check below catches a deploy that did not land.
- **landing / fingerprint**: every SHA-256 printed on the landing page equals the repo key, and
  there is at least one.
- **landing / add-repo link**: the link (`https://...?fingerprint=` or `fdroidrepos://`) points at
  this repo URL with the repo key.
- **landing / qr.svg**: when the page shows it, it encodes exactly that link (needs
  `pip install 'fdroidkit[site]'`, else skipped).
- **signer**: with `--repo`, every signer of every served version is pinned under
  `AllowedAPKSigningKeys` in `metadata/<app>.yml` (a rotated Play key needs both pins). Without
  `--repo`, a signer change between versions is a warning. Signers come from the signed index.
- **pairip**: the newest APK of each app has no `com.pairip` classes (Play automatic protection
  shows a licence wall outside Play, see [play-protection.md](play-protection.md)). Only the dex
  entries are fetched, with HTTP Range requests; a host without range support costs one full
  download. `--all-versions` scans every served APK, `--no-apk` skips the scan.
- **version**: with `--repo`, the live versionCode is not behind `CurrentVersionCode` in the
  metadata or the local `repo/index-v2.json`. An app in the local index but not live fails (not
  deployed); metadata with no APK yet is a warning.

## Exit status

0 nothing failed (warnings allowed), 1 at least one check failed, 3 the repo could not be read
(no entry.jar at the URL: the URL must end in `/repo`).

## JSON

`--json` prints `{"url", "site", "ok", "counts": {ok, warn, fail, skip}, "checks": [{name,
subject, status, detail}]}` for monitoring.
