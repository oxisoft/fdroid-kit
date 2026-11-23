# doctor: audit a live repo

```
fdroidkit doctor --url https://fdroid.example.com/fdroid/repo
fdroidkit doctor --repo /srv/fdroid            # URL from repo_url in config.yml
fdroidkit doctor --url ... --repo /srv/fdroid --json
fdroidkit doctor --url https://you.github.io/fdroid/fdroid/repo --budget github-pages
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

- **storage / total**: the bytes of every APK the signed `index-v2.json` of `repo/` and of
  `archive/` list (their `file.size`, nothing is downloaded). The archive counts only when it is
  served next to the repo (`.../archive`), signed by the repo key and its index matches its
  entry.jar; otherwise a **storage / archive** warning says why it was left out. The detail names
  the three biggest apps. Without `--budget` this is information only.
- **storage / projected**: with `--repo` and a budget, `archive_older` from `config.yml` (3 when
  unset) times the newest APK of every app, which is what `repo/` holds once each app has that
  many releases. A warning when it passes 80% of the budget while today is still fine, with the
  largest `archive_older` that stays under it.

## Storage budget

`--budget` takes a host name or a size:

- `github-pages`: 1,000,000,000 bytes. GitHub: "Published GitHub Pages sites may be no larger
  than 1 GB" ([GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)).
  1 GB is read as 10^9 bytes, the stricter reading.
- a byte count (`750000000`), or `500M`, `2G`, `1.5GB` (decimal) and `1GiB` (binary).

Under 80% is ok, from 80% a warning, from 100% a failure. Only APKs are counted: the index files,
icons, screenshots and the landing page come on top, usually a few MB.

How to bring the total down:

- lower `archive_older` in `config.yml`: it is the number of versions per app kept in `repo/`
  before older ones move to `archive/`. The total grows with it for every app at once.
- drop old versions from `archive/` (delete the APKs, run `fdroid update`), or stop serving the
  archive (`archive_older: 0` keeps every version in `repo/`, which is worse, not better).
- move big apps to a host without a site limit (your own server, the Gitea action).

On GitHub Pages with the kit's action the archive does not grow: the restore step brings back
only what the current index lists, so steady state is `archive_older` versions per app. The
`projected` line computes exactly that.

## Exit status

0 nothing failed (warnings allowed), 1 at least one check failed, 3 the repo could not be read
(no entry.jar at the URL: the URL must end in `/repo`).

## JSON

`--json` prints `{"url", "site", "ok", "counts": {ok, warn, fail, skip}, "checks": [{name,
subject, status, detail}], "storage": {...}}` for monitoring. `storage` holds `total_bytes`,
`repo_bytes`, `archive_bytes`, `apks`, `archive` (whether the archive was counted), `budget`,
`budget_bytes`, `percent`, `archive_older` and `projected_bytes` (with `--repo`), and `apps`:
one entry per app, biggest first, with `package`, `bytes`, `versions`, `repo_bytes`,
`archive_bytes`, so you see which app to trim.

Exit status 2 means a `--budget` value it could not read.
