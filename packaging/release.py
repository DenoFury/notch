"""Make a signed release:  .venv\\Scripts\\python.exe packaging\\release.py [--no-build] [--publish]

1. builds dist\\Notch-Setup.exe (skip with --no-build)
2. signs it: dist\\Notch-Setup.exe.sig = {"version", "sha256", "signature"} (Ed25519)
3. checks that signature with the public key built into the app (catches a wrong key early)
4. --publish: creates the GitHub release v<version> with both files (needs the GitHub CLI, `gh`,
   signed in to an account that can write to the repository)

The private key lives at %USERPROFILE%\\.notch-release\\signing-key.pem, outside the project, and
must never be committed. Back it up: without it you can't ship updates the installed apps accept.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
EXE = os.path.join(DIST, "Notch-Setup.exe")
SIG = EXE + ".sig"
KEY = os.path.join(os.environ.get("USERPROFILE", ""), ".notch-release", "signing-key.pem")
sys.path.insert(0, ROOT)


def version():
    src = open(os.path.join(ROOT, "notch", "installer.py"), encoding="utf-8").read()
    return re.search(r'VERSION = "(\d+\.\d+\.\d+)"', src).group(1)


def main():
    from cryptography.hazmat.primitives import serialization

    from notch.updater import REPO, signed_message, verify
    ver = version()
    if "--no-build" not in sys.argv:
        subprocess.run([sys.executable, os.path.join(HERE, "build.py")], check=True)
    if not os.path.exists(KEY):
        sys.exit(f"No signing key at {KEY}")
    key = serialization.load_pem_private_key(open(KEY, "rb").read(), password=None)
    with open(EXE, "rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest()
    sig = base64.b64encode(key.sign(signed_message(ver, sha))).decode()
    doc = {"version": ver, "sha256": sha, "signature": sig}
    if not verify(doc):
        sys.exit("The signature doesn't match the public key in notch/updater.py: wrong key?")
    with open(SIG, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Signed Notch {ver}\n  {EXE}\n  {SIG}\n  sha256 {sha}")
    cmd = ["gh", "release", "create", f"v{ver}", EXE, SIG, "--repo", REPO, "--title", f"Notch {ver}",
           "--notes", f"Notch {ver}. Download Notch-Setup.exe and run it. Installed copies update themselves."]
    if "--publish" in sys.argv:
        subprocess.run(cmd, check=True)
        print(f"Published https://github.com/{REPO}/releases/tag/v{ver}")
    else:
        print("\nTo publish:\n  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))


if __name__ == "__main__":
    main()
