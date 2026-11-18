import subprocess
import sys

import pytest

from fdroidkit import __version__, cli


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    for cmd in ("init", "add-app", "publish", "site", "verify"):
        assert cmd in out


@pytest.mark.parametrize("argv", [
    ["init", "--name", "Example", "--url", "https://f.example.com/fdroid/repo"],
    ["add-app", "com.example.app", "--pin", "a" * 64],
    ["site"],
    ["verify"],
])
def test_unimplemented_commands_exit_2(argv, capsys):
    assert cli.main(argv) == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_publish_rejects_bad_version_code():
    with pytest.raises(SystemExit):
        cli.main(["publish", "com.example.app", "x", "1.0"])


def test_module_entry_point():
    r = subprocess.run([sys.executable, "-m", "fdroidkit", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and __version__ in r.stdout
