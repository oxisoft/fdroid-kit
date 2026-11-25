# Moving an existing fdroidserver repo to fdroidkit

You already run an F-Droid repo by hand (`fdroid init`, `fdroid update`, `fdroid deploy`) and
want the kit's checks, landing page and CI. The one thing that must survive is the **index key**:
users added your repo under its fingerprint, and a new key makes every one of them re-add it.
So you do not run `fdroidkit init` (it refuses a tree that already has a `config.yml`, for this
reason). You point the kit at the tree you have.

Every command below was run end to end on a repo made with `fdroid init` (fdroidserver 2.4.5,
build-tools 37), against the flags of fdroidkit 0.1.5. Examples use `/srv/fdroid` as the working
tree (the folder with `config.yml`, `metadata/`, `repo/`) and `https://fdroid.example.com` as the
host.

## 0. Before you start

Back up the working tree and the keystore it names (`keystore:` in `config.yml`, `keystore.p12`
in the tree by default) somewhere outside both. Write down the fingerprint users know:

```
pip install "fdroidkit[site] @ git+https://github.com/oxisoft/fdroid-kit@v0.1.9"
fdroidkit doctor --url https://fdroid.example.com/fdroid/repo
```

The `entry.jar` line shows the index key (`signed by A1A88181...`). The same value must come out
at the end.

## 1. Take the secrets out of config.yml

`fdroid init` writes the passphrase in clear (`keystorepass:` and `keypass:`, the same value) and
puts `keystore.p12` inside the tree. Move the key out and let fdroidserver read the passphrase
from the environment, so `config.yml` can be committed:

```
mkdir -p -m 700 ~/.config/fdroidkit
mv /srv/fdroid/keystore.p12 ~/.config/fdroidkit/fdroid.example.com.p12
chmod 600 ~/.config/fdroidkit/fdroid.example.com.p12
```

Then edit `/srv/fdroid/config.yml`. Copy the old passphrase into your password manager first;
it stays the same, only its place changes:

```yaml
keystore: /home/you/.config/fdroidkit/fdroid.example.com.p12
keystorepass: {env: FDROID_KEYSTORE_PASS}
keypass: {env: FDROID_KEYSTORE_PASS}
```

Keep `repo_keyalias` as it is. Check that `repo_url` is set to the public URL (the kit reads it
for the live read-back) and, if you deploy with `fdroid deploy`, that `serverwebroot` is set:

```yaml
repo_url: https://fdroid.example.com/fdroid/repo
serverwebroot: deploy@web:/var/www/fdroid
```

From now on export the passphrase before any command that signs:
`export FDROID_KEYSTORE_PASS='<the old passphrase>'`. If a variable that `config.yml` reads is
unset, `publish` and `refresh` stop before fdroidserver runs (fdroidserver itself would only log
it and then crash inside keytool).

If the tree is a git repo, add the kit's ignore rules so a key or passphrase file never gets
committed:

```
printf '*.p12\n*.jks\n*.keystore\nkeystore*\n*.pass\n' >> /srv/fdroid/.gitignore
```

## 2. Pin the signers of every app

`publish` refuses an APK whose signers are not all in the app's `AllowedAPKSigningKeys`. A
hand-run repo usually has no pins, so read them from the APKs you already serve. On an app that
already has metadata, `add-app` only adds the missing pins and leaves every other field alone.

Play App Signing (the APK came from Play Console, App bundle explorer):

```
fdroidkit --repo /srv/fdroid add-app com.example.app --discover-signer /srv/fdroid/repo/com.example.app_42.apk
```

This also checks that the signer is Google's (`O=Google Inc.`) and that the APK has no Play
automatic protection. An app you sign yourself takes `--not-play`. An app released on GitHub
can be pinned and wired to its releases in one step:

```
fdroidkit --repo /srv/fdroid add-app io.example.tool --from-github example/tool --asset '*.apk'
```

After a signing key rotation the APK carries two signers; `--discover-signer` pins both. A pin
you know from elsewhere: `--pin <sha256>` (repeatable).

## 3. First refresh

Re-index and redeploy the repo as it is, with the new pins, and read the result back from the
live site. No APK is added and no version moves.

```
fdroidkit --repo /srv/fdroid refresh --dry-run
fdroidkit --repo /srv/fdroid refresh --after-update "fdroidkit site" --rsync-site web:/var/www/fdroid/
```

The dry run refuses when an app whose metadata says it is published has no APK in `repo/` or
`archive/` (a `fdroid update` would silently drop it from the index, and the deploy would remove
it for every user). If you keep APKs only on the server, `--restore` downloads the missing ones
from the live index first. `--after-update "fdroidkit site"` renders the landing page into
`site/`, `--rsync-site` copies it next to the repo (`site/` holds `index.html` for the host
root, the repo stays under `/fdroid/repo`).

## 4. Check that nothing changed for users

```
fdroidkit --repo /srv/fdroid verify --live
fdroidkit doctor --repo /srv/fdroid
```

`verify --live` compares every fingerprint on the page and the QR with the key of the live
`entry.jar`. `doctor` must show the same `signed by` value as in step 0, every signer pinned,
and the live versionCode equal to the local metadata. If the fingerprint differs, stop and
restore the backup from step 0: the tree signed with another key.

## 5. Publish the next release with the kit

```
fdroidkit --repo /srv/fdroid publish com.example.app 43 1.5.0 --source file --apk universal.apk --dry-run
fdroidkit --repo /srv/fdroid publish com.example.app 43 1.5.0 --source file --apk universal.apk \
    --after-update "fdroidkit site" --rsync-site web:/var/www/fdroid/
```

`--source play` fetches the universal APK from Play itself (`pip install 'fdroidkit[play]'`,
a service account in `$PLAY_SA_FILE`); `--source github-release` takes the release asset.
Then move the same command into CI with the [Gitea / Forgejo action](../README.md#in-ci), or to
GitHub Pages with [docs/github-pages.md](github-pages.md) (there the key lives in the
`FDROID_KEYSTORE_B64` and `FDROID_KEYSTORE_PASS` secrets; `base64 -w0` of the `.p12` you moved in
step 1), and add the weekly [doctor check](../examples/doctor-weekly.yml).

## Notes

- `fdroid init` generated your key with the same passphrase for store and key. If yours differ,
  give `keypass` its own variable: `keypass: {env: FDROID_KEY_PASS}`.
- `fdroid update` logs `repo_icon "..." does not exist, generating placeholder`: your index ships
  fdroidserver's placeholder icon. Put a 512 px PNG at the path `repo_icon` names, relative to the
  tree root. See [setup-traps.md](setup-traps.md).
- Branding for the landing page (title, accent, links, translated listings) goes in
  `/srv/fdroid/fdroidkit.json`, see [examples/fdroidkit.json](../examples/fdroidkit.json).
