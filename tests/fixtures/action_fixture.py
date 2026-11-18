"""Set up a fixture F-Droid repo + APK for exercising the CI actions.

    python tests/fixtures/action_fixture.py <dir>

writes <dir>/fdroid (metadata for com.example.app, pinned signer, published 1.2.0/12)
and <dir>/in.apk (one_key fixture, 1.3.0/13). The stand-in tools in tests/fixtures/bin
read it, so `fdroidkit publish --source file --build-tools tests/fixtures/bin` runs the
whole check path with no Android SDK.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import make_fixtures as fx  # noqa: E402

PKG = "com.example.app"


def main(out: Path) -> None:
    repo = out / "fdroid"
    (repo / "metadata").mkdir(parents=True, exist_ok=True)
    (repo / "repo").mkdir(exist_ok=True)
    (repo / "metadata" / f"{PKG}.yml").write_text(fx.metadata(fx.PLAY))
    fx.make_apk(out / "in.apk", "one_key", PKG, 13, "1.3.0")
    print(f"repo {repo}\napk {out / 'in.apk'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
