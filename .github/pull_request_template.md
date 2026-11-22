## What and why

<!-- One change per PR. Link the issue: Fixes #123 -->

## Checklist

- [ ] `.venv/bin/pytest -q` passes locally
- [ ] A test covers the change (a bug fix: the test fails without it)
- [ ] New tool output (aapt2, apksigner, fdroid) is pasted verbatim into a fixture or test
- [ ] No real APK, keystore, passphrase, service account or token in the diff
- [ ] `--help`, README and docs match the CLI; `CHANGELOG.md` has an Unreleased entry
- [ ] Checks still refuse rather than warn (any escape hatch is an explicit, documented flag)
