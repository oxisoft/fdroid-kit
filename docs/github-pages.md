# F-Droid repo on GitHub Pages

No server needed: the repo lives in a GitHub repository, a workflow signs the index on a
hosted runner and GitHub Pages serves it. Working demo:
[oxisoft/fdroid-kit-example](https://github.com/oxisoft/fdroid-kit-example), served at
<https://oxisoft.github.io/fdroid-kit-example/> (throwaway test key, not for real use).

## Set up (once)

1. Create the repo locally and pin your app's signer:

   ```sh
   export FDROID_KEYSTORE_PASS="$(openssl rand -base64 24)"   # keep it in your password manager
   fdroidkit --repo fdroid init --name "My apps" \
     --url https://<owner>.github.io/<repo>/fdroid/repo \
     --keystore ~/.config/fdroidkit/my-index.p12
   fdroidkit --repo fdroid add-app com.example.app --discover-signer app-release.apk
   ```

2. In `fdroid/config.yml`, let the key path come from the environment as well (the action
   writes the key to `$RUNNER_TEMP` and sets `FDROID_KEYSTORE`), and drop `sdk_path`
   (the runner has `ANDROID_HOME`):

   ```yaml
   keystore: {env: FDROID_KEYSTORE}
   ```

3. Secrets (Settings > Secrets and variables > Actions):
   - `FDROID_KEYSTORE_B64`: `base64 -w0 ~/.config/fdroidkit/my-index.p12`
   - `FDROID_KEYSTORE_PASS`: the passphrase

   With the gh CLI, without the values ever touching your shell history:
   `base64 -w0 my-index.p12 | gh secret set FDROID_KEYSTORE_B64` and
   `gh secret set FDROID_KEYSTORE_PASS < passfile`.

4. Settings > Pages > Source: **GitHub Actions**.

5. Copy [`examples/github-pages.yml`](../examples/github-pages.yml) to
   `.github/workflows/`, commit `fdroid/` (config.yml, metadata/, icon.png) and push.

## Publish

Run the workflow (Actions > publish to F-Droid > Run workflow) with the package, versionCode,
versionName and the URL of the signed APK, or call the action from your app's release job:

```yaml
- uses: oxisoft/fdroid-kit/.github/actions/publish@v0.1.3
  env:
    FDROID_KEYSTORE_B64: ${{ secrets.FDROID_KEYSTORE_B64 }}
    FDROID_KEYSTORE_PASS: ${{ secrets.FDROID_KEYSTORE_PASS }}
  with:
    package: com.example.app
    version-code: "42"
    version-name: "1.4.0"
    source: file
    apk: build/app/outputs/flutter-apk/app-release.apk
```

followed by a job with `actions/deploy-pages@v5` (environment `github-pages`, permissions
`pages: write` + `id-token: write`).

What the action does: installs fdroidkit + fdroidserver in a venv, restores the APKs the
live index lists (so APKs never go into git), writes the index key under `$RUNNER_TEMP`
with umask 077, runs every fdroidkit check (identity, signer pins, versionCode, Play
protection guard), `fdroid update`, renders the landing page, checks every fingerprint on
it against the key (`fdroidkit verify`), uploads `site/` + `fdroid/repo/` as the Pages
artifact, and in an `if: always()` step shreds the key and the venv.

## Limits

- GitHub Pages sites are capped at 1 GB and artifacts at 10 GB; a Flutter APK is about
  50 MB, so keep `archive_older` small. The restore step only brings back what the
  current index lists: archived versions are not restored and drop out of the archive.
- Big APKs are fine: the 100 MB per-file limit is a git push limit, and the action deploys
  a Pages artifact, not a commit. The demo serves OxiTalk's 202 MB APK from Pages (HTTP 200,
  range requests work). What does bite with big APKs: the 1 GB site cap counts every version
  the index keeps (four 200 MB versions fill it), deployments time out after 10 minutes, and
  the soft bandwidth limit of 100 GB per month is about 500 downloads of a 200 MB APK. For
  an app that size keep `archive_older` at 1 or 2, and if downloads outgrow the bandwidth,
  move the repo to your own server (Gitea action) rather than Pages.
- An app you sign yourself and release on GitHub can be published straight from its release:
  `source: github-release` with `github-repo: owner/name` (and optional `asset`, `tag`);
  versionCode and versionName are read from the APK.
- Pages is public even for a private repository on paid plans: the repo is public by nature.
- The live read-back happens after deploy-pages, in a later job (`fdroidkit verify --live`).
