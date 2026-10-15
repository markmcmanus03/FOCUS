#!/usr/bin/env python3
"""Refresh version.json, which the in-app updater reads from the main branch.

    python3 tools/update_manifest.py           rewrite version.json from the current files
    python3 tools/update_manifest.py --check   exit 1 if version.json is out of date

Edit the "notes" list in version.json by hand for each release; this script fills in
the version (from APP_VERSION in focus_trainer.py), the release date and file checksums.
"""
import datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["focus_trainer.py", "README.md", "install_zorin.sh", "Start Focus Trainer.bat", "setup_windows.bat",
         "icon.png"]
MANIFEST = os.path.join(ROOT, "version.json")


def app_version():
    with open(os.path.join(ROOT, "focus_trainer.py"), encoding="utf-8") as f:
        return re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M).group(1)


def sha256(name):
    with open(os.path.join(ROOT, name), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build(old):
    version = app_version()
    same = old.get("version") == version
    return {
        "version": version,
        "released": old.get("released") if same and old.get("released") else datetime.date.today().isoformat(),
        "notes": old.get("notes", []),
        "files": {name: sha256(name) for name in FILES},
    }


def main(argv):
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            old = json.load(f)
    except FileNotFoundError:
        old = {}
    new = build(old)
    if "--check" in argv:
        stale = [k for k in ("version", "files") if old.get(k) != new[k]]
        if stale:
            print(f"version.json is out of date ({', '.join(stale)}). Run: python3 tools/update_manifest.py")
            return 1
        print(f"version.json is up to date ({new['version']}).")
        return 0
    if old.get("version") != new["version"] and old.get("notes") == new["notes"]:
        print("Reminder: update the release notes in version.json for this version.")
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(new, f, indent=2)
        f.write("\n")
    print(f"Wrote version.json for {new['version']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
