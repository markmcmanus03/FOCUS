# Focus Trainer

Train your concentration while you read. Start a timed session, and every time you notice
your attention has drifted, click a small dot in the corner of your screen (or press a
keyboard shortcut), then go back to the text. Every session is saved, so you can see
whether you're improving over time.

Works on **Windows** and **Zorin OS** (and other Linux desktops). It's a single Python file.

---

## Install

### Windows
1. Install Python from <https://www.python.org/downloads/>. Keep the default options, which
   include Tk. Ticking "Add python.exe to PATH" also helps.
2. Double-click **`setup_windows.bat`**. This installs `pynput`, which the keyboard shortcut needs.
3. Double-click **`Start Focus Trainer.bat`** to run the app.
   For a desktop icon, right-click the .bat file → *Send to* → *Desktop (create shortcut)*.

### Zorin OS
1. Open a terminal in this folder and run:
   ```bash
   ./install_zorin.sh
   ```
   It installs `python3-tk` and `python3-pynput` (it asks for your password) and adds
   **Focus Trainer** to your app menu.
2. Start it from the app menu, or with `python3 focus_trainer.py`.

---

## Using it

1. Choose a session length and press **Start session**. The main window minimises and a
   small dark dot appears near the bottom-right corner of your screen, on top of every app.
2. Read. When you notice your mind has wandered, **click the dot** or **press Ctrl+Alt+D**,
   then go straight back to reading.
3. When the time is up, a soft chime plays and your results appear.

The dot:
- **Drag** it to move it. It remembers where you put it.
- **Right-click** it to undo the last press, pause or resume, or end the session early.
- On Windows, clicking it doesn't take focus away from your reading app.

Good to know:
- Two presses less than a second apart count once, so a double-click isn't counted twice.
- If your computer goes to sleep, the session pauses itself.
- If the app or computer crashes mid-session, you're offered the chance to save that
  session the next time you open it.
- Before saving a session shorter than 2 minutes, the app asks you, because very short
  sessions can skew your averages.

---

## Settings

| Setting | Options |
|---|---|
| **When you log a distraction** | *Neutral*: the dot blinks faintly. *Subtle negative cue*: the widget flashes dull red. *Audible negative cue*: a short, low buzz (volume adjustable). *Flash and buzz*: both. |
| **Floating widget shows** | *Just the dot*, *dot and minutes left*, or *dot, minutes left and count*. Time is shown in whole minutes, so nothing ticks. |
| **Size / opacity** | Small, medium or large, and how see-through it is. *Show preview* displays the widget so you can position it and test your feedback setting. |
| **Dot colour** | *Match system*, *Light* or *Dark*. The dot is least noticeable when it blends with the page you're reading. |
| **Appearance** | *Match system* follows the light/dark setting of Windows or Zorin and switches as soon as you change it. You can also force *Light* or *Dark*. |
| **Keyboard shortcut** | On or off. Type a shortcut (e.g. `Ctrl+Alt+D`, `F8`) and press *Save*, or press *Record…* and then the keys. |
| **Session history and CSV files** | Where sessions are saved (see below), and whether up-to-date CSV copies are kept there. |
| **Updates** | Whether to check GitHub for a newer version when the app starts, plus a *Check now* button. |

---

## One history across Windows and Zorin

In **Settings → Session history folder → Change…**, choose a folder that both systems can
see, and do this on each computer. Any of these works:

- **Dropbox** (has clients for Windows and Linux)
- **Syncthing** (free, works on both)
- **OneDrive** (built into Windows; on Zorin, use a client such as `onedrive` or `rclone`)
- A **shared partition**, if you dual-boot on one machine

When you switch folders, the app offers to copy your existing sessions into the new one.
Every session is saved as its own small file, so syncing never causes edit conflicts.
Preferences such as the shortcut and widget position stay separate on each computer.

---

## Reading the Progress tab

- **Headline:** your last 7 sessions compared with the 7 before them. This is your
  improving-or-slipping signal.
- **Tiles:** totals for the period you choose (7 days to all time), compared with the
  previous period of the same length.
- **Distractions per 10 minutes:** the main measure, adjusted for length so a 15-minute
  session and a 45-minute one can be compared. Dots are individual sessions. The line is
  the 7-session average, where longer sessions count for more. **Lower is better.**
- **Longest focused stretch:** the longest gap without a press in each session.
  **Higher is better.**
- **By time of day / by day of week:** distractions per 10 minutes, grouped by when each
  session started. The darker bar marks your best slot. A slot needs at least 2 sessions
  to count.
- If one session is off the scale, it's drawn as a ▲ at the top edge. Hover over any dot
  or bar to see its exact numbers.

The **History** tab lists every session. You can delete sessions there or export them to CSV.

---

## Exporting your data (CSV)

Every session records a full log: when it started and ended, each distraction press (and
whether it came from a click, the keyboard shortcut or the `--log` command), every pause and
resume with real times, any undo, and how the session ended.

**History → Export CSV…** asks for a folder and saves two files. If you select sessions in
the list first, only those are exported; otherwise everything is.

- **`focus-trainer-sessions.csv`**: one row per session. Columns include `date`,
  `start_time`, `weekday`, `time_of_day`, `planned_minutes`, `active_minutes`,
  `paused_minutes`, `pause_count`, `completed`, `end_reason`, `distractions`,
  `distractions_per_10_min`, `longest_focus_seconds`, `average_focus_seconds`,
  `first_distraction_seconds`, `by_click`, `by_hotkey`, `by_command`, `undone_presses`,
  `computer`, and the feedback and widget settings used.
- **`focus-trainer-events.csv`**: one row per event (`session_start`, `distraction`,
  `undo`, `pause`, `auto_pause` (the computer slept), `resume`, `session_end`), with
  its date, time, `seconds_into_session` (reading time, excluding pauses), `source`,
  `counted` (FALSE for presses you undid), `seconds_since_previous` (the focus stretch
  before each distraction), and `paused_seconds` on resumes.

**Automatic copies:** with *Keep up-to-date CSV files* switched on (Settings), the same two
files are kept as `sessions.csv` and `events.csv` in the `csv` folder inside your history
folder. They're refreshed after every session, so you can point Excel, Python or R straight
at them. If a file is open in Excel when a session ends, it's updated after your next session.

Sessions recorded with version 1.0 have only the press times. Their events are rebuilt from
those, and the source columns are left blank.

---

## Updates

When the app starts, it checks the `main` branch of
[markmcmanus03/FOCUS](https://github.com/markmcmanus03/FOCUS) for a newer version. If it
finds one, it shows what's new and asks before installing. It never interrupts a session;
the offer waits until the session ends. *Settings → Updates* has a **Check now** button
and lets you turn off the automatic check.

When you choose **Install and restart**, the app:
1. downloads the new files and checks each one against the checksum published with the
   release, so nothing changes if a download is incomplete or altered
2. makes sure the new program has no errors before using it
3. copies your current version into a `.backup` folder inside the app folder (the
   three most recent backups are kept)
4. replaces the files and restarts.

Your sessions and settings live elsewhere and aren't touched. If the app folder is a Git
clone, the updater runs `git pull` instead.

To go back to an earlier version, copy the files from the newest folder in `.backup` over
the ones in the app folder.

Notes:
- The repository must be **public** for the updater to download from it. If it's private,
  the app says so when it checks.
- Copies of version 1.0 don't include the updater. Download the app once more to get it;
  after that, updates are automatic.

---

## Zorin OS and Wayland

Check which session you're running with `echo $XDG_SESSION_TYPE`.

- **x11:** everything works, including the shortcut in any app.
- **wayland:** the dot works normally, but Wayland stops apps from seeing keys you press in
  other apps, so the built-in shortcut only works while Focus Trainer is focused. There are
  two fixes:
  1. **Use a system shortcut:** in *Settings → Keyboard → Keyboard Shortcuts → Custom
     Shortcuts*, add a shortcut that runs
     `python3 /full/path/to/focus_trainer.py --log`
     (*Settings → Keyboard shortcut* in the app shows the exact command to copy), or
  2. **Switch to X11:** on the login screen, click the gear icon and choose the session
     that doesn't say "Wayland".

`focus_trainer.py --log` tells the running app to log a distraction, so you can bind it
to any button or shortcut tool.

---

## Where things are stored

| What | Windows | Zorin / Linux |
|---|---|---|
| Sessions (default) | `Documents\Focus Trainer\sessions\` | `~/Documents/Focus Trainer/sessions/` |
| CSV copies | `Documents\Focus Trainer\csv\` | `~/Documents/Focus Trainer/csv/` |
| Settings, sounds, error log | `%APPDATA%\FocusTrainer\` | `~/.config/focus-trainer/` |
| Previous versions (after updating) | `.backup\` in the app folder | `.backup/` in the app folder |

Each session file records the start time, the planned and actual length, and the time of
every press. Nothing leaves your computer except through a sync folder you choose.

## Troubleshooting

- **"No module named tkinter":** on Zorin, run `sudo apt install python3-tk`. On Windows,
  re-run the Python installer and make sure *tcl/tk and IDLE* is ticked.
- **The shortcut needs pynput:** run `setup_windows.bat`, or on Zorin run
  `sudo apt install python3-pynput`, then restart the app.
- **No sound:** check *Buzz volume* in Settings and your system volume. On Linux the app
  plays sounds through `paplay`, `pw-play` or `aplay`.
- **Something went wrong:** details are written to `error.log` in the settings folder.
