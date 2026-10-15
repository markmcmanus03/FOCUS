# Focus Trainer

A single-file Tkinter desktop app (`focus_trainer.py`) for training concentration while
reading. It runs on Windows and Zorin OS (Linux) with the Python standard library, plus
the optional `pynput` package for the global keyboard shortcut. It must keep working on
Python 3.8+ with no other dependencies.

## Releasing a change (the in-app updater depends on this)

Installed copies update themselves from `version.json` on the `main` branch:

1. Bump `APP_VERSION` in `focus_trainer.py`. Any change users should receive needs a
   higher version.
2. Replace the `notes` list in `version.json` with short, user-facing notes for this version.
3. Run `python3 tools/update_manifest.py`, which updates the version, date and checksums.
   `python3 tools/update_manifest.py --check` must pass before merging.
4. Merge to `main`. Updates go live as soon as `main` changes.

The checksums are taken from the files in the working tree. `.gitattributes` stores `.bat`
files with CRLF and everything else with LF, so those files match what GitHub serves. Don't
change those rules without regenerating the manifest. A new file that users should receive
must be added to `FILES` in `tools/update_manifest.py`.

## Testing

There is no test suite. Run the app on a virtual display (`Xvfb :99`, `DISPLAY=:99`) with
`HOME`/`XDG_CONFIG_HOME` pointed at a scratch folder so real settings and data aren't
touched. Setting the environment variable `FOCUS_TRAINER_UPDATE_URL` points the updater at
a local server for testing.
