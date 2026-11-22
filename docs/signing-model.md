# Signing model: two keys

An F-Droid repo that serves Play-signed APKs involves two unrelated keys.

**1. The app signing key** signs the APK. With Play App Signing, Google holds it. fdroidkit never
sees, needs or stores it: it only checks that the APK's signer digests match the pins in
`AllowedAPKSigningKeys`. This is what makes F-Droid and Play installs update each other.

**2. The index key** signs your repo's index (`entry.jar`, `index-v2.json`). F-Droid clients pin
its fingerprint when a user adds the repo. It is the only secret the kit touches.

## Where the index key lives

Pick one:

- **Self-hosted runner**: the `.p12` sits on the runner host, mode 0600, outside the CI workspace,
  covered by your backups. CI never handles it as a secret.
- **Hosted CI**: store it as `FDROID_KEYSTORE_B64` and `FDROID_KEYSTORE_PASS` repo secrets. The
  action writes it to `$RUNNER_TEMP` under umask 077 and wipes it in an `if: always()` step.

Either way `config.yml` reads the passphrases through fdroidserver's `{env: NAME}` form, so the
config holds no secret and can be committed.

## Guards

- `init` creates the key outside the tree and writes a `.gitignore` for keystores and `*.pass`
  files, so `git add .` cannot pick them up. (No command checks the tree for a key placed there
  by hand: keep it outside, see [migrating.md](migrating.md) step 1.)
- Public files are written under umask 022 (see setup-traps.md, the entry.jar 403).

## Losing the index key

Costs every user a re-add of the repo under a new fingerprint. It never costs an app: the APKs keep
their Play signature. `init` prints a backup reminder for this reason.

## Play service account

`publish --source play` needs a service account JSON (`PLAY_SA_JSON`) with the narrowest Play role
that can read generated APKs. Same temp-file and wipe pattern as the hosted-CI index key.
