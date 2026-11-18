# Test fixtures

No real app ships in this repo. `make_fixtures.py` writes tiny APK-shaped zips at test
time; each carries a `fixture.json` with what the real tools would print for it:

- `one_key`: one signer plus a Source Stamp line (must be ignored)
- `rotated`: two signers after a Play signing-key rotation (both must be pinned)
- `pairip`: a clean `classes.dex` and a `classes2.dex` with `Lcom/pairip/` (refused)
- `stranger`: signed by a key nobody pinned (refused)

`bin/` holds stand-ins for `aapt2`, `apksigner` and `fdroid` that read `fixture.json`,
so `publish` runs its real subprocess path end to end, including `fdroid update`,
`fdroid deploy` into a local web root and the read-back over HTTP.
