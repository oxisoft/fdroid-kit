# Security policy

fdroid-kit handles the one secret of an F-Droid repo, the index signing key, and runs in CI next
to other secrets. A flaw there can let someone sign an index your users trust, so please report
it privately.

## Report a vulnerability

Use GitHub's private reporting:
[Report a vulnerability](https://github.com/oxisoft/fdroid-kit/security/advisories/new)
(Security tab, "Report a vulnerability"). Only the maintainers see it. If you cannot use GitHub,
mail info@oxisoft.io with "fdroid-kit security" in the subject.

Please do not open a public issue or pull request for it, and do not post key material, even
your own, anywhere public.

What helps: the fdroidkit version (`fdroidkit --version`), the command or action and its inputs,
what you expected and what happened, and a minimal way to reproduce it.

We answer within 3 working days, agree on a fix and a disclosure date with you, and credit you in
the advisory and CHANGELOG unless you prefer not to be named.

## In scope

- **Index key handling**: the keystore or its passphrase written to a world-readable file, into
  the git tree, the landing page, a log or an artifact; `init` overwriting an existing key;
  the key left on disk after the GitHub action (it must be wiped in its `if: always()` step).
- **Secrets in the actions**: `FDROID_KEYSTORE_B64`, `FDROID_KEYSTORE_PASS`, `PLAY_SA_JSON`,
  `GITHUB_TOKEN` or any `{env: NAME}` value echoed, cached, uploaded or reachable by a later step.
- **Check bypasses**: an APK published although a signer is not pinned, `com.pairip` is present,
  the package or versionCode does not match, or the versionCode goes down.
- **Landing page and verify**: a fingerprint, add-repo link or QR on the page that does not match
  the key that signed `entry.jar` while `verify` or `doctor` still passes.
- **Downloads**: a GitHub release asset or live index file that makes fdroidkit write outside the
  repo, run code, or trust an unsigned index.

## Out of scope

- Bugs in fdroidserver, the Android build-tools or F-Droid clients: report them upstream
  ([fdroidserver](https://gitlab.com/fdroid/fdroidserver/-/issues)). If fdroid-kit should guard
  against one, that part is in scope.
- A compromised CI runner or server you run the kit on, or a leaked app signing key (the kit
  never holds it, see [docs/signing-model.md](docs/signing-model.md)).
- Findings that need write access to your own repo config or secrets.

## Supported versions

Only the latest release gets fixes. Pin a release tag (`@v0.1.8`) and upgrade when a security
release ships; the CHANGELOG marks them.

## If your index key leaked

Treat it as lost: create a new repo key with `fdroidkit init` in a fresh working tree, publish
your apps there and tell users to re-add the repo under the new fingerprint. The APKs keep their
own signature, so no app update breaks. See "Losing the index key" in
[docs/signing-model.md](docs/signing-model.md).
