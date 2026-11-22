# Contributing to fdroid-kit

Thanks for helping. Bug reports, fixes, docs and new checks for real F-Droid traps are all
welcome. Issues and pull requests go to [github.com/oxisoft/fdroid-kit](https://github.com/oxisoft/fdroid-kit).
A security problem (a leaked key, a secret in a log) is not an issue: see [SECURITY.md](SECURITY.md).
Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Dev setup

Python 3.10 or newer. Everything runs from a local virtualenv in the checkout:

```
git clone https://github.com/oxisoft/fdroid-kit && cd fdroid-kit
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/fdroidkit --version
.venv/bin/pytest -q
```

The test suite needs no Android SDK, no fdroidserver, no Java and no network. `tests/fixtures/bin/`
holds stand-ins for `aapt2`, `apksigner` and `fdroid`, so `publish` and `refresh` run their real
subprocess path end to end, including a deploy into a local web root and the read-back over HTTP.

Optional, to test against the real tools: install
[fdroidserver](https://f-droid.org/docs/Installing_the_Server_and_Repo_Tools/) (with
`setuptools<81` for fdroidserver 2.4.x), a JDK and the Android build-tools, then run
`python tests/canary/real_tools.py --build-tools <build-tools dir>` and
`tests/canary/e2e.sh <work dir> <build-tools dir>`. The weekly
[canary workflow](.github/workflows/canary.yml) does the same with the newest versions.

## Test fixtures

- **No real APK in the repo.** `tests/fixtures/make_fixtures.py` writes tiny APK-shaped zips at
  test time, each with a `fixture.json` holding what the real tools would print. A new signer
  case (rotation, Source Stamp, a new apksigner output format) is a new fixture, with the real
  tool output pasted verbatim into the test so the parser is checked against the exact text.
- **No real keys in tests.** No keystore, passphrase, Play service account JSON or GitHub token
  is ever committed, not even an expired or throwaway one. Tests that need a key create it in a
  temp dir (`keytool`, random passphrase) or use an obvious placeholder like `Zml4dHVyZS1rZXk=`.
  Signer digests in tests are public data and fine to use.
- Tests must not reach the network. A test that needs a live index serves a local one.

See [tests/fixtures/README.md](tests/fixtures/README.md) for the fixture set.

## Pull requests

- One change per PR, with tests. A bug fix starts with a test that fails without it.
- `pytest -q` passes locally; CI runs it on Python 3.10 and 3.13 plus both composite actions.
- A change to a command or flag updates its `--help` text, the README section and `CHANGELOG.md`
  (Unreleased). Docs must match the CLI: copy flags from `fdroidkit <command> --help`.
- Every check stays a hard stop: `publish` refuses rather than warns. If a check needs an escape
  hatch, make it an explicit flag and document why.
- The kit wraps fdroidserver, it does not replace it. Prefer calling `fdroid` over reimplementing it.
- No new runtime dependency for the core commands; optional extras (`[play]`, `[site]`) only.

## Releases

Maintainers tag `vX.Y.Z`, bump `version` in `pyproject.toml`, move the CHANGELOG entries under the
new version and update the `@vX.Y.Z` pins in the README and examples.

## License

By contributing you agree that your work is released under the [MIT License](LICENSE).
