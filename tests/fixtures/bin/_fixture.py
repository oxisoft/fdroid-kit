import json
import sys
import zipfile


def info(apk):
    with zipfile.ZipFile(apk) as z:
        return json.loads(z.read("fixture.json"))


def main(tool):
    args = sys.argv[1:]
    if tool == "aapt2":
        assert args[:2] == ["dump", "badging"], args
        sys.stdout.write(info(args[2])["badging"])
    elif tool == "apksigner":
        assert args[:2] == ["verify", "--print-certs"], args
        sys.stdout.write(info(args[2])["signers"])
