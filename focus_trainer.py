#!/usr/bin/env python3
"""
Focus Trainer
=============

Train your concentration while reading. Start a timed session, then click the small
floating dot (or press your keyboard shortcut) every time you notice your attention
has drifted. Every session is saved, so you can see whether you are improving.

Runs on Windows and Linux with only the Python standard library (plus Tk).
The optional `pynput` package adds a global keyboard shortcut.

    python3 focus_trainer.py          start the app
    python3 focus_trainer.py --log    log a distraction in the running app
                                      (handy to bind to a system keyboard shortcut)
"""

from __future__ import annotations

import array
import csv
import glob
import hashlib
import io
import json
import math
import os
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from datetime import datetime, timedelta

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter import font as tkfont
except ImportError:  # pragma: no cover - depends on the system
    sys.stderr.write(
        "Focus Trainer needs Tkinter.\n"
        "  Zorin / Ubuntu:  sudo apt install python3-tk\n"
        "  Windows:         re-run the python.org installer and tick 'tcl/tk and IDLE'\n"
    )
    sys.exit(1)

APP_NAME = "Focus Trainer"
APP_VERSION = "1.1.0"
IPC_HOST, IPC_PORT = "127.0.0.1", 47821
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Updates come from version.json on the main branch of the GitHub repository
UPDATE_REPO = "markmcmanus03/FOCUS"
UPDATE_BRANCH = "main"
UPDATE_BASE_URL = (os.environ.get("FOCUS_TRAINER_UPDATE_URL")
                   or f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_BRANCH}/")

DEBOUNCE_SECONDS = 1.0        # presses closer together than this count once
SHORT_SESSION_SECONDS = 120   # ask before saving sessions shorter than this
ROLLING_WINDOW = 7            # sessions in the moving average
SUSPEND_GAP_SECONDS = 20      # a tick gap this long means the computer slept

# Colour tokens. `C` holds the active theme and is updated in place when it changes,
# so code always reads colours at draw time (C.page, C.ink, ...).
LIGHT = dict(
    page="#f9f9f7", surface="#fcfcfb", field="#fcfcfb", stripe="#f6f5f2",
    ink="#0b0b0b", ink2="#52514e", muted="#898781",
    grid="#e1e0d9", axis="#c3c2b7", border="#e4e3dd", hover="#f0efec",
    accent="#2a78d6", accent_hover="#256abf", accent_press="#1c5cab", on_accent="#ffffff",
    focus="#86b6ef", select="#cde2fb",
    series="#2a78d6", series_soft="#86b6ef", series_strong="#1c5cab",
    good="#006300", bad="#d03b3b", tip_bg="#1a1a19", tip_fg="#f0efec",
)
DARK = dict(
    page="#121211", surface="#1a1a19", field="#1f1f1e", stripe="#161615",
    ink="#f0efec", ink2="#c3c2b7", muted="#8f8d86",
    grid="#2c2c2a", axis="#45443f", border="#2f2f2c", hover="#262624",
    accent="#2a78d6", accent_hover="#3987e5", accent_press="#1c5cab", on_accent="#ffffff",
    focus="#3987e5", select="#1d3554",
    series="#5598e7", series_soft="#245a9e", series_strong="#86b6ef",
    good="#0ca30c", bad="#e66767", tip_bg="#f0efec", tip_fg="#0b0b0b",
)


class _Colors:
    def __init__(self, values):
        self.__dict__.update(values)


C = _Colors(LIGHT)

# Floating widget colours: low contrast, so it sits quietly on the page you're reading
DOT_DARK = dict(body="#2a2a28", dot="#77766f", blink="#d6d5ce", text="#a3a29a",
                red_body="#5e2222", red_dot="#c44a4a")
DOT_LIGHT = dict(body="#ecebe6", dot="#a8a7a0", blink="#4a4945", text="#6b6a64",
                 red_body="#f2d4d4", red_dot="#c44a4a")

FEEDBACK_MODES = [
    ("neutral", "Neutral", "The dot blinks faintly so you know the press counted."),
    ("flash", "Subtle negative cue", "The widget flashes a dull red, then fades back."),
    ("buzz", "Audible negative cue", "A short, low buzz."),
    ("flash_buzz", "Flash and buzz", "Both of the above."),
]
DISPLAY_MODES = [
    ("dot", "Just the dot", "No timer, no count. You see your results when the session ends."),
    ("time", "Dot and minutes left", "A dim countdown in whole minutes."),
    ("time_count", "Dot, minutes left and count", "Also shows how many distractions you've logged."),
]
WIDGET_SIZES = [("small", "Small"), ("medium", "Medium"), ("large", "Large")]

TIME_BUCKETS = [  # (name, hours label, first hour, end hour, phrase)
    ("Early", "5–9", 5, 9, "early in the morning"),
    ("Morning", "9–12", 9, 12, "in the morning"),
    ("Afternoon", "12–17", 12, 17, "in the afternoon"),
    ("Evening", "17–21", 17, 21, "in the evening"),
    ("Night", "21–5", 21, 5, "at night"),
]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
THEME_CHOICES = [("system", "Match system"), ("light", "Light"), ("dark", "Dark")]

DEFAULT_SETTINGS = {
    "data_folder": "",
    "duration_minutes": 20,
    "feedback": "neutral",
    "display": "dot",
    "buzz_volume": 60,
    "widget_size": "medium",
    "widget_opacity": 0.6,
    "widget_x": None,
    "widget_y": None,
    "chime": True,
    "minimize_on_start": True,
    "hotkey_enabled": True,
    "hotkey": "Ctrl+Alt+D",
    "appearance": "system",
    "dot_theme": "system",
    "auto_csv": True,
    "check_updates": True,
    "skipped_version": "",
    "updated_from": "",
}


# ---------------------------------------------------------------------------
# Paths, files and small helpers
# ---------------------------------------------------------------------------

def config_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "FocusTrainer")
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/FocusTrainer")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "focus-trainer")


def default_data_dir():
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return os.path.join(docs if os.path.isdir(docs) else home, "Focus Trainer")


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - opening a folder the user chose
    else:
        opener = "open" if IS_MAC else "xdg-open"
        subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_wayland():
    return (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY")))


def log_command():
    exe = sys.executable or "python3"
    if IS_WINDOWS and exe.lower().endswith("pythonw.exe"):
        exe = exe[:-5] + ".exe"
    return f'"{exe}" "{os.path.abspath(__file__)}" --log'


def parse_iso(text):
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.astimezone()


def now_local():
    return datetime.now().astimezone()


def fmt_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def fmt_minutes(seconds):
    m = int(round(seconds / 60))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def fmt_offset(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def short_date(dt):
    return f"{dt.day} {dt.strftime('%b')}"


def long_date(dt):
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b %Y')}"


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def nice_ticks(vmax, target=4):
    vmax = vmax if vmax > 0 else 1.0
    raw = vmax / target
    mag = 10 ** math.floor(math.log10(raw))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    count = max(1, int(math.ceil(vmax / step - 1e-9)))
    return [i * step for i in range(count + 1)], step


def virtual_screen(widget):
    """(x, y, width, height) of the whole desktop, across all monitors."""
    if IS_WINDOWS:
        try:
            import ctypes
            u = ctypes.windll.user32
            return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        except Exception:
            pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def rounded_rect(canvas, x0, y0, x1, y1, r_top, r_bottom=None, **kw):
    """Polygon with rounded corners (Tk has no native rounded rectangle)."""
    r_bottom = r_top if r_bottom is None else r_bottom
    lim = min((x1 - x0) / 2, (y1 - y0) / 2)
    rt, rb = max(0, min(r_top, lim)), max(0, min(r_bottom, lim))
    pts = [
        x0 + rt, y0, x0 + rt, y0, x1 - rt, y0, x1 - rt, y0, x1, y0,
        x1, y0 + rt, x1, y0 + rt, x1, y1 - rb, x1, y1 - rb, x1, y1,
        x1 - rb, y1, x1 - rb, y1, x0 + rb, y1, x0 + rb, y1, x0, y1,
        x0, y1 - rb, x0, y1 - rb, x0, y0 + rt, x0, y0 + rt, x0, y0,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


# ---------------------------------------------------------------------------
# Settings and session storage
# ---------------------------------------------------------------------------

class Settings:
    """Per-computer preferences, stored next to the app's config."""

    def __init__(self, path):
        self.path = path
        self.data = dict(DEFAULT_SETTINGS)
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update({k: v for k, v in loaded.items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        if self.data.get(key) != value:
            self.data[key] = value
            self.save()

    def save(self):
        try:
            write_json_atomic(self.path, self.data)
        except OSError:
            traceback.print_exc()


class SessionStore:
    """One small JSON file per session, so a synced folder never has edit conflicts."""

    def __init__(self, folder):
        self.folder = folder

    @property
    def sessions_dir(self):
        return os.path.join(self.folder, "sessions")

    def files(self):
        return sorted(glob.glob(os.path.join(self.sessions_dir, "*.json")))

    def save(self, rec):
        start = parse_iso(rec["start"])
        host = "".join(ch for ch in rec.get("machine", "pc") if ch.isalnum() or ch in "-_")[:24] or "pc"
        name = f"{start.strftime('%Y-%m-%d_%H%M%S')}_{host}_{rec['id'][:6]}.json"
        path = os.path.join(self.sessions_dir, name)
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        write_json_atomic(path, clean)
        return path

    def load_all(self):
        by_id = {}
        bad = 0
        for path in self.files():
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                rec["_start"] = parse_iso(rec["start"])
                rec["active_seconds"] = float(rec["active_seconds"])
                rec["distractions"] = [float(t) for t in rec.get("distractions", [])]
                rec["id"] = str(rec["id"])
            except (OSError, ValueError, KeyError, TypeError):
                bad += 1
                continue
            if rec["id"] in by_id:  # e.g. a sync "conflicted copy"
                by_id[rec["id"]]["_paths"].append(path)
                continue
            rec["_paths"] = [path]
            rec["_m"] = session_metrics(rec)
            by_id[rec["id"]] = rec
        return sorted(by_id.values(), key=lambda r: r["_start"]), bad

    def delete(self, rec):
        for path in rec.get("_paths", []):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def session_metrics(rec):
    dur = max(float(rec.get("active_seconds", 0)), 0.0)
    times = sorted(min(max(t, 0.0), dur) for t in rec.get("distractions", []))
    edges = [0.0] + times + [dur]
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    longest = max(gaps) if gaps else dur
    return {
        "seconds": dur,
        "count": len(times),
        "rate": len(times) * 600.0 / dur if dur > 0 else 0.0,
        "longest": longest,
        "longest_start": edges[gaps.index(longest)] if gaps else 0.0,
        "times": times,
    }


def weighted_rate(recs):
    """Distractions per 10 minutes across several sessions (long sessions count for more)."""
    secs = sum(r["_m"]["seconds"] for r in recs)
    if secs <= 0:
        return None
    return sum(r["_m"]["count"] for r in recs) * 600.0 / secs


def change_text(new, old, label, lower_is_better=True):
    """('↓ 18% vs previous 30 days · better', colour) for a delta line."""
    if new is None or old is None:
        return f"No sessions in {label} to compare with", C.muted
    if old == 0:
        if new == 0:
            return f"→ Same as {label}", C.ink2
        better = not lower_is_better
        return f"↑ Up from 0 in {label} · {'better' if better else 'worse'}", C.good if better else C.bad
    pct = (new - old) / old * 100
    if abs(pct) < 5:
        return f"→ About the same as {label}", C.ink2
    better = (pct < 0) == lower_is_better
    arrow = "↓" if pct < 0 else "↑"
    amount = f"{new / old:.1f}×" if pct >= 100 else f"{abs(pct):.0f}%"
    return f"{arrow} {amount} vs {label} · {'better' if better else 'worse'}", C.good if better else C.bad


def time_bucket(hour):
    """Index into TIME_BUCKETS for an hour of the day."""
    for i, (_, _, a, b, _) in enumerate(TIME_BUCKETS):
        if (a <= hour < b) if a < b else (hour >= a or hour < b):
            return i
    return 0


# ---------------------------------------------------------------------------
# CSV export: one row per session, and one row per event
# ---------------------------------------------------------------------------

SESSION_COLUMNS = [
    "session_id", "date", "start_time", "weekday", "time_of_day", "start", "end",
    "planned_minutes", "active_minutes", "paused_minutes", "pause_count", "completed", "end_reason",
    "distractions", "distractions_per_10_min", "longest_focus_seconds", "longest_focus_from_seconds",
    "average_focus_seconds", "first_distraction_seconds", "by_click", "by_hotkey", "by_command",
    "undone_presses", "computer", "platform", "feedback", "widget_display", "app_version",
]
EVENT_COLUMNS = [
    "session_id", "session_start", "event_number", "event", "date", "time", "timestamp",
    "seconds_into_session", "minutes_into_session", "source", "counted", "seconds_since_previous",
    "paused_seconds", "detail",
]
EVENT_NAMES = {"start": "session_start", "end": "session_end", "distraction": "distraction", "undo": "undo",
               "pause": "pause", "auto_pause": "auto_pause", "resume": "resume"}
SOURCE_NAMES = {"click": "click", "hotkey": "hotkey", "ipc": "command", "command": "command"}
END_REASONS = {"completed": "timer finished", "ended_early": "ended early", "quit": "app closed during the session",
               "recovered": "recovered after the app closed unexpectedly", "unfinished": "session still running"}


def _yes(flag):
    return "TRUE" if flag else "FALSE"


def session_events(rec):
    """The session's event list, rebuilt from press times for files saved by version 1.0."""
    if rec.get("events"):
        return rec["events"], False
    start = rec["_start"]
    completed = rec.get("completed", False)
    events = [{"type": "start", "at": rec["start"], "t": 0.0}]
    for t in sorted(rec.get("distractions", [])):
        events.append({"type": "distraction", "t": t,
                       "at": (start + timedelta(seconds=t)).isoformat(timespec="seconds")})
    end_at = rec.get("end") or (start + timedelta(seconds=rec["active_seconds"])).isoformat(timespec="seconds")
    events.append({"type": "end", "at": end_at, "t": rec["active_seconds"],
                   "reason": "completed" if completed else "ended_early"})
    return events, True


def session_row(rec):
    m = rec["_m"]
    start = rec["_start"]
    events, rebuilt = session_events(rec)
    presses = [e for e in events if e["type"] == "distraction" and not e.get("undone")]
    by_source = {name: sum(1 for e in presses if SOURCE_NAMES.get(e.get("source")) == name)
                 for name in ("click", "hotkey", "command")}
    completed = rec.get("completed", False)
    return {
        "session_id": rec["id"],
        "date": start.strftime("%Y-%m-%d"),
        "start_time": start.strftime("%H:%M:%S"),
        "weekday": DAY_NAMES[start.weekday()],
        "time_of_day": TIME_BUCKETS[time_bucket(start.hour)][0],
        "start": rec["start"],
        "end": rec.get("end", ""),
        "planned_minutes": round(rec.get("planned_seconds", m["seconds"]) / 60, 2),
        "active_minutes": round(m["seconds"] / 60, 2),
        "paused_minutes": round(rec["paused_seconds"] / 60, 2) if "paused_seconds" in rec else "",
        "pause_count": rec.get("pause_count", 0),
        "completed": _yes(completed),
        "end_reason": rec.get("end_reason") or ("completed" if completed else "ended_early"),
        "distractions": m["count"],
        "distractions_per_10_min": round(m["rate"], 3),
        "longest_focus_seconds": round(m["longest"], 1),
        "longest_focus_from_seconds": round(m["longest_start"], 1),
        "average_focus_seconds": round(m["seconds"] / (m["count"] + 1), 1),
        "first_distraction_seconds": round(m["times"][0], 1) if m["times"] else "",
        "by_click": "" if rebuilt else by_source["click"],
        "by_hotkey": "" if rebuilt else by_source["hotkey"],
        "by_command": "" if rebuilt else by_source["command"],
        "undone_presses": "" if rebuilt else sum(1 for e in events if e["type"] == "undo"),
        "computer": rec.get("machine", ""),
        "platform": rec.get("platform", ""),
        "feedback": rec.get("feedback", ""),
        "widget_display": rec.get("display", ""),
        "app_version": rec.get("app_version", "1.0"),
    }


def event_rows(rec):
    events, rebuilt = session_events(rec)
    previous = 0.0
    for number, ev in enumerate(events, 1):
        at = parse_iso(ev["at"])
        kind = ev["type"]
        t = float(ev.get("t", 0.0))
        row = dict.fromkeys(EVENT_COLUMNS, "")
        row.update({
            "session_id": rec["id"], "session_start": rec["start"], "event_number": number,
            "event": EVENT_NAMES.get(kind, kind), "date": at.strftime("%Y-%m-%d"),
            "time": at.strftime("%H:%M:%S"), "timestamp": ev["at"],
            "seconds_into_session": round(t, 1), "minutes_into_session": round(t / 60, 2),
        })
        detail = []
        if kind == "distraction":
            row["source"] = SOURCE_NAMES.get(ev.get("source"), "")
            row["counted"] = _yes(not ev.get("undone"))
            if not ev.get("undone"):
                row["seconds_since_previous"] = round(t - previous, 1)
                previous = t
            else:
                detail.append("undone")
        elif kind == "undo":
            detail.append(f"removed the press at {fmt_offset(ev.get('removed_t', t))}")
        elif kind == "resume":
            row["paused_seconds"] = ev.get("paused_seconds", "")
        elif kind == "auto_pause":
            detail.append("the computer went to sleep")
        elif kind == "end":
            detail.append(END_REASONS.get(ev.get("reason"), ev.get("reason", "")))
        if rebuilt:
            detail.append("rebuilt from a version 1.0 session file; times assume no pauses")
        row["detail"] = "; ".join(d for d in detail if d)
        yield row


def write_csv(path, columns, rows, only_if_changed=False):
    """Write rows as CSV. Returns False if the file already had exactly this content."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    if only_if_changed and os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def export_csvs(recs, folder, prefix="", only_if_changed=False):
    """Write <prefix>sessions.csv and <prefix>events.csv; returns their paths."""
    recs = sorted(recs, key=lambda r: r["_start"])
    sessions_path = os.path.join(folder, f"{prefix}sessions.csv")
    events_path = os.path.join(folder, f"{prefix}events.csv")
    write_csv(sessions_path, SESSION_COLUMNS, (session_row(r) for r in recs), only_if_changed)
    write_csv(events_path, EVENT_COLUMNS, (row for r in recs for row in event_rows(r)), only_if_changed)
    return sessions_path, events_path


# ---------------------------------------------------------------------------
# System appearance (light / dark)
# ---------------------------------------------------------------------------

def detect_system_theme():
    """'dark' or 'light', following the desktop's appearance setting."""
    def run(*cmd):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip().lower()
    try:
        if IS_WINDOWS:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        if IS_MAC:
            return "dark" if "dark" in run("defaults", "read", "-g", "AppleInterfaceStyle") else "light"
        if shutil.which("gsettings"):  # GNOME, Zorin, Cinnamon, Budgie...
            scheme = run("gsettings", "get", "org.gnome.desktop.interface", "color-scheme")
            if "prefer-dark" in scheme:
                return "dark"
            if "prefer-light" in scheme:
                return "light"
            if "dark" in run("gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"):
                return "dark"
            return "light"
    except Exception:
        pass
    return "dark" if "dark" in os.environ.get("GTK_THEME", "").lower() else "light"


# ---------------------------------------------------------------------------
# Updates from GitHub
# ---------------------------------------------------------------------------

class UpdateError(Exception):
    pass


SAFE_UPDATE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")


def version_tuple(text):
    parts = [int(x) for x in re.findall(r"\d+", str(text))[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": f"FocusTrainer/{APP_VERSION}",
                                               "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("GitHub returned 'not found'. If the FOCUS repository is private, "
                              "it needs to be public for updates to work.") from exc
        raise UpdateError(f"GitHub returned an error ({exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"Couldn't reach GitHub ({getattr(exc, 'reason', exc)}).") from exc


def fetch_manifest():
    raw = http_get(f"{UPDATE_BASE_URL}version.json?t={int(time.time())}")
    try:
        man = json.loads(raw.decode("utf-8"))
        assert isinstance(man["version"], str) and isinstance(man["files"], dict)
        for name, digest in man["files"].items():
            assert SAFE_UPDATE_NAME.match(name) and re.fullmatch(r"[0-9a-fA-F]{64}", digest)
        man["notes"] = [str(n) for n in man.get("notes", [])]
    except (ValueError, KeyError, AssertionError, TypeError, AttributeError) as exc:
        raise UpdateError("The update information on GitHub couldn't be read.") from exc
    return man


def is_git_checkout(app_dir=APP_DIR):
    return os.path.isdir(os.path.join(app_dir, ".git"))


def install_update(man, app_dir=APP_DIR):
    """Install the version described by `man`. Returns the backup folder (None for a git pull)."""
    if is_git_checkout(app_dir):
        git = shutil.which("git")
        if not git:
            raise UpdateError("This copy is a Git clone, but Git isn't installed. Run 'git pull' in the app folder.")
        result = subprocess.run([git, "-C", app_dir, "pull", "--ff-only"], capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise UpdateError("'git pull' didn't work:\n" + (result.stderr or result.stdout).strip()[-600:])
        return None

    # 1. download everything and check it before touching any file
    blobs = {}
    for name, digest in man["files"].items():
        data = http_get(UPDATE_BASE_URL + urllib.parse.quote(name))
        if hashlib.sha256(data).hexdigest() != digest.lower():
            raise UpdateError(f"{name} didn't match its checksum, so nothing was changed. Please try again later.")
        blobs[name] = data
    if "focus_trainer.py" not in blobs:
        raise UpdateError("The update doesn't include the app itself.")
    try:
        compile(blobs["focus_trainer.py"], "focus_trainer.py", "exec")
    except SyntaxError as exc:
        raise UpdateError(f"The new version contains an error ({exc}), so it wasn't installed.") from exc

    # 2. back up the current files
    backups = os.path.join(app_dir, ".backup")
    backup = os.path.join(backups, f"{APP_VERSION}_{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(backup, exist_ok=True)
    for name in blobs:
        if os.path.exists(os.path.join(app_dir, name)):
            shutil.copy2(os.path.join(app_dir, name), os.path.join(backup, name))

    # 3. replace the files; if anything fails, put the old ones back
    written = []
    try:
        for name, data in blobs.items():
            dest = os.path.join(app_dir, name)
            tmp = dest + ".update-tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            if not IS_WINDOWS:
                if os.path.exists(dest):
                    mode = os.stat(dest).st_mode & 0o777
                else:
                    mode = 0o755 if name.endswith((".py", ".sh")) else 0o644
                os.chmod(tmp, mode)
            os.replace(tmp, dest)
            written.append(name)
    except OSError as exc:
        for name in written:
            old = os.path.join(backup, name)
            if os.path.exists(old):
                shutil.copy2(old, os.path.join(app_dir, name))
        raise UpdateError(f"Couldn't write the new files ({exc}). Your current version was left in place.") from exc

    # keep the three most recent backups
    for old in sorted(glob.glob(os.path.join(backups, "*")), key=os.path.getmtime)[:-3]:
        shutil.rmtree(old, ignore_errors=True)
    return backup


# ---------------------------------------------------------------------------
# A running session
# ---------------------------------------------------------------------------

class ActiveSession:
    """A running session. Times called `t` are seconds of active (unpaused) reading."""

    def __init__(self, planned_seconds):
        self.id = uuid.uuid4().hex
        self.start = now_local()
        self.planned = float(planned_seconds)
        self.accum = 0.0
        self.run_started = time.monotonic()
        self.last_tick = self.run_started
        self.paused = False
        self.auto_paused = False
        self.pause_count = 0
        self.paused_seconds = 0.0
        self.pause_wall_start = None
        self.distractions = []
        self.last_log = -1e9
        self.events = [{"type": "start", "at": self.start.isoformat(timespec="seconds"), "t": 0.0}]

    def elapsed(self):
        if self.paused:
            return self.accum
        return self.accum + (time.monotonic() - self.run_started)

    def remaining(self):
        return max(0.0, self.planned - self.elapsed())

    def _event(self, kind, t=None, at=None, **extra):
        ev = {"type": kind, "at": (at or now_local()).isoformat(timespec="seconds"),
              "t": round(self.elapsed() if t is None else t, 1)}
        ev.update(extra)
        self.events.append(ev)
        return ev

    def _begin_pause(self, kind, at=None):
        self.paused = True
        self.pause_count += 1
        self.pause_wall_start = (at or now_local()).timestamp()
        self._event(kind, t=self.accum, at=at)

    def pause(self):
        if not self.paused:
            self.accum = self.elapsed()
            self._begin_pause("pause")

    def resume(self):
        if self.paused:
            paused_for = max(0.0, time.time() - (self.pause_wall_start or time.time()))
            self.paused_seconds += paused_for
            self.pause_wall_start = None
            self.run_started = time.monotonic()
            self.paused = False
            self.auto_paused = False
            self._event("resume", paused_seconds=round(paused_for))

    def check_suspend(self):
        """If the computer slept, don't count that time and pause the session."""
        now = time.monotonic()
        gap = now - self.last_tick
        self.last_tick = now
        if gap > SUSPEND_GAP_SECONDS and not self.paused:
            self.accum = max(0.0, self.elapsed() - gap)
            self.auto_paused = True
            self._begin_pause("auto_pause", at=now_local() - timedelta(seconds=gap))
            return True
        return False

    def log(self, source="click"):
        if self.paused:
            return False
        t = self.elapsed()
        if t - self.last_log < DEBOUNCE_SECONDS:
            return False
        self.last_log = t
        self.distractions.append(round(t, 1))
        self._event("distraction", t=t, source=source)
        return True

    def undo(self):
        if not self.distractions:
            return False
        removed = self.distractions.pop()
        self.last_log = -1e9
        for ev in reversed(self.events):
            if ev["type"] == "distraction" and not ev.get("undone"):
                ev["undone"] = True
                break
        self._event("undo", removed_t=removed)
        return True

    def to_record(self, completed, end_reason, extra=None):
        active = min(self.elapsed(), self.planned)
        paused = self.paused_seconds
        if self.paused and self.pause_wall_start:
            paused += max(0.0, time.time() - self.pause_wall_start)
        end = now_local()
        events = [dict(ev) for ev in self.events]
        events.append({"type": "end", "at": end.isoformat(timespec="seconds"), "t": round(active, 1),
                       "reason": end_reason})
        rec = {
            "format": 2,
            "app_version": APP_VERSION,
            "id": self.id,
            "start": self.start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
            "planned_seconds": int(round(self.planned)),
            "active_seconds": round(active, 1),
            "paused_seconds": round(paused),
            "completed": bool(completed),
            "end_reason": end_reason,
            "distractions": [t for t in self.distractions if t <= active + 0.5],
            "pause_count": self.pause_count,
            "events": events,
            "machine": socket.gethostname() or platform.node() or "pc",
            "platform": platform.system(),
        }
        rec.update(extra or {})
        return rec


# ---------------------------------------------------------------------------
# Sounds (generated once as small WAV files; no audio libraries needed)
# ---------------------------------------------------------------------------

class Sounds:
    RATE = 22050

    def __init__(self, base_dir, root):
        self.dir = os.path.join(base_dir, "sounds")
        self.root = root
        self.procs = []
        self.player = None
        if not IS_WINDOWS:
            for cmd in (["paplay"], ["pw-play"], ["aplay", "-q"], ["afplay"]):
                if shutil.which(cmd[0]):
                    self.player = cmd
                    break

    def _write(self, name, samples):
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, name)
        data = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for v in samples))
        if sys.byteorder == "big":
            data.byteswap()
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(data.tobytes())
        return path

    def _buzz_file(self, volume):
        name = f"buzz_v1_{volume}.wav"
        path = os.path.join(self.dir, name)
        if os.path.exists(path):
            return path
        for old in glob.glob(os.path.join(self.dir, "buzz_*.wav")):
            try:
                os.remove(old)
            except OSError:
                pass
        rate, n = self.RATE, int(self.RATE * 0.24)
        amp = 0.9 * volume / 100.0
        out = []
        for i in range(n):
            t = i / rate
            # Low sawtooth (harmonics keep it audible on laptop speakers) with a rough tremolo
            s = sum(math.sin(2 * math.pi * 110.0 * k * t) / k for k in range(1, 16)) * 0.5
            trem = 0.75 + 0.25 * math.sin(2 * math.pi * 32 * t)
            env = min(1.0, t / 0.008, (n - i) / rate / 0.05)
            out.append(s * trem * env * amp)
        return self._write(name, out)

    def _chime_file(self):
        path = os.path.join(self.dir, "chime_v1.wav")
        if os.path.exists(path):
            return path
        rate, n = self.RATE, int(self.RATE * 2.2)
        notes = [(0.0, 587.33), (0.28, 880.0)]
        out = []
        for i in range(n):
            t = i / rate
            v = 0.0
            for start, f in notes:
                if t >= start:
                    tt = t - start
                    env = min(1.0, tt / 0.01) * math.exp(-tt / 0.55)
                    v += (math.sin(2 * math.pi * f * tt)
                          + 0.25 * math.sin(4 * math.pi * f * tt) * math.exp(-tt / 0.2)) * env
            out.append(v * 0.22)
        return self._write("chime_v1.wav", out)

    def play(self, path):
        try:
            if IS_WINDOWS:
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                return
            if self.player:
                self.procs = [p for p in self.procs if p.poll() is None]
                self.procs.append(subprocess.Popen(self.player + [path], stdout=subprocess.DEVNULL,
                                                   stderr=subprocess.DEVNULL))
                return
        except Exception:
            traceback.print_exc()
        self.root.bell()

    def buzz(self, volume):
        volume = int(round(volume / 5.0) * 5)
        if volume > 0:
            self.play(self._buzz_file(volume))

    def chime(self):
        self.play(self._chime_file())


# ---------------------------------------------------------------------------
# Global keyboard shortcut (optional, via pynput)
# ---------------------------------------------------------------------------

MODIFIERS = ["ctrl", "alt", "shift", "cmd"]
MOD_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win" if IS_WINDOWS else "Super"}
KEY_ALIASES = {"control": "ctrl", "ctl": "ctrl", "option": "alt", "opt": "alt", "win": "cmd",
               "windows": "cmd", "super": "cmd", "meta": "cmd", "command": "cmd", "escape": "esc",
               "return": "enter", "pgup": "page_up", "pgdn": "page_down", "del": "delete", "ins": "insert"}
NAMED_KEYS = ({f"f{i}" for i in range(1, 25)} | {
    "space", "enter", "tab", "esc", "pause", "scroll_lock", "insert", "delete", "home", "end",
    "page_up", "page_down", "up", "down", "left", "right", "print_screen", "menu", "num_lock",
    "backspace", "caps_lock", "media_play_pause", "media_next", "media_previous"})


def parse_combo(text):
    """'Ctrl+Alt+D' -> (frozenset({'ctrl','alt'}), 'd'). Raises ValueError with a friendly message."""
    parts = [p.strip().strip("<>").lower().replace(" ", "_") for p in text.split("+") if p.strip()]
    mods, key = set(), None
    for p in parts:
        p = KEY_ALIASES.get(p, p)
        if p in MODIFIERS:
            mods.add(p)
        elif key is None:
            key = p
        else:
            raise ValueError("Use one normal key plus modifiers, for example Ctrl+Alt+D.")
    if key is None:
        raise ValueError("Add a normal key after the modifiers, for example Ctrl+Alt+D.")
    if not (len(key) == 1 or key in NAMED_KEYS or (key.startswith("vk") and key[2:].isdigit())):
        raise ValueError(f"Unknown key '{key}'. Try a letter, a number or a key like F8.")
    if (len(key) == 1 or key in ("space", "enter", "tab", "backspace")) and not (mods - {"shift"}):
        raise ValueError("Add Ctrl or Alt to that key, otherwise normal typing would log distractions.")
    return frozenset(mods), key


def format_combo(mods, key):
    if len(key) == 1:
        label = key.upper()
    elif key.startswith("vk"):
        label = f"VK{key[2:]}"
    else:
        label = key.replace("_", " ").title().replace(" ", "")
        label = {"PageUp": "Page Up", "PageDown": "Page Down", "ScrollLock": "Scroll Lock",
                 "PrintScreen": "Print Screen", "NumLock": "Num Lock", "CapsLock": "Caps Lock"}.get(label, label)
    return "+".join([MOD_LABELS[m] for m in MODIFIERS if m in mods] + [label])


class HotkeyManager:
    """Watches the keyboard for one combination and posts events to the Tk queue."""

    def __init__(self, out_queue):
        self.q = out_queue
        self.kb = None
        self.error = None
        try:
            from pynput import keyboard
            self.kb = keyboard
        except Exception as exc:  # not installed, or no display available
            self.error = str(exc)
        self.listener = None
        self.target = None
        self.recording = False
        self.mods_down = set()
        self.keys_down = set()

    @property
    def available(self):
        return self.kb is not None

    def _ensure_listener(self):
        if self.listener is None or not self.listener.is_alive():
            self.mods_down.clear()
            self.keys_down.clear()
            self.listener = self.kb.Listener(on_press=self._on_press, on_release=self._on_release)
            self.listener.daemon = True
            self.listener.start()

    def start(self, mods, key):
        self.target = (frozenset(mods), key)
        self._ensure_listener()

    def stop(self):
        self.target = None
        self.recording = False
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    def record(self):
        self.recording = True
        self._ensure_listener()

    def _name(self, key):
        kb = self.kb
        if isinstance(key, kb.Key):
            name = key.name
            if name == "alt_gr":
                return "alt_gr"
            for m in MODIFIERS:
                if name == m or name.startswith(m + "_"):
                    return m
            return name
        if isinstance(key, kb.KeyCode):
            ch = key.char
            if ch and len(ch) == 1:
                if ord(ch) < 32:  # Ctrl+letter arrives as a control character on Windows
                    return chr(ord(ch) + 96)
                return ch.lower()
            vk = getattr(key, "vk", None)
            if vk is not None:
                if IS_WINDOWS and (65 <= vk <= 90 or 48 <= vk <= 57):
                    return chr(vk).lower()
                return f"vk{vk}"
        return None

    def _on_press(self, key):
        try:
            name = self._name(key)
            if name is None:
                return
            if name in MODIFIERS or name == "alt_gr":
                self.mods_down.add(name)
                return
            mods = frozenset(m for m in self.mods_down if m in MODIFIERS)
            if self.recording:
                self.recording = False
                self.q.put(("hotkey_recorded", None if name == "esc" else format_combo(mods, name)))
                return
            if self.target and name == self.target[1] and mods == self.target[0] and name not in self.keys_down:
                self.q.put(("distraction", "hotkey"))
            self.keys_down.add(name)
        except Exception:
            traceback.print_exc()

    def _on_release(self, key):
        try:
            name = self._name(key)
            self.mods_down.discard(name)
            self.keys_down.discard(name)
        except Exception:
            traceback.print_exc()


# ---------------------------------------------------------------------------
# Talking to a running copy (single instance + `--log` command)
# ---------------------------------------------------------------------------

def ipc_send(message, timeout=1.0):
    try:
        with socket.create_connection((IPC_HOST, IPC_PORT), timeout=timeout) as s:
            s.sendall((message + "\n").encode())
            return s.recv(16).startswith(b"ok")
    except OSError:
        return False


class IPCServer(threading.Thread):
    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.q = out_queue
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if IS_WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        elif not IS_WINDOWS:  # lets a restarted copy reuse the port straight away
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((IPC_HOST, IPC_PORT))
        self.sock.listen(5)

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(2)
                    cmd = conn.recv(64).decode(errors="ignore").strip()
                    if cmd in ("distraction", "show"):
                        self.q.put((cmd, "ipc"))
                        conn.sendall(b"ok\n")
                    else:
                        conn.sendall(b"err\n")
                except OSError:
                    pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The floating widget
# ---------------------------------------------------------------------------

class FocusWidget:
    DOT_SIZES = {"small": 8, "medium": 11, "large": 15}
    FONT_SIZES = {"small": 8, "medium": 9, "large": 11}
    KEY = "#fe01fd"  # colour made transparent on Windows, for round corners

    def __init__(self, app, preview=False):
        self.app = app
        self.preview = preview
        self.pal = app.dot_palette()
        self.body, self.dot = self.pal["body"], self.pal["dot"]
        self.text = None
        self.w = self.h = 0
        self.x = self.y = None
        self.jobs = []
        self.drag = None
        self.menu_open_until = 0.0
        self.font = tkfont.Font(family=app.font_family)

        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        bg = self.KEY if IS_WINDOWS else self.body
        self.win.configure(bg=bg)
        try:
            self.win.attributes("-topmost", True)
            if IS_WINDOWS:
                self.win.attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.win, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._menu)
        if IS_MAC:
            self.canvas.bind("<Button-2>", self._menu)
        self.set_opacity(app.settings["widget_opacity"])
        self.top_job = None
        self.reconfigure()
        self.win.deiconify()
        self._no_activate()
        self._keep_on_top()

    def _no_activate(self):
        """On Windows, stop clicks on the widget from taking focus away from your reading app."""
        if not IS_WINDOWS:
            return  # override-redirect windows never take focus on X11
        try:
            import ctypes
            self.win.update_idletasks()
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            gwl_exstyle, ws_ex_noactivate, ws_ex_toolwindow = -20, 0x08000000, 0x00000080
            style = user32.GetWindowLongW(hwnd, gwl_exstyle)
            user32.SetWindowLongW(hwnd, gwl_exstyle, style | ws_ex_noactivate | ws_ex_toolwindow)
        except Exception:
            traceback.print_exc()

    # -- appearance -------------------------------------------------------
    def reconfigure(self):
        size = self.app.settings["widget_size"]
        self.d = max(6, round(self.DOT_SIZES.get(size, 11) * self.app.scale))
        self.pad = max(5, round(self.d * 0.65))
        self.font.configure(size=self.FONT_SIZES.get(size, 9))
        self.text = None
        self.render()

    def retheme(self):
        self._cancel_anim()
        self.pal = self.app.dot_palette()
        self.body, self.dot = self.pal["body"], self.pal["dot"]
        self.render()

    def set_opacity(self, value):
        try:
            self.win.attributes("-alpha", max(0.1, min(1.0, float(value))))
        except tk.TclError:
            pass

    def _current_text(self):
        mode = self.app.settings["display"]
        sess = self.app.session
        if self.preview and sess is None:
            remaining, count, paused = self.app.planned_minutes() * 60, 0, False
        elif sess is not None:
            remaining, count, paused = sess.remaining(), len(sess.distractions), sess.paused
        else:
            return "", False
        if mode == "dot":
            return "", paused
        if paused:
            return "paused", True
        text = "<1m" if remaining < 60 else f"{math.ceil(remaining / 60)}m"
        if mode == "time_count":
            text += f" · {count}"
        return text, False

    def render(self):
        text, paused = self._current_text()
        h = self.d + 2 * self.pad
        gap = round(self.pad * 0.8)
        w = h if not text else self.pad + self.d + gap + self.font.measure(text) + self.pad + round(self.pad * 0.3)
        if text != self.text or w != self.w or h != self.h:
            self.text = text
            self._apply_geometry(w, h)
        c = self.canvas
        c.delete("all")
        if IS_WINDOWS:
            rounded_rect(c, 0, 0, w, h, h / 2, fill=self.body, outline=self.body)
        else:
            c.configure(bg=self.body)
            self.win.configure(bg=self.body)
        x0, y0, d = self.pad, self.pad, self.d
        if paused:
            lw = max(1, round(1.5 * self.app.scale))
            c.create_oval(x0 + lw, y0 + lw, x0 + d - lw, y0 + d - lw, outline=self.dot, width=lw)
        else:
            c.create_oval(x0, y0, x0 + d, y0 + d, fill=self.dot, outline=self.dot)
        if text:
            c.create_text(x0 + d + gap, h / 2, text=text, anchor="w", fill=self.pal["text"], font=self.font)

    def _apply_geometry(self, w, h):
        vx, vy, vw, vh = virtual_screen(self.win)
        if self.x is None:
            sx, sy = self.app.settings["widget_x"], self.app.settings["widget_y"]
            if (isinstance(sx, (int, float)) and isinstance(sy, (int, float))
                    and vx <= sx <= vx + vw - 8 and vy <= sy <= vy + vh - 8):
                self.x, self.y = int(sx), int(sy)
            else:
                sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
                self.x, self.y = sw - w - self.app.px(28), sh - h - self.app.px(84)
        elif w != self.w and self.x + self.w / 2 > vx + vw / 2:
            self.x -= w - self.w  # on the right half of the screen, grow leftwards
        self.w, self.h = w, h
        self.canvas.configure(width=w, height=h)
        self.win.geometry(f"{w}x{h}+{int(self.x)}+{int(self.y)}")

    def _keep_on_top(self):
        try:
            if time.monotonic() > self.menu_open_until:
                self.win.attributes("-topmost", True)
                if not IS_WINDOWS:
                    self.win.lift()
            self.top_job = self.win.after(2000, self._keep_on_top)
        except tk.TclError:
            pass

    # -- feedback ---------------------------------------------------------
    def _cancel_anim(self):
        for job in self.jobs:
            try:
                self.win.after_cancel(job)
            except tk.TclError:
                pass
        self.jobs = []
        self.body, self.dot = self.pal["body"], self.pal["dot"]

    def _set_colors(self, body, dot):
        self.body, self.dot = body, dot
        self.render()

    def feedback(self, mode):
        self._cancel_anim()
        pal = self.pal
        if mode in ("flash", "flash_buzz"):
            self._set_colors(pal["red_body"], pal["red_dot"])
            steps = 10
            for i in range(1, steps + 1):
                t = i / steps
                self.jobs.append(self.win.after(350 + i * 45, lambda t=t: self._set_colors(
                    mix(pal["red_body"], pal["body"], t), mix(pal["red_dot"], pal["dot"], t))))
        else:
            self._set_colors(pal["body"], pal["blink"])
            self.jobs.append(self.win.after(170, lambda: self._set_colors(pal["body"], pal["dot"])))
        if mode in ("buzz", "flash_buzz"):
            self.app.sounds.buzz(self.app.settings["buzz_volume"])

    # -- mouse ------------------------------------------------------------
    def _press(self, e):
        self.drag = {"x": e.x_root, "y": e.y_root, "wx": self.x, "wy": self.y, "moving": False}

    def _motion(self, e):
        if not self.drag:
            return
        dx, dy = e.x_root - self.drag["x"], e.y_root - self.drag["y"]
        if not self.drag["moving"] and abs(dx) + abs(dy) > 4:
            self.drag["moving"] = True
        if self.drag["moving"]:
            self.x, self.y = self.drag["wx"] + dx, self.drag["wy"] + dy
            self.win.geometry(f"+{int(self.x)}+{int(self.y)}")

    def _release(self, _e):
        drag, self.drag = self.drag, None
        if drag and drag["moving"]:
            self.app.settings["widget_x"], self.app.settings["widget_y"] = int(self.x), int(self.y)
        elif self.preview and self.app.session is None:
            self.feedback(self.app.settings["feedback"])
        else:
            self.app.log_distraction("click")

    def _menu(self, e):
        app = self.app
        m = tk.Menu(self.win, tearoff=0)
        sess = app.session
        if sess is not None:
            m.add_command(label="Undo last distraction", command=app.undo_distraction,
                          state="normal" if sess.distractions else "disabled")
            m.add_command(label="Resume" if sess.paused else "Pause", command=app.toggle_pause)
            m.add_command(label="End session…", command=app.end_session_early)
            m.add_separator()
        elif self.preview:
            m.add_command(label="Close preview", command=app.close_preview)
        m.add_command(label=f"Open {APP_NAME}", command=app.show_main)
        self.menu_open_until = time.monotonic() + 15
        m.bind("<Unmap>", lambda _e: setattr(self, "menu_open_until", 0.0))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
        if IS_WINDOWS:  # tk_popup blocks until the menu closes on Windows
            self.menu_open_until = 0.0

    def destroy(self):
        self._cancel_anim()
        try:
            if self.top_job:
                self.win.after_cancel(self.top_job)
            self.win.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Charts (drawn on Tk canvases, with hover tooltips)
# ---------------------------------------------------------------------------

class Chart(tk.Canvas):
    def __init__(self, parent, app, height):
        super().__init__(parent, height=app.px(height), bg=C.surface, highlightthickness=1,
                         highlightbackground=C.border, highlightcolor=C.border, bd=0)
        self.app = app
        self.s = app.scale
        self.hits = []
        self.hit_mode = "box"
        self.plot = None
        self.hovered = None
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._clear_hover())

    def retheme(self):
        self.configure(bg=C.surface, highlightbackground=C.border, highlightcolor=C.border)
        self.redraw()

    def redraw(self):
        self.delete("all")
        self.hits = []
        self.hovered = None
        w, h = self.winfo_width(), self.winfo_height()
        if w > 60 and h > 60:
            self.draw(w, h)

    def draw(self, w, h):
        raise NotImplementedError

    def title(self, title, subtitle):
        p = self.app.px
        self.create_text(p(16), p(14), text=title, anchor="nw", font=self.app.f_h3, fill=C.ink)
        if subtitle:
            self.create_text(p(16), p(14) + self.app.f_h3.metrics("linespace") + p(2), text=subtitle,
                             anchor="nw", font=self.app.f_small, fill=C.muted)

    def empty(self, w, h, text):
        self.create_text(w / 2, h / 2 + self.app.px(10), text=text, fill=C.muted, font=self.app.f_body,
                         width=w - self.app.px(40), justify="center")

    def y_axis(self, x0, x1, y0, y1, vmax, fmt=None):
        ticks, step = nice_ticks(vmax)
        top = ticks[-1]
        fmt = fmt or (lambda v: f"{round(v, 2):g}")
        for t in ticks:
            y = y1 - (t / top) * (y1 - y0)
            if t > 0:
                self.create_line(x0, y, x1, y, fill=C.grid)
            self.create_text(x0 - self.app.px(8), y, text=fmt(t), anchor="e", fill=C.muted, font=self.app.f_tiny)
        self.create_line(x0, y1, x1, y1, fill=C.axis)
        return lambda v: y1 - (v / top) * (y1 - y0)

    # -- hover ------------------------------------------------------------
    def _on_motion(self, e):
        target = None
        if self.hit_mode == "x" and self.hits and self.plot:
            x0, y0, x1, y1 = self.plot
            if x0 - 20 <= e.x <= x1 + 20 and y0 - 20 <= e.y <= y1 + 10:
                best = min(self.hits, key=lambda hit: abs(hit["x"] - e.x))
                if abs(best["x"] - e.x) <= max(self.app.px(14), best.get("reach", 0)):
                    target = best
        else:
            for hit in self.hits:
                bx0, by0, bx1, by1 = hit["box"]
                if bx0 <= e.x <= bx1 and by0 <= e.y <= by1:
                    target = hit
                    break
        if target is self.hovered:
            return
        self._clear_hover()
        if target is not None:
            self.hovered = target
            self._draw_hover(target)

    def _clear_hover(self):
        self.delete("hover")
        self.hovered = None

    def _draw_hover(self, hit):
        p = self.app.px
        if self.hit_mode == "x" and self.plot:
            _, y0, _, y1 = self.plot
            self.create_line(hit["x"], y0, hit["x"], y1, fill=C.axis, tags="hover")
        for (rx, ry, r, color) in hit.get("rings", []):
            self.create_oval(rx - r - p(2), ry - r - p(2), rx + r + p(2), ry + r + p(2),
                             fill=C.surface, outline=C.surface, tags="hover")
            self.create_oval(rx - r, ry - r, rx + r, ry + r, fill=color, outline=color, tags="hover")
        pad = p(7)
        tid = self.create_text(0, 0, text="\n".join(hit["lines"]), anchor="nw", fill=C.tip_fg,
                               font=self.app.f_small, tags="hover")
        bx0, by0, bx1, by1 = self.bbox(tid)
        tw, th = bx1 - bx0, by1 - by0
        ax, ay = hit["x"], hit["y"]
        tx = ax + p(12)
        if tx + tw + 2 * pad > self.winfo_width() - p(4):
            tx = ax - p(12) - tw - 2 * pad
        ty = ay - th - 2 * pad - p(10)
        if ty < p(4):
            ty = ay + p(14)
        self.coords(tid, tx + pad, ty + pad)
        rect = rounded_rect(self, tx, ty, tx + tw + 2 * pad, ty + th + 2 * pad, p(4),
                            fill=C.tip_bg, outline=C.tip_bg, tags="hover")
        self.tag_raise(tid, rect)


class TrendChart(Chart):
    """One value per session (dots) plus its moving average (line)."""

    def __init__(self, parent, app, height=240):
        super().__init__(parent, app, height)
        self.hit_mode = "x"
        self.data = None

    def set_data(self, **data):
        self.data = data
        self.redraw()

    def draw(self, w, h):
        d, p = self.data, self.app.px
        if not d:
            return
        self.title(d["title"], d["subtitle"])
        pts = d["points"]
        if not pts:
            self.empty(w, h, d["empty"])
            return
        # legend, top right
        lx = w - p(16)
        t2 = self.create_text(lx, p(20), text=f"{ROLLING_WINDOW}-session average", anchor="e",
                              fill=C.ink2, font=self.app.f_small)
        bx0 = self.bbox(t2)[0]
        self.create_line(bx0 - p(26), p(20), bx0 - p(8), p(20), fill=C.series, width=p(2), capstyle="round")
        t1 = self.create_text(bx0 - p(40), p(20), text="Each session", anchor="e", fill=C.ink2, font=self.app.f_small)
        bx1 = self.bbox(t1)[0]
        r0 = p(4)
        self.create_oval(bx1 - p(8) - 2 * r0, p(20) - r0, bx1 - p(8), p(20) + r0, fill=C.series_soft, outline=C.series_soft)

        x0, x1, y0, y1 = p(56), w - p(66), p(70), h - p(30)
        self.plot = (x0, y0, x1, y1)
        values = [pt["value"] for pt in pts]
        rolling = [pt["avg"] for pt in pts]
        top = max(values + rolling)
        if len(values) >= 5:  # keep one freak session from flattening everything else
            ranked = sorted(values)
            typical = max(max(rolling), ranked[int(0.9 * (len(ranked) - 1))])
            top = min(top, typical * 1.5)
        ymap = self.y_axis(x0, x1, y0, y1, top * 1.08, d.get("tick_fmt"))
        n = len(pts)
        xs = [(x0 + x1) / 2] if n == 1 else [x0 + i * (x1 - x0) / (n - 1) for i in range(n)]
        spacing = (x1 - x0) / max(1, n - 1)
        r = max(p(2), min(p(4), spacing * 0.35))

        ytop = y0 - p(4)
        for x, v in zip(xs, values):
            if ymap(v) < ytop:  # off the scale: a small up-arrow at the top edge
                self.create_polygon(x, ytop - r - p(2), x - r - p(1), ytop + r, x + r + p(1), ytop + r,
                                    fill=C.series_soft, outline=C.series_soft)
            else:
                self.create_oval(x - r, ymap(v) - r, x + r, ymap(v) + r, fill=C.series_soft, outline=C.surface)
        if n >= 2:
            coords = []
            for x, v in zip(xs, rolling):
                coords += [x, ymap(v)]
            self.create_line(*coords, fill=C.series, width=p(2), capstyle="round", joinstyle="round")
        # direct label for the latest average
        self.create_text(xs[-1] + p(8), ymap(rolling[-1]), text=d["fmt"](rolling[-1]), anchor="w",
                         fill=C.ink, font=self.app.f_small_bold)

        # x labels: a handful of dates, evenly spread
        k = min(n, max(2, int((x1 - x0) / p(90))))
        idxs = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)}) if n > 1 else [0]
        for i in idxs:
            anchor = "n" if 0 < i < n - 1 or n == 1 else ("nw" if i == 0 else "ne")
            self.create_text(xs[i], y1 + p(8), text=short_date(pts[i]["date"]), anchor=anchor,
                             fill=C.muted, font=self.app.f_tiny)

        for x, pt in zip(xs, pts):
            y = max(ymap(pt["value"]), ytop)
            self.hits.append({"x": x, "y": y, "lines": pt["tip"], "reach": spacing / 2,
                              "rings": [(x, y, r + p(1), C.series_strong)]})


class BarChart(Chart):
    def __init__(self, parent, app, height=250):
        super().__init__(parent, app, height)
        self.data = None

    def set_data(self, **data):
        self.data = data
        self.redraw()

    def draw(self, w, h):
        d, p = self.data, self.app.px
        if not d:
            return
        self.title(d["title"], d["subtitle"])
        cats = d["cats"]
        if not any(c["value"] is not None for c in cats):
            self.empty(w, h, d["empty"])
            return
        two_lines = any(c.get("sub") for c in cats)
        x0, x1, y0, y1 = p(44), w - p(16), p(78), h - p(46 if two_lines else 32)
        vmax = max(c["value"] for c in cats if c["value"] is not None)
        ymap = self.y_axis(x0, x1, y0, y1, vmax * 1.12 if vmax > 0 else 1)
        slot = (x1 - x0) / len(cats)
        bw = min(p(44), slot * 0.6)
        for i, c in enumerate(cats):
            cx = x0 + slot * (i + 0.5)
            self.create_text(cx, y1 + p(7), text=c["label"], anchor="n", fill=C.ink2, font=self.app.f_tiny)
            if c.get("sub"):
                self.create_text(cx, y1 + p(7) + self.app.f_tiny.metrics("linespace"), text=c["sub"],
                                 anchor="n", fill=C.muted, font=self.app.f_tiny)
            if c["value"] is None:
                self.create_text(cx, y1 - p(10), text="–", fill=C.muted, font=self.app.f_small)
            else:
                top = ymap(c["value"])
                color = C.series if c.get("highlight") else C.series_soft
                if y1 - top >= 1:
                    rounded_rect(self, cx - bw / 2, top, cx + bw / 2, y1, p(4), 0, fill=color, outline=color)
                self.create_text(cx, top - p(4), text=d["fmt"](c["value"]), anchor="s", fill=C.ink2,
                                 font=self.app.f_tiny)
            self.hits.append({"box": (cx - slot / 2, y0 - p(20), cx + slot / 2, y1 + p(30)),
                              "x": cx, "y": ymap(c["value"]) if c["value"] is not None else y1,
                              "lines": c["tip"]})


class TimelineStrip(Chart):
    """A session drawn as a bar: ticks are distractions, the blue run is the longest focus."""

    def __init__(self, parent, app):
        super().__init__(parent, app, 70)
        self.configure(highlightthickness=0)
        self.rec = None

    def set_record(self, rec):
        self.rec = rec
        self.redraw()

    def draw(self, w, h):
        rec, p = self.rec, self.app.px
        if not rec:
            return
        m = rec["_m"]
        dur = max(m["seconds"], 1.0)
        x0, x1, y = p(4), w - p(4), p(34)
        th = p(6)
        xmap = lambda t: x0 + (t / dur) * (x1 - x0)  # noqa: E731
        rounded_rect(self, x0, y - th / 2, x1, y + th / 2, th / 2, fill=C.grid, outline=C.grid)
        lx0, lx1 = xmap(m["longest_start"]), xmap(m["longest_start"] + m["longest"])
        if lx1 - lx0 >= 2:
            rounded_rect(self, lx0, y - th / 2, lx1, y + th / 2, th / 2, fill=C.series, outline=C.series)
        label = f"Longest focus {fmt_duration(m['longest'])}"
        lw = self.app.f_tiny.measure(label)
        cx = min(max((lx0 + lx1) / 2, x0 + lw / 2), x1 - lw / 2)
        self.create_text(cx, y - p(10), text=label, anchor="s", fill=C.series_strong, font=self.app.f_tiny)
        for t in m["times"]:
            x = xmap(t)
            self.create_line(x, y - p(9), x, y + p(9), fill=C.ink2, width=p(2))
            self.hits.append({"box": (x - p(6), y - p(14), x + p(6), y + p(14)), "x": x, "y": y - p(6),
                              "lines": [f"Distracted at {fmt_offset(t)}"]})
        self.create_text(x0, y + p(14), text="0:00", anchor="nw", fill=C.muted, font=self.app.f_tiny)
        self.create_text(x1, y + p(14), text=fmt_offset(dur), anchor="ne", fill=C.muted, font=self.app.f_tiny)


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=C.page, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))

    def retheme(self):
        self.canvas.configure(bg=C.page)

    def scroll(self, units):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    @staticmethod
    def route_wheel(root, e):
        """Scroll whichever ScrollFrame is under the mouse pointer."""
        try:
            w = root.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        while w is not None and not isinstance(w, ScrollFrame):
            w = getattr(w, "master", None)
        if w is None:
            return
        if getattr(e, "num", None) in (4, 5):
            w.scroll(-3 if e.num == 4 else 3)
        elif e.delta:
            w.scroll(int(-e.delta / (1 if IS_MAC else 40)) or (-1 if e.delta > 0 else 1))


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------

class App:
    PERIODS = [("Last 7 days", 7), ("Last 30 days", 30), ("Last 90 days", 90),
               ("Last 12 months", 365), ("All time", None)]
    PRESETS = (10, 15, 20, 30, 45, 60)

    def __init__(self, root, ipc=None):
        self.root = root
        self.ipc = ipc
        self.cfg_dir = config_dir()
        os.makedirs(self.cfg_dir, exist_ok=True)
        self.settings = Settings(os.path.join(self.cfg_dir, "settings.json"))
        self.q = queue.Queue()
        if ipc is not None:
            ipc.q = self.q
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.session = None
        self.widget = None
        self.preview = None
        self.sessions = []
        self.tick_job = None
        self.last_autosave = 0.0
        self.last_result = None
        self.last_result_fresh = False
        self.hotkey_status_role = ("", "muted")
        self.update_status_role = ("", "muted")
        self.pending_update = None
        self.update_checking = False
        self.csv_note = ""

        root.report_callback_exception = self._report_error
        self.system_theme = detect_system_theme()
        self.theme = self._wanted_theme()
        C.__dict__.update(DARK if self.theme == "dark" else LIGHT)
        self._setup_fonts()
        self._setup_style()
        self.sounds = Sounds(self.cfg_dir, root)
        self.hotkeys = HotkeyManager(self.q)
        self.store = SessionStore(self.data_folder())

        self._build_ui()
        self._flush_unsaved()
        self.reload_sessions()
        if self.sessions:
            self.show_results(self.sessions[-1], fresh=False)
        self.apply_hotkey()
        self.auto_export()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self._poll_queue)
        root.after(400, self._check_unfinished)
        root.after(100, self._set_title_bar)
        root.after(3000, self._poll_theme)
        root.after(600, self._after_update_notice)
        if self.settings["check_updates"]:
            root.after(2500, lambda: self.check_updates(manual=False))

    # -- basics ---------------------------------------------------------------
    def px(self, n):
        return int(round(n * self.scale))

    def data_folder(self):
        return self.settings["data_folder"] or default_data_dir()

    def planned_minutes(self):
        try:
            value = float(self.duration_var.get())
        except (ValueError, tk.TclError, AttributeError):
            value = self.settings["duration_minutes"]
        return max(1, min(600, int(round(value))))

    def _report_error(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        sys.stderr.write(text)
        try:
            with open(os.path.join(self.cfg_dir, "error.log"), "a", encoding="utf-8") as f:
                f.write(f"--- {now_local().isoformat()}\n{text}\n")
        except OSError:
            pass
        messagebox.showerror(APP_NAME, f"Something went wrong:\n{val}\n\nDetails were written to error.log in\n{self.cfg_dir}")

    def _setup_fonts(self):
        families = set(tkfont.families(self.root))
        preferred = (["Segoe UI"] if IS_WINDOWS else []) + ["Inter", "Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans"]
        self.font_family = next((f for f in preferred if f in families),
                                tkfont.nametofont("TkDefaultFont").actual("family"))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=self.font_family, size=10)
            except tk.TclError:
                pass
        F = lambda size, weight="normal": tkfont.Font(family=self.font_family, size=size, weight=weight)  # noqa: E731
        self.f_body = F(10)
        self.f_small = F(9)
        self.f_small_bold = F(9, "bold")
        self.f_tiny = F(8)
        self.f_h1 = F(17, "bold")
        self.f_h2 = F(12, "bold")
        self.f_h3 = F(11, "bold")
        self.f_big = F(22, "bold")

    def _setup_style(self):
        r = self.root
        r.configure(bg=C.page)
        st = ttk.Style(r)
        if st.theme_use() != "clam":
            st.theme_use("clam")
        p = self.px
        st.configure(".", background=C.page, foreground=C.ink, font=self.f_body, bordercolor=C.border,
                     lightcolor=C.page, darkcolor=C.page, troughcolor=C.grid, focuscolor=C.focus,
                     selectbackground=C.select, selectforeground=C.ink, insertcolor=C.ink)
        st.configure("TFrame", background=C.page)
        st.configure("Card.TFrame", background=C.surface, bordercolor=C.border, relief="solid", borderwidth=1)
        st.configure("CardInner.TFrame", background=C.surface, borderwidth=0, relief="flat")
        st.configure("TLabel", background=C.page, foreground=C.ink)
        st.configure("H1.TLabel", font=self.f_h1)
        st.configure("H2.TLabel", font=self.f_h2)
        st.configure("H3.TLabel", font=self.f_h3)
        st.configure("Sub.TLabel", foreground=C.ink2)
        st.configure("Muted.TLabel", foreground=C.muted, font=self.f_small)
        st.configure("Card.TLabel", background=C.surface)
        st.configure("CardH2.TLabel", background=C.surface, font=self.f_h2)
        st.configure("CardBig.TLabel", background=C.surface, font=self.f_big)
        st.configure("CardMuted.TLabel", background=C.surface, foreground=C.muted, font=self.f_small)
        st.configure("CardSub.TLabel", background=C.surface, foreground=C.ink2, font=self.f_small)

        st.configure("TButton", background=C.surface, foreground=C.ink, bordercolor=C.axis, lightcolor=C.surface,
                     darkcolor=C.surface, padding=(p(12), p(5)), width=0)
        st.map("TButton", background=[("disabled", C.page), ("pressed", C.grid), ("active", C.hover)],
               foreground=[("disabled", C.muted)], lightcolor=[("active", C.hover)], darkcolor=[("active", C.hover)])
        st.configure("Accent.TButton", background=C.accent, foreground=C.on_accent, bordercolor=C.accent,
                     lightcolor=C.accent, darkcolor=C.accent, padding=(p(26), p(10)), font=self.f_h3)
        st.map("Accent.TButton",
               background=[("pressed", C.accent_press), ("active", C.accent_hover)],
               lightcolor=[("pressed", C.accent_press), ("active", C.accent_hover)],
               darkcolor=[("pressed", C.accent_press), ("active", C.accent_hover)],
               bordercolor=[("pressed", C.accent_press), ("active", C.accent_hover)])
        st.configure("Seg.Toolbutton", background=C.surface, foreground=C.ink2, bordercolor=C.axis,
                     lightcolor=C.surface, darkcolor=C.surface, padding=(p(12), p(6)), anchor="center")
        st.map("Seg.Toolbutton",
               background=[("selected", C.accent), ("active", C.hover)],
               foreground=[("selected", C.on_accent)],
               bordercolor=[("selected", C.accent)],
               lightcolor=[("selected", C.accent), ("active", C.hover)],
               darkcolor=[("selected", C.accent), ("active", C.hover)])

        st.configure("TNotebook", background=C.page, borderwidth=0, tabmargins=(p(16), p(10), p(16), 0))
        st.configure("TNotebook.Tab", background=C.page, foreground=C.ink2, padding=(p(16), p(7)),
                     bordercolor=C.page, lightcolor=C.page, darkcolor=C.page)
        st.map("TNotebook.Tab", background=[("selected", C.surface), ("active", C.hover)],
               foreground=[("selected", C.ink)], bordercolor=[("selected", C.border)],
               lightcolor=[("selected", C.surface)])
        st.configure("Treeview", background=C.surface, fieldbackground=C.surface, foreground=C.ink,
                     rowheight=p(26), bordercolor=C.border)
        st.configure("Treeview.Heading", background=C.page, foreground=C.ink2, font=self.f_small_bold,
                     relief="flat", bordercolor=C.border, lightcolor=C.page, darkcolor=C.page)
        st.map("Treeview.Heading", background=[("active", C.hover)])
        st.map("Treeview", background=[("selected", C.select)], foreground=[("selected", C.ink)])
        for name in ("TRadiobutton", "TCheckbutton"):
            st.configure(name, background=C.page, indicatorbackground=C.field, indicatormargin=(0, 0, p(6), 0),
                         upperbordercolor=C.axis, lowerbordercolor=C.axis)
            st.map(name, background=[("active", C.page)], indicatorbackground=[("selected", C.accent), ("pressed", C.grid)],
                   indicatorforeground=[("selected", C.on_accent)])
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            st.configure(name, fieldbackground=C.field, foreground=C.ink, background=C.surface, bordercolor=C.axis,
                         lightcolor=C.field, darkcolor=C.field, padding=(p(6), p(4)), arrowcolor=C.ink2)
            st.map(name, fieldbackground=[("readonly", C.field), ("disabled", C.page)],
                   foreground=[("disabled", C.muted)], background=[("active", C.hover)])
        st.map("TCombobox", selectbackground=[("readonly", C.field)], selectforeground=[("readonly", C.ink)])
        st.configure("Horizontal.TScale", background=C.surface, troughcolor=C.grid, bordercolor=C.axis,
                     lightcolor=C.surface, darkcolor=C.surface)
        st.map("Horizontal.TScale", background=[("active", C.hover)])
        st.configure("Vertical.TScrollbar", background=C.grid, troughcolor=C.page, bordercolor=C.page,
                     arrowcolor=C.muted, lightcolor=C.grid, darkcolor=C.grid)
        st.map("Vertical.TScrollbar", background=[("active", C.axis)])
        # classic Tk pieces (menus, dialogs, combobox lists) read the option database
        for pattern, value in (
                ("*Background", C.page), ("*Foreground", C.ink), ("*activeBackground", C.hover),
                ("*activeForeground", C.ink), ("*selectBackground", C.select), ("*selectForeground", C.ink),
                ("*highlightBackground", C.page), ("*highlightColor", C.focus), ("*insertBackground", C.ink),
                ("*troughColor", C.grid), ("*Entry.background", C.field), ("*Listbox.background", C.field),
                ("*TCombobox*Listbox.background", C.field), ("*TCombobox*Listbox.foreground", C.ink),
                ("*TCombobox*Listbox.selectBackground", C.select), ("*TCombobox*Listbox.selectForeground", C.ink),
                ("*Menu.background", C.surface), ("*Menu.foreground", C.ink),
                ("*Menu.activeBackground", C.select), ("*Menu.activeForeground", C.ink),
                ("*Menu.disabledForeground", C.muted)):
            r.option_add(pattern, value)

    # -- light / dark theme ---------------------------------------------------------
    def _wanted_theme(self):
        mode = self.settings["appearance"]
        return mode if mode in ("light", "dark") else self.system_theme

    def dot_palette(self):
        mode = self.settings["dot_theme"]
        mode = mode if mode in ("light", "dark") else self.system_theme
        return DOT_DARK if mode == "dark" else DOT_LIGHT

    def _poll_theme(self):
        if "system" in (self.settings["appearance"], self.settings["dot_theme"]):
            found = detect_system_theme()
            if found != self.system_theme:
                self.system_theme = found
                self.apply_theme()
        self.root.after(3000, self._poll_theme)

    def apply_theme(self):
        wanted = self._wanted_theme()
        if wanted != self.theme:
            self.theme = wanted
            C.__dict__.update(DARK if wanted == "dark" else LIGHT)
            self._setup_style()
            stack = [self.root]
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                if hasattr(w, "retheme"):
                    w.retheme()
            self.tree.tag_configure("odd", background=C.stripe)
            try:  # an already-created combobox drop-down keeps its colours otherwise
                popdown = self.root.tk.call("ttk::combobox::PopdownWindow", self.period_combo)
                self.root.tk.call(f"{popdown}.f.l", "configure", "-background", C.field, "-foreground", C.ink,
                                  "-selectbackground", C.select, "-selectforeground", C.ink)
            except tk.TclError:
                pass
            self._set_title_bar()
            self.refresh_progress()
            if self.last_result is not None:
                self.show_results(self.last_result, fresh=self.last_result_fresh)
            self._hotkey_msg(*self.hotkey_status_role)
            self._update_msg(*self.update_status_role)
        for w in (self.widget, self.preview):
            if w is not None and w.pal is not self.dot_palette():
                w.retheme()

    def _set_title_bar(self):
        """Dark title bar on Windows 10/11 (Linux window managers follow the system theme themselves)."""
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            value = ctypes.c_int(1 if self.theme == "dark" else 0)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer and older Windows builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                              ctypes.sizeof(value)) == 0:
                    break
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
        except Exception:
            pass

    def _set_icon(self):
        size = 32
        img = tk.PhotoImage(width=size, height=size)
        rows = []
        for y in range(size):
            row = []
            for x in range(size):
                dx, dy = abs(x - 15.5), abs(y - 15.5)
                inside = (max(dx - 9, 0) ** 2 + max(dy - 9, 0) ** 2) <= 36 and dx < 15.5 and dy < 15.5
                dot = math.hypot(x - 15.5, y - 15.5) <= 6.5
                row.append(DOT_DARK["blink"] if dot else (DOT_DARK["body"] if inside else C.page))
            rows.append("{" + " ".join(row) + "}")
        img.put(" ".join(rows))
        self._icon = img
        try:
            self.root.iconphoto(True, img)
        except tk.TclError:
            pass

    # -- layout ---------------------------------------------------------------
    def _build_ui(self):
        r = self.root
        r.title(APP_NAME)
        self._set_icon()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(self.px(1000), sw - 40), min(self.px(780), sh - 90)
        r.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        r.minsize(min(self.px(760), w), min(self.px(520), h))

        self.nb = ttk.Notebook(r)
        self.nb.pack(fill="both", expand=True)
        self.tab_session = ttk.Frame(self.nb)
        self.tab_progress = ScrollFrame(self.nb)
        self.tab_history = ttk.Frame(self.nb)
        self.tab_settings = ScrollFrame(self.nb)
        self.nb.add(self.tab_session, text="Session")
        self.nb.add(self.tab_progress, text="Progress")
        self.nb.add(self.tab_history, text="History")
        self.nb.add(self.tab_settings, text="Settings")
        self._build_session_tab(self.tab_session)
        self._build_progress_tab(self.tab_progress.inner)
        self._build_history_tab(self.tab_history)
        self._build_settings_tab(self.tab_settings.inner)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            r.bind_all(seq, lambda e: ScrollFrame.route_wheel(r, e), add="+")

    def _on_tab_changed(self, _e=None):
        current = self.nb.select()
        if current != str(self.tab_settings):
            self.close_preview()
        if current in (str(self.tab_progress), str(self.tab_history)):
            self.reload_sessions()

    # Session tab ---------------------------------------------------------------
    def _build_session_tab(self, parent):
        p = self.px
        outer = ttk.Frame(parent, padding=(p(32), p(26), p(32), p(20)))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Reading session", style="H1.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Start the timer and read. Each time you notice your attention has drifted, "
                              "log it and go back to the text.", style="Sub.TLabel").pack(anchor="w", pady=(p(2), p(20)))

        # idle: choose a length and start
        self.idle_box = ttk.Frame(outer)
        row = ttk.Frame(self.idle_box)
        row.pack(anchor="w")
        ttk.Label(row, text="Length", style="H3.TLabel").pack(side="left", padx=(0, p(14)))
        self.duration_var = tk.StringVar(value=str(self.settings["duration_minutes"]))
        for m in self.PRESETS:
            ttk.Radiobutton(row, text=f"{m} min", value=str(m), variable=self.duration_var,
                            style="Seg.Toolbutton").pack(side="left", padx=(0, p(4)))
        ttk.Label(row, text="or", style="Sub.TLabel").pack(side="left", padx=(p(10), p(6)))
        ttk.Spinbox(row, from_=1, to=600, width=5, textvariable=self.duration_var).pack(side="left")
        ttk.Label(row, text="min", style="Sub.TLabel").pack(side="left", padx=(p(6), 0))
        ttk.Button(self.idle_box, text="Start session", style="Accent.TButton",
                   command=self.start_session).pack(anchor="w", pady=(p(22), p(10)))
        self.hint_label = ttk.Label(self.idle_box, style="Muted.TLabel", justify="left")
        self.hint_label.pack(anchor="w")
        self.idle_box.pack(fill="x", anchor="w")

        # running
        self.run_box = ttk.Frame(outer)
        self.run_title = ttk.Label(self.run_box, text="Session in progress", style="H2.TLabel")
        self.run_title.pack(anchor="w")
        self.run_status = ttk.Label(self.run_box, style="Sub.TLabel")
        self.run_status.pack(anchor="w", pady=(p(4), p(14)))
        btns = ttk.Frame(self.run_box)
        btns.pack(anchor="w")
        self.pause_btn = ttk.Button(btns, text="Pause", command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=(0, p(8)))
        ttk.Button(btns, text="End session", command=self.end_session_early).pack(side="left")

        # results of the latest session
        self.result_card = ttk.Frame(outer, style="Card.TFrame", padding=(p(22), p(18)))
        head = ttk.Frame(self.result_card, style="CardInner.TFrame")
        head.pack(fill="x")
        self.res_title = ttk.Label(head, text="Last session", style="CardH2.TLabel")
        self.res_title.pack(side="left")
        self.res_date = ttk.Label(head, style="CardMuted.TLabel")
        self.res_date.pack(side="left", padx=(p(10), 0), pady=(p(3), 0))
        stats = ttk.Frame(self.result_card, style="CardInner.TFrame")
        stats.pack(fill="x", pady=(p(14), p(6)))
        self.res_stats = []
        for i in range(4):
            cell = ttk.Frame(stats, style="CardInner.TFrame")
            cell.grid(row=0, column=i, sticky="w", padx=(0, p(24)))
            stats.columnconfigure(i, weight=1, uniform="stat")
            val = ttk.Label(cell, style="CardBig.TLabel")
            val.pack(anchor="w")
            cap = ttk.Label(cell, style="CardSub.TLabel")
            cap.pack(anchor="w")
            self.res_stats.append((val, cap))
        self.timeline = TimelineStrip(self.result_card, self)
        self.timeline.pack(fill="x", pady=(p(10), 0))
        self.res_compare = ttk.Label(self.result_card, style="Card.TLabel", justify="left")
        self.res_compare.pack(anchor="w", pady=(p(8), 0))
        self._update_hint()

    def _update_hint(self):
        parts = []
        if self.settings["hotkey_enabled"] and self.hotkeys.available and self.hotkeys.target:
            parts.append(f"During a session, click the dot in the corner of your screen or press "
                         f"{format_combo(*self.hotkeys.target)} to log a distraction.")
        else:
            parts.append("During a session, click the dot in the corner of your screen to log a distraction.")
        parts.append("Drag the dot to move it. Right-click it to undo, pause or end the session.")
        self.hint_label.configure(text="\n".join(parts))

    # Progress tab -----------------------------------------------------------------
    def _build_progress_tab(self, parent):
        p = self.px
        outer = ttk.Frame(parent, padding=(p(32), p(26), p(32), p(28)))
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="Progress", style="H1.TLabel").pack(side="left")
        self.period_var = tk.StringVar(value="Last 30 days")
        combo = self.period_combo = ttk.Combobox(head, textvariable=self.period_var, state="readonly", width=15,
                                                 values=[n for n, _ in self.PERIODS])
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_progress())
        ttk.Label(head, text="Show", style="Sub.TLabel").pack(side="right", padx=(0, p(8)))
        vrow = ttk.Frame(outer)
        vrow.pack(anchor="w", pady=(p(4), p(16)))
        self.verdict = ttk.Label(vrow, style="Sub.TLabel", justify="left")
        self.verdict.pack(side="left")
        self.verdict_delta = ttk.Label(vrow, style="Sub.TLabel")
        self.verdict_delta.pack(side="left", padx=(p(10), 0))

        tiles = ttk.Frame(outer)
        tiles.pack(fill="x")
        self.tiles = []
        for i in range(4):
            card = ttk.Frame(tiles, style="Card.TFrame", padding=(p(16), p(12)))
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else p(10), 0))
            tiles.columnconfigure(i, weight=1, uniform="tile")
            cap = ttk.Label(card, style="CardSub.TLabel")
            cap.pack(anchor="w")
            val = ttk.Label(card, style="CardBig.TLabel")
            val.pack(anchor="w", pady=(p(2), p(2)))
            sub = ttk.Label(card, style="CardMuted.TLabel", justify="left")
            sub.pack(anchor="w")
            self.tiles.append((cap, val, sub))
        tiles.bind("<Configure>", lambda e: [t[2].configure(wraplength=max(80, e.width // 4 - p(40)))
                                             for t in self.tiles])

        self.chart_rate = TrendChart(outer, self)
        self.chart_rate.pack(fill="x", pady=(p(16), 0))
        self.chart_longest = TrendChart(outer, self)
        self.chart_longest.pack(fill="x", pady=(p(12), 0))
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(p(12), 0))
        self.chart_tod = BarChart(row, self)
        self.chart_tod.grid(row=0, column=0, sticky="nsew", padx=(0, p(6)))
        self.chart_dow = BarChart(row, self)
        self.chart_dow.grid(row=0, column=1, sticky="nsew", padx=(p(6), 0))
        row.columnconfigure(0, weight=1, uniform="bars")
        row.columnconfigure(1, weight=1, uniform="bars")
        self.insight = ttk.Label(outer, style="Sub.TLabel", justify="left")
        self.insight.pack(anchor="w", pady=(p(10), 0))

    # History tab -------------------------------------------------------------
    def _build_history_tab(self, parent):
        p = self.px
        outer = ttk.Frame(parent, padding=(p(32), p(26), p(32), p(20)))
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="History", style="H1.TLabel").pack(side="left")
        self.hist_info = ttk.Label(outer, style="Sub.TLabel")
        self.hist_info.pack(anchor="w", pady=(p(2), p(14)))

        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True)
        cols = [("date", "Date", 130, "w"), ("time", "Start", 60, "w"), ("length", "Length", 80, "e"),
                ("count", "Distractions", 95, "e"), ("rate", "Per 10 min", 85, "e"),
                ("longest", "Longest focus", 105, "e"), ("status", "Status", 110, "center"), ("machine", "Computer", 130, "w")]
        self.tree = ttk.Treeview(table, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for key, label, width, anchor in cols:
            self.tree.heading(key, text=label, anchor=anchor)
            self.tree.column(key, width=p(width), anchor=anchor, stretch=key in ("date", "machine"))
        vsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("odd", background=C.stripe)

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(p(12), 0))
        ttk.Button(btns, text="Delete selected…", command=self.delete_selected).pack(side="left")
        ttk.Button(btns, text="Export CSV…", command=self.export_csv).pack(side="left", padx=(p(8), 0))
        ttk.Button(btns, text="Open data folder", command=lambda: open_folder(self.store.sessions_dir)).pack(
            side="left", padx=(p(8), 0))
        ttk.Button(btns, text="Refresh", command=self.reload_sessions).pack(side="right")
        ttk.Label(outer, text="Export CSV… saves two files: one row per session, and one row per event (every press, "
                              "pause, resume and undo). If you select\nsessions in the list, only those are exported.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(p(8), 0))

    # Settings tab ---------------------------------------------------------------
    def _section(self, parent, title, first=False):
        p = self.px
        ttk.Label(parent, text=title, style="H2.TLabel").pack(anchor="w", pady=(0 if first else p(26), p(8)))
        box = ttk.Frame(parent)
        box.pack(fill="x", padx=(p(2), 0))
        return box

    def _radio_list(self, parent, options, var, on_change):
        p = self.px
        for value, label, desc in options:
            row = ttk.Frame(parent)
            row.pack(anchor="w", fill="x", pady=(0, p(4)))
            ttk.Radiobutton(row, text=label, value=value, variable=var, command=on_change).pack(side="left")
            ttk.Label(row, text=f" – {desc}", style="Muted.TLabel").pack(side="left")

    def _build_settings_tab(self, parent):
        p, s = self.px, self.settings
        outer = ttk.Frame(parent, padding=(p(32), p(26), p(32), p(28)))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Settings", style="H1.TLabel").pack(anchor="w", pady=(0, p(16)))

        # feedback
        box = self._section(outer, "When you log a distraction", first=True)
        self.feedback_var = tk.StringVar(value=s["feedback"])
        self._radio_list(box, FEEDBACK_MODES, self.feedback_var,
                         lambda: self._set("feedback", self.feedback_var.get()))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(p(8), 0))
        ttk.Label(row, text="Buzz volume").pack(side="left")
        self.volume_var = tk.DoubleVar(value=s["buzz_volume"])
        ttk.Scale(row, from_=0, to=100, variable=self.volume_var, length=p(180),
                  command=lambda v: self._set("buzz_volume", int(float(v)))).pack(side="left", padx=(p(10), p(14)))
        ttk.Button(row, text="Try it", command=self.test_feedback).pack(side="left")

        # widget
        box = self._section(outer, "Floating widget")
        self.display_var = tk.StringVar(value=s["display"])
        self._radio_list(box, DISPLAY_MODES, self.display_var, lambda: self._set("display", self.display_var.get()))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(p(10), 0))
        ttk.Label(row, text="Size").pack(side="left", padx=(0, p(10)))
        self.size_var = tk.StringVar(value=s["widget_size"])
        for value, label in WIDGET_SIZES:
            ttk.Radiobutton(row, text=label, value=value, variable=self.size_var, style="Seg.Toolbutton",
                            command=lambda: self._set("widget_size", self.size_var.get())).pack(side="left", padx=(0, p(4)))
        ttk.Label(row, text="Opacity").pack(side="left", padx=(p(24), p(10)))
        self.opacity_var = tk.DoubleVar(value=round(s["widget_opacity"] * 100))
        ttk.Scale(row, from_=15, to=100, variable=self.opacity_var, length=p(160),
                  command=lambda v: self._set("widget_opacity", round(float(v)) / 100.0)).pack(side="left")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(p(10), 0))
        ttk.Label(row, text="Colour").pack(side="left", padx=(0, p(10)))
        self.dot_theme_var = tk.StringVar(value=s["dot_theme"])
        for value, label in THEME_CHOICES:
            ttk.Radiobutton(row, text=label, value=value, variable=self.dot_theme_var, style="Seg.Toolbutton",
                            command=lambda: self._set("dot_theme", self.dot_theme_var.get())).pack(side="left", padx=(0, p(4)))
        ttk.Label(row, text="  The dot is least noticeable when it blends with the page you read.",
                  style="Muted.TLabel").pack(side="left", padx=(p(8), 0))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(p(12), 0))
        self.preview_btn = ttk.Button(row, text="Show preview", command=self.toggle_preview)
        self.preview_btn.pack(side="left")
        ttk.Button(row, text="Reset position", command=self.reset_widget_position).pack(side="left", padx=(p(8), 0))
        ttk.Label(box, text="Use the preview to place the widget where you want it before you start reading. "
                            "Clicking the preview tries out your feedback setting.",
                  style="Muted.TLabel").pack(anchor="w", pady=(p(6), 0))

        # appearance
        box = self._section(outer, "Appearance")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Label(row, text="Theme").pack(side="left", padx=(0, p(10)))
        self.appearance_var = tk.StringVar(value=s["appearance"])
        for value, label in THEME_CHOICES:
            ttk.Radiobutton(row, text=label, value=value, variable=self.appearance_var, style="Seg.Toolbutton",
                            command=lambda: self._set("appearance", self.appearance_var.get())).pack(side="left", padx=(0, p(4)))
        ttk.Label(box, text="Match system follows the light or dark setting of Windows or Zorin and switches as soon "
                            "as you change it.", style="Muted.TLabel").pack(anchor="w", pady=(p(6), 0))

        # session
        box = self._section(outer, "Session")
        self.chime_var = tk.BooleanVar(value=s["chime"])
        row = ttk.Frame(box)
        row.pack(anchor="w", fill="x")
        ttk.Checkbutton(row, text="Play a soft chime when the session ends", variable=self.chime_var,
                        command=lambda: self._set("chime", self.chime_var.get())).pack(side="left")
        ttk.Button(row, text="Play", command=self.sounds.chime).pack(side="left", padx=(p(12), 0))
        self.minimize_var = tk.BooleanVar(value=s["minimize_on_start"])
        ttk.Checkbutton(box, text="Minimise this window when a session starts", variable=self.minimize_var,
                        command=lambda: self._set("minimize_on_start", self.minimize_var.get())).pack(
            anchor="w", pady=(p(6), 0))

        # hotkey
        box = self._section(outer, "Keyboard shortcut")
        self.hotkey_enabled_var = tk.BooleanVar(value=s["hotkey_enabled"])
        ttk.Checkbutton(box, text="Log distractions with a keyboard shortcut (works in any app)",
                        variable=self.hotkey_enabled_var, command=self._toggle_hotkey).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(p(8), 0))
        ttk.Label(row, text="Shortcut").pack(side="left", padx=(0, p(10)))
        self.hotkey_var = tk.StringVar(value=s["hotkey"])
        entry = ttk.Entry(row, textvariable=self.hotkey_var, width=18)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.save_hotkey())
        ttk.Button(row, text="Save", command=self.save_hotkey).pack(side="left", padx=(p(8), 0))
        self.record_btn = ttk.Button(row, text="Record…", command=self.record_hotkey)
        self.record_btn.pack(side="left", padx=(p(8), 0))
        self.hotkey_status = ttk.Label(box, style="Muted.TLabel", justify="left")
        self.hotkey_status.pack(anchor="w", pady=(p(6), 0))
        ttk.Label(box, text="Type a shortcut such as Ctrl+Alt+D or F8 and press Save, or press Record… and then "
                            "the keys.\nAlternative: make a system shortcut that runs this command:",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(p(8), p(4)))
        cmd = ttk.Entry(box, width=80)
        cmd.insert(0, log_command())
        cmd.configure(state="readonly")
        cmd.pack(anchor="w", fill="x")

        # data
        box = self._section(outer, "Session history and CSV files")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="Open", command=lambda: open_folder(self.store.sessions_dir)).pack(side="right")
        ttk.Button(row, text="Change…", command=self.change_data_folder).pack(side="right", padx=(p(8), p(8)))
        self.folder_entry = ttk.Entry(row)
        self.folder_entry.pack(side="left", fill="x", expand=True)
        self._show_folder()
        ttk.Label(box, text="To share one history between Windows and Zorin, pick a folder both can see, such as "
                            "a Dropbox, OneDrive or Syncthing\nfolder, on each computer. Each session is its own small "
                            "file, so syncing never causes conflicts.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(p(6), 0))
        row = ttk.Frame(box)
        row.pack(anchor="w", fill="x", pady=(p(12), 0))
        self.auto_csv_var = tk.BooleanVar(value=s["auto_csv"])
        ttk.Checkbutton(row, text="Keep up-to-date CSV files of every session in this folder",
                        variable=self.auto_csv_var, command=self._toggle_auto_csv).pack(side="left")
        ttk.Button(row, text="Open CSV folder", command=lambda: open_folder(self.csv_folder())).pack(
            side="left", padx=(p(12), 0))
        self.csv_status = ttk.Label(box, style="Muted.TLabel", justify="left")
        self.csv_status.pack(anchor="w", pady=(p(6), 0))

        # updates
        box = self._section(outer, "Updates")
        self.check_updates_var = tk.BooleanVar(value=s["check_updates"])
        row = ttk.Frame(box)
        row.pack(anchor="w", fill="x")
        ttk.Checkbutton(row, text="Check for updates when the app starts", variable=self.check_updates_var,
                        command=lambda: self._set("check_updates", self.check_updates_var.get())).pack(side="left")
        ttk.Button(row, text="Check now", command=lambda: self.check_updates(manual=True)).pack(
            side="left", padx=(p(12), 0))
        self.update_status = ttk.Label(box, style="Muted.TLabel", justify="left")
        self.update_status.pack(anchor="w", pady=(p(6), 0))
        how = "runs 'git pull' (this copy is a Git clone)" if is_git_checkout() else "replaces the app's files"
        ttk.Label(box, text=f"You have version {APP_VERSION}. Updates come from github.com/{UPDATE_REPO} "
                            f"({UPDATE_BRANCH} branch). Installing one {how},\nafter backing up the current "
                            "version. Your sessions and settings aren't touched.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(p(4), 0))
        ttk.Label(outer, text=f"{APP_NAME} {APP_VERSION}\nApp folder: {APP_DIR}\nSettings for this computer: {self.cfg_dir}",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(p(30), 0))

    def _show_folder(self):
        self.folder_entry.configure(state="normal")
        self.folder_entry.delete(0, "end")
        self.folder_entry.insert(0, self.data_folder())
        self.folder_entry.configure(state="readonly")

    def _set(self, key, value):
        self.settings[key] = value
        if key in ("appearance", "dot_theme"):
            self.apply_theme()
            return
        for w in (self.widget, self.preview):
            if w is None:
                continue
            if key == "widget_opacity":
                w.set_opacity(value)
            elif key == "widget_size":
                w.reconfigure()
            elif key == "display":
                w.render()

    # -- sessions ---------------------------------------------------------------
    def start_session(self):
        if self.session is not None:
            return
        try:
            minutes = float(self.duration_var.get())
            if not 1 <= minutes <= 600:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "Choose a session length between 1 and 600 minutes.", parent=self.root)
            return
        self.settings["duration_minutes"] = int(round(minutes))
        self.duration_var.set(str(int(round(minutes))))
        self.close_preview()
        self.session = ActiveSession(int(round(minutes)) * 60)
        self.widget = FocusWidget(self)
        self._save_in_progress()
        self.idle_box.pack_forget()
        self.result_card.pack_forget()
        self.run_box.pack(fill="x", anchor="w")
        self._update_running_ui()
        if self.settings["minimize_on_start"]:
            self.root.iconify()
        self._tick()

    def _tick(self):
        s = self.session
        if s is None:
            return
        if s.check_suspend():
            self._save_in_progress()
        if not s.paused and s.remaining() <= 0:
            self.finish_session(completed=True, reason="completed")
            return
        if self.widget:
            self.widget.render()
        self._update_running_ui()
        if time.monotonic() - self.last_autosave > 15:
            self._save_in_progress()
        self.tick_job = self.root.after(500, self._tick)

    def _update_running_ui(self):
        s = self.session
        if s is None:
            return
        started = s.start.strftime("%H:%M")
        if s.paused:
            why = " (your computer went to sleep)" if s.auto_paused else ""
            text = f"Paused{why} · {fmt_minutes(s.remaining())} left · started {started}"
        else:
            text = f"{fmt_minutes(s.remaining())} left · started {started}"
        if self.settings["display"] == "time_count":
            text += f" · {plural(len(s.distractions), 'distraction')} logged"
        self.run_title.configure(text="Session paused" if s.paused else "Session in progress")
        self.run_status.configure(text=text)
        self.pause_btn.configure(text="Resume" if s.paused else "Pause")

    def log_distraction(self, source="click"):
        s = self.session
        if s is None or s.paused:
            return
        if s.log(SOURCE_NAMES.get(source, source)):
            if self.widget:
                self.widget.feedback(self.settings["feedback"])
            self._save_in_progress()
            self._update_running_ui()

    def undo_distraction(self):
        if self.session and self.session.undo():
            if self.widget:
                self.widget.render()
            self._save_in_progress()
            self._update_running_ui()

    def toggle_pause(self):
        s = self.session
        if s is None:
            return
        s.resume() if s.paused else s.pause()
        if self.widget:
            self.widget.render()
        self._save_in_progress()
        self._update_running_ui()

    def end_session_early(self):
        if self.session is None:
            return
        self.show_main()
        if messagebox.askyesno("End session", "End this session now?\n\nIt will be saved as ended early.",
                               parent=self.root):
            self.finish_session(completed=False, reason="ended_early")

    def finish_session(self, completed, reason, confirm_short=True):
        s = self.session
        if s is None:
            return
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
            self.tick_job = None
        self.session = None
        if self.widget:
            self.widget.destroy()
            self.widget = None
        rec = s.to_record(completed, reason, {"feedback": self.settings["feedback"],
                                              "display": self.settings["display"]})
        self._clear_in_progress()
        self.run_box.pack_forget()
        self.idle_box.pack(fill="x", anchor="w")
        if self.last_result is not None:
            self.result_card.pack(fill="x", pady=(self.px(26), 0))

        keep = True
        if not completed and rec["active_seconds"] < SHORT_SESSION_SECONDS:
            keep = (not confirm_short) and rec["active_seconds"] >= 30
            if confirm_short:
                self.show_main()
                keep = messagebox.askyesno(
                    "Short session", f"This session lasted {fmt_duration(rec['active_seconds'])}. "
                                     "Very short sessions can skew your averages.\n\nSave it anyway?",
                    parent=self.root)
        if not keep:
            return
        self._save_record(rec)
        if completed and self.settings["chime"]:
            self.sounds.chime()
        self.reload_sessions()
        saved = next((r for r in self.sessions if r["id"] == rec["id"]), None)
        if saved is None:  # saving failed; still show the result
            saved = dict(rec, _start=parse_iso(rec["start"]))
            saved["_m"] = session_metrics(saved)
        self.show_results(saved, fresh=True)
        self.nb.select(self.tab_session)
        self.show_main()
        self.auto_export()
        if self.pending_update is not None:
            self.root.after(1500, lambda: self.show_update_dialog(self.pending_update))

    def _save_record(self, rec):
        try:
            self.store.save(rec)
            return True
        except OSError as exc:
            backup = SessionStore(os.path.join(self.cfg_dir, "unsaved"))
            try:
                backup.save(rec)
                where = f"A copy was kept in {backup.sessions_dir} and will be moved there next time."
            except OSError:
                where = "A backup copy couldn't be written either."
            messagebox.showwarning(APP_NAME, f"Couldn't save the session to your history folder:\n{exc}\n\n{where}",
                                   parent=self.root)
            return False

    def _flush_unsaved(self):
        backup = SessionStore(os.path.join(self.cfg_dir, "unsaved"))
        for path in backup.files():
            try:
                os.makedirs(self.store.sessions_dir, exist_ok=True)
                shutil.move(path, os.path.join(self.store.sessions_dir, os.path.basename(path)))
            except OSError:
                pass

    # in-progress snapshot, so a crash or power cut doesn't lose a session
    def _in_progress_path(self):
        return os.path.join(self.cfg_dir, "in_progress.json")

    def _save_in_progress(self):
        if self.session is None:
            return
        self.last_autosave = time.monotonic()
        try:
            write_json_atomic(self._in_progress_path(), self.session.to_record(False, "unfinished"))
        except OSError:
            pass

    def _clear_in_progress(self):
        try:
            os.remove(self._in_progress_path())
        except OSError:
            pass

    def _check_unfinished(self):
        path = self._in_progress_path()
        if not os.path.exists(path) or self.session is not None:
            return
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            start = parse_iso(rec["start"])
            active = float(rec["active_seconds"])
        except (OSError, ValueError, KeyError, TypeError):
            self._clear_in_progress()
            return
        if active >= 60 and messagebox.askyesno(
                "Unfinished session",
                f"A session from {long_date(start)} at {start.strftime('%H:%M')} didn't finish properly "
                f"({fmt_minutes(active)}, {plural(len(rec.get('distractions', [])), 'distraction')} logged).\n\n"
                "Save it to your history as ended early?", parent=self.root):
            rec["completed"] = False
            rec["end_reason"] = "recovered"
            for ev in rec.get("events", [])[-1:]:
                if ev.get("type") == "end":
                    ev["reason"] = "recovered"
            self._save_record(rec)
            self.reload_sessions()
            self.auto_export()
        self._clear_in_progress()

    # -- results / progress / history -----------------------------------------------
    def reload_sessions(self):
        self.store = SessionStore(self.data_folder())
        self.sessions, bad = self.store.load_all()
        self.refresh_progress()
        self.refresh_history(bad)

    def show_results(self, rec, fresh):
        self.last_result = rec
        self.last_result_fresh = fresh
        m = rec["_m"]
        start = rec["_start"]
        self.res_title.configure(text="Session complete" if fresh and rec.get("completed") else
                                 ("Session ended early" if fresh else "Last session"))
        self.res_date.configure(text=f"{long_date(start)} · {start.strftime('%H:%M')}")
        planned = rec.get("planned_seconds", m["seconds"])
        length_cap = "minutes read" if rec.get("completed") else f"minutes read of {round(planned / 60)} planned"
        values = [
            (f"{m['seconds'] / 60:.0f}" if m["seconds"] >= 60 else f"{m['seconds'] / 60:.1f}", length_cap),
            (str(m["count"]), "distractions"),
            (f"{m['rate']:.1f}", "per 10 minutes"),
            (fmt_duration(m["longest"]), "longest focused stretch"),
        ]
        for (val, cap), (vl, cl) in zip(values, self.res_stats):
            vl.configure(text=val)
            cl.configure(text=cap)
        self.timeline.set_record(rec)
        earlier = [r for r in self.sessions if r["_start"] < rec["_start"] and r["id"] != rec["id"]][-ROLLING_WINDOW:]
        avg = weighted_rate(earlier)
        if avg is None:
            self.res_compare.configure(text="This is your first session, so it's your baseline to improve on.",
                                       foreground=C.ink2)
        else:
            text, color = change_text(m["rate"], avg, f"your previous {plural(len(earlier), 'session')} "
                                                      f"({avg:.1f} per 10 min)")
            self.res_compare.configure(text=text, foreground=color)
        if self.session is None:
            self.result_card.pack(fill="x", pady=(self.px(26), 0))

    def refresh_progress(self):
        label = self.period_var.get()
        days = dict(self.PERIODS).get(label)
        now = now_local()
        allrecs = self.sessions
        if days:
            cutoff = now - timedelta(days=days)
            cur = [r for r in allrecs if r["_start"] >= cutoff]
            prev = [r for r in allrecs if cutoff - timedelta(days=days) <= r["_start"] < cutoff]
            prev_label = f"previous {days} days" if days != 365 else "previous 12 months"
        else:
            cur, prev, prev_label = list(allrecs), [], ""

        # headline: last 7 sessions vs the 7 before, regardless of the filter
        n = len(allrecs)
        self.verdict_delta.configure(text="")
        if n == 0:
            self.verdict.configure(text="No sessions yet. Finish your first session to start tracking.")
        elif n < 4:
            self.verdict.configure(text=f"{plural(n, 'session')} so far. After four sessions you'll see whether "
                                        "you're improving.")
        else:
            k = min(ROLLING_WINDOW, n // 2)
            recent, before = allrecs[-k:], allrecs[-2 * k:-k]
            a, b = weighted_rate(recent), weighted_rate(before)
            text, color = change_text(a, b, f"the {k} before")
            self.verdict.configure(text=f"Your last {k} sessions: {a:.1f} distractions per 10 min.")
            self.verdict_delta.configure(text=text, foreground=color)

        # tiles
        rate_cur, rate_prev = weighted_rate(cur), weighted_rate(prev)
        secs = sum(r["_m"]["seconds"] for r in cur)
        mean_long = (sum(r["_m"]["longest"] for r in cur) / len(cur)) if cur else None
        mean_long_prev = (sum(r["_m"]["longest"] for r in prev) / len(prev)) if prev else None
        best = max(cur, key=lambda r: r["_m"]["longest"]) if cur else None

        def delta(new, old, lower_is_better):
            if not days:
                return "All sessions", C.muted
            if new is None:
                return "", C.muted
            if old is None:
                return f"No sessions in {prev_label}", C.muted
            return change_text(new, old, prev_label, lower_is_better)

        tiles = [
            ("Sessions", str(len(cur)), (f"{fmt_minutes(secs)} of reading" if cur else "None in this period", C.muted)),
            ("Distractions per 10 min", f"{rate_cur:.1f}" if rate_cur is not None else "–",
             delta(rate_cur, rate_prev, True)),
            ("Average longest focus", fmt_duration(mean_long) if mean_long is not None else "–",
             delta(mean_long, mean_long_prev, False)),
            ("Best focused stretch", fmt_duration(best["_m"]["longest"]) if best else "–",
             (f"{long_date(best['_start'])}" if best else "", C.muted)),
        ]
        for (cap, val, (sub, color)), (cl, vl, sl) in zip(tiles, self.tiles):
            cl.configure(text=cap)
            vl.configure(text=val)
            sl.configure(text=sub, foreground=color)

        # trend charts (moving averages use the full history so the line starts correctly)
        ids = {r["id"] for r in cur}
        rate_pts, long_pts = [], []
        for i, r in enumerate(allrecs):
            if r["id"] not in ids:
                continue
            window = allrecs[max(0, i - ROLLING_WINDOW + 1):i + 1]
            avg_rate = weighted_rate(window) or 0.0
            avg_long = sum(w["_m"]["longest"] for w in window) / len(window) / 60
            m = r["_m"]
            when = f"{long_date(r['_start'])}, {r['_start'].strftime('%H:%M')}"
            rate_pts.append({"date": r["_start"], "value": m["rate"], "avg": avg_rate, "tip": [
                when, f"{m['rate']:.1f} per 10 min  ({plural(m['count'], 'distraction')} in {fmt_minutes(m['seconds'])})",
                f"{len(window)}-session average: {avg_rate:.1f}"]})
            long_pts.append({"date": r["_start"], "value": m["longest"] / 60, "avg": avg_long, "tip": [
                when, f"Longest focus: {fmt_duration(m['longest'])}",
                f"{len(window)}-session average: {fmt_duration(avg_long * 60)}"]})
        empty = "No sessions in this period yet."
        self.chart_rate.set_data(title="Distractions per 10 minutes", subtitle="Lower is better",
                                 points=rate_pts, fmt=lambda v: f"{v:.1f}", empty=empty)
        self.chart_longest.set_data(title="Longest focused stretch", subtitle="Minutes between distractions · higher is better",
                                    points=long_pts, fmt=lambda v: fmt_duration(v * 60), empty=empty,
                                    tick_fmt=lambda v: f"{v:g}m")

        # time-of-day and weekday patterns
        def cats_for(groups, labels, subs, long_names):
            out = []
            rates = [weighted_rate(g) if g else None for g in groups]
            eligible = [(v, i) for i, (v, g) in enumerate(zip(rates, groups)) if v is not None and len(g) >= 2]
            best_i = min(eligible)[1] if len(eligible) >= 2 else None
            for i, g in enumerate(groups):
                v = rates[i]
                tip = [long_names[i]]
                if v is None:
                    tip.append("No sessions")
                else:
                    tip += [f"{v:.1f} distractions per 10 min",
                            f"{plural(len(g), 'session')} · {fmt_minutes(sum(r['_m']['seconds'] for r in g))}"]
                out.append({"label": labels[i], "sub": subs[i] if subs else "", "value": v,
                            "highlight": i == best_i, "tip": tip})
            return out, best_i, rates

        tod_groups = [[] for _ in TIME_BUCKETS]
        dow_groups = [[] for _ in WEEKDAYS]
        for r in cur:
            tod_groups[time_bucket(r["_start"].hour)].append(r)
            dow_groups[r["_start"].weekday()].append(r)
        tod_names = [f"{b[0]} ({b[1].replace('–', ':00–')}:00)" for b in TIME_BUCKETS]
        tod_cats, tod_best, tod_rates = cats_for(tod_groups, [b[0] for b in TIME_BUCKETS],
                                                 [b[1] for b in TIME_BUCKETS], tod_names)
        dow_cats, dow_best, dow_rates = cats_for(dow_groups, WEEKDAYS, None, DAY_NAMES)
        self.chart_tod.set_data(title="By time of day", subtitle="Distractions per 10 min, by start time · lower is better",
                                cats=tod_cats, fmt=lambda v: f"{v:.1f}", empty=empty)
        self.chart_dow.set_data(title="By day of week", subtitle="Distractions per 10 min · lower is better",
                                cats=dow_cats, fmt=lambda v: f"{v:.1f}", empty=empty)
        notes = []
        if tod_best is not None:
            notes.append(f"You focus best {TIME_BUCKETS[tod_best][4]} ({tod_rates[tod_best]:.1f} per 10 min)")
        if dow_best is not None:
            notes.append(f"your best day is {DAY_NAMES[dow_best]} ({dow_rates[dow_best]:.1f})")
        if notes:
            text = "; ".join(notes) + ". The highlighted bar marks the best one."
            self.insight.configure(text=text[0].upper() + text[1:])
        else:
            self.insight.configure(text="Patterns appear once you have at least two sessions in two or more "
                                        "time slots or days." if cur else "")

    def refresh_history(self, bad=0):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(reversed(self.sessions)):
            m = r["_m"]
            status = "Completed" if r.get("completed") else "Ended early"
            self.tree.insert("", "end", iid=r["id"], tags=("odd",) if i % 2 else (), values=(
                long_date(r["_start"]), r["_start"].strftime("%H:%M"), fmt_minutes(m["seconds"]), m["count"],
                f"{m['rate']:.1f}", fmt_duration(m["longest"]), status, r.get("machine", "")))
        info = f"{plural(len(self.sessions), 'session')} in {self.store.sessions_dir}"
        if bad:
            info += f"  ·  {plural(bad, 'file')} couldn't be read"
        self.hist_info.configure(text=info)

    def delete_selected(self):
        ids = set(self.tree.selection())
        if not ids:
            messagebox.showinfo(APP_NAME, "Select one or more sessions in the list first.", parent=self.root)
            return
        if not messagebox.askyesno("Delete sessions", f"Permanently delete {plural(len(ids), 'session')}?",
                                   parent=self.root):
            return
        for r in self.sessions:
            if r["id"] in ids:
                self.store.delete(r)
        self.reload_sessions()
        self.auto_export()
        if self.last_result is not None and self.last_result["id"] in ids:
            self.last_result = None
            self.result_card.pack_forget()
            if self.sessions:
                self.show_results(self.sessions[-1], fresh=False)

    def export_csv(self):
        ids = set(self.tree.selection())
        recs = [r for r in self.sessions if r["id"] in ids] if ids else list(self.sessions)
        if not recs:
            messagebox.showinfo(APP_NAME, "There are no sessions to export yet.", parent=self.root)
            return
        folder = filedialog.askdirectory(parent=self.root, title="Choose a folder for the CSV files",
                                         initialdir=os.path.expanduser("~"))
        if not folder:
            return
        prefix = "focus-trainer-"
        targets = [os.path.join(folder, f"{prefix}{n}.csv") for n in ("sessions", "events")]
        existing = [os.path.basename(t) for t in targets if os.path.exists(t)]
        if existing and not messagebox.askyesno(
                APP_NAME, f"{' and '.join(existing)} already exist{'s' if len(existing) == 1 else ''} in that "
                          "folder.\n\nReplace them?", parent=self.root):
            return
        try:
            paths = export_csvs(recs, folder, prefix)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Couldn't write the CSV files:\n{exc}\n\n"
                                           "If one is open in Excel, close it and try again.", parent=self.root)
            return
        which = f"the {plural(len(recs), 'selected session')}" if ids else f"all {plural(len(recs), 'session')}"
        messagebox.showinfo(APP_NAME, f"Exported {which} to:\n\n" + "\n".join(paths), parent=self.root)

    def csv_folder(self):
        return os.path.join(self.data_folder(), "csv")

    def auto_export(self):
        """Keep csv/sessions.csv and csv/events.csv in the history folder up to date."""
        if not self.settings["auto_csv"]:
            note = "CSV files aren't being kept up to date. Use Export CSV… on the History tab when you need them."
        elif not self.sessions:
            note = f"CSV files will appear in {self.csv_folder()} after your first session."
        else:
            try:
                export_csvs(self.sessions, self.csv_folder(), only_if_changed=True)
                note = f"sessions.csv and events.csv in {self.csv_folder()} include all {len(self.sessions)} sessions."
            except OSError as exc:
                note = (f"Couldn't update the CSV files ({exc.strerror or exc}). If one is open in Excel, "
                        "they'll be updated after your next session.")
        self.csv_note = note
        self.csv_status.configure(text=note)

    def _toggle_auto_csv(self):
        self.settings["auto_csv"] = bool(self.auto_csv_var.get())
        self.auto_export()

    # -- settings actions ----------------------------------------------------------
    def test_feedback(self):
        w = self.widget or self.preview
        if w is None:
            self.toggle_preview()
            w = self.preview
        w.feedback(self.settings["feedback"])

    def toggle_preview(self):
        if self.preview is not None:
            self.close_preview()
        elif self.session is None:
            self.preview = FocusWidget(self, preview=True)
            self.preview_btn.configure(text="Hide preview")

    def close_preview(self):
        if self.preview is not None:
            self.preview.destroy()
            self.preview = None
        if hasattr(self, "preview_btn"):
            self.preview_btn.configure(text="Show preview")

    def reset_widget_position(self):
        self.settings["widget_x"] = self.settings["widget_y"] = None
        for w in (self.widget, self.preview):
            if w is not None:
                w.x = None
                w.text = None
                w.render()

    def _toggle_hotkey(self):
        self.settings["hotkey_enabled"] = bool(self.hotkey_enabled_var.get())
        self.apply_hotkey()

    def save_hotkey(self):
        try:
            mods, key = parse_combo(self.hotkey_var.get())
        except ValueError as exc:
            self._hotkey_msg(str(exc), "bad")
            return
        combo = format_combo(mods, key)
        self.hotkey_var.set(combo)
        self.settings["hotkey"] = combo
        if not self.hotkey_enabled_var.get():
            self.hotkey_enabled_var.set(True)
            self.settings["hotkey_enabled"] = True
        self.apply_hotkey()

    def record_hotkey(self):
        if not self.hotkeys.available:
            self.apply_hotkey()
            return
        self.hotkeys.record()
        self.record_btn.configure(text="Press keys…", state="disabled")
        self._hotkey_msg("Press the new shortcut now (Esc cancels).", "ink2")

    def _hotkey_msg(self, text, role):
        self.hotkey_status_role = (text, role)
        self.hotkey_status.configure(text=text, foreground=getattr(C, role))

    def apply_hotkey(self):
        self.hotkeys.target = None
        if not self.settings["hotkey_enabled"]:
            self.hotkeys.stop()
            self._hotkey_msg("Keyboard shortcut is off.", "muted")
        elif not self.hotkeys.available:
            install = "py -m pip install pynput" if IS_WINDOWS else "sudo apt install python3-pynput"
            self._hotkey_msg(f"The shortcut needs the pynput package. Install it with:   {install}\n"
                             f"then restart {APP_NAME}.  ({self.hotkeys.error})", "bad")
        else:
            try:
                mods, key = parse_combo(self.settings["hotkey"])
                self.hotkeys.start(mods, key)
            except ValueError as exc:
                self.hotkeys.stop()
                self._hotkey_msg(f"{exc}", "bad")
            except Exception as exc:
                self.hotkeys.stop()
                self._hotkey_msg(f"Couldn't start the keyboard listener: {exc}", "bad")
            else:
                msg = f"On: press {format_combo(mods, key)} in any app to log a distraction."
                color = "good"
                if not IS_WINDOWS and is_wayland():
                    msg += ("\nYou're in a Wayland session, where apps can't see shortcuts typed into other apps. "
                            "Use the system-shortcut command below,\nor choose the X11 session "
                            "(the gear icon on the login screen).")
                    color = "ink2"
                self._hotkey_msg(msg, color)
        self._update_hint()

    def change_data_folder(self):
        new = filedialog.askdirectory(parent=self.root, title="Choose where to keep your session history",
                                      initialdir=self.data_folder())
        if not new:
            return
        new = os.path.normpath(new)
        if os.path.normcase(new) == os.path.normcase(os.path.normpath(self.data_folder())):
            return
        old, target = self.store, SessionStore(new)
        try:
            os.makedirs(target.sessions_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Can't use that folder:\n{exc}", parent=self.root)
            return
        old_files = old.files()
        if old_files and messagebox.askyesno(
                "Copy your sessions?",
                f"Copy your {plural(len(old_files), 'session')} into the new folder?\n\n"
                "Sessions already in the new folder are kept, so histories from both computers are combined.",
                parent=self.root):
            for path in old_files:
                dest = os.path.join(target.sessions_dir, os.path.basename(path))
                if not os.path.exists(dest):
                    shutil.copy2(path, dest)
        self.settings["data_folder"] = new
        self._show_folder()
        self.reload_sessions()
        self.auto_export()
        if self.sessions and self.session is None:
            self.show_results(self.sessions[-1], fresh=False)

    # -- updates ---------------------------------------------------------------------
    def _update_msg(self, text, role="muted"):
        self.update_status_role = (text, role)
        self.update_status.configure(text=text, foreground=getattr(C, role))

    def check_updates(self, manual=False):
        if self.update_checking:
            return
        self.update_checking = True
        if manual:
            self._update_msg("Checking for updates…", "ink2")

        def work():
            try:
                self.q.put(("update_checked", (manual, fetch_manifest(), None)))
            except Exception as exc:  # network errors, bad data
                self.q.put(("update_checked", (manual, None, str(exc))))
        threading.Thread(target=work, daemon=True).start()

    def _on_update_checked(self, manual, man, error):
        self.update_checking = False
        if error:
            self._update_msg(f"Couldn't check for updates. {error}", "bad" if manual else "muted")
            return
        if version_tuple(man["version"]) <= version_tuple(APP_VERSION):
            self._update_msg(f"You have the latest version ({APP_VERSION}).", "good" if manual else "muted")
            return
        self._update_msg(f"Version {man['version']} is available.", "ink2")
        if not manual and man["version"] == self.settings["skipped_version"]:
            return
        self.pending_update = man
        if self.session is None:
            self.show_update_dialog(man)

    def show_update_dialog(self, man):
        if self.session is not None or getattr(self, "update_dialog", None) is not None:
            return
        p = self.px
        self.show_main()
        dlg = self.update_dialog = tk.Toplevel(self.root, bg=C.page)
        dlg.title("Update available")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        body = ttk.Frame(dlg, padding=(p(24), p(20), p(24), p(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"{APP_NAME} {man['version']} is available", style="H2.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"You have version {APP_VERSION}.", style="Sub.TLabel").pack(anchor="w", pady=(p(2), p(12)))
        if man["notes"]:
            ttk.Label(body, text="What's new", style="H3.TLabel").pack(anchor="w")
            for note in man["notes"][:12]:
                ttk.Label(body, text=f"•  {note}", wraplength=p(460), justify="left").pack(anchor="w", pady=(p(2), 0))
        how = ("This copy is a Git clone, so the update runs 'git pull'." if is_git_checkout() else
               "Your current version is backed up first. Your sessions and settings aren't touched.")
        ttk.Label(body, text=how, style="Muted.TLabel", wraplength=p(460), justify="left").pack(anchor="w", pady=(p(14), 0))
        status = ttk.Label(body, style="Sub.TLabel", wraplength=p(460), justify="left")
        status.pack(anchor="w", pady=(p(8), 0))
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(p(14), 0))
        install = ttk.Button(btns, text="Install and restart", style="Accent.TButton")
        later = ttk.Button(btns, text="Later")
        skip = ttk.Button(btns, text="Skip this version")
        install.pack(side="left")
        later.pack(side="left", padx=(p(8), 0))
        skip.pack(side="right")

        def close():
            self.update_dialog = None
            dlg.destroy()

        def do_skip():
            self.settings["skipped_version"] = man["version"]
            self.pending_update = None
            close()

        def do_later():
            self.pending_update = None
            close()

        def do_install():
            for b in (install, later, skip):
                b.configure(state="disabled")
            dlg.protocol("WM_DELETE_WINDOW", lambda: None)
            status.configure(text="Downloading and installing…", foreground=C.ink2)

            def work():
                try:
                    self.q.put(("update_installed", (man, install_update(man), None)))
                except Exception as exc:
                    self.q.put(("update_installed", (man, None, str(exc))))
            threading.Thread(target=work, daemon=True).start()

        def failed(error):
            status.configure(text=f"The update wasn't installed. {error}", foreground=C.bad)
            for b in (install, later, skip):
                b.configure(state="normal")
            dlg.protocol("WM_DELETE_WINDOW", do_later)

        install.configure(command=do_install)
        later.configure(command=do_later)
        skip.configure(command=do_skip)
        dlg.protocol("WM_DELETE_WINDOW", do_later)
        self.update_failed = failed
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dlg.winfo_reqheight()) // 3)
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.grab_set()

    def _on_update_installed(self, man, backup, error):
        if error:
            if getattr(self, "update_dialog", None) is not None:
                self.update_failed(error)
            return
        self.settings["updated_from"] = APP_VERSION
        self.settings["skipped_version"] = ""
        self.restart_app()

    def restart_app(self):
        """Start the (updated) app in a new process and close this one."""
        self.close_preview()
        self.hotkeys.stop()
        if self.ipc is not None:
            self.ipc.close()  # free the port so the new copy can take over
        kwargs = {"cwd": APP_DIR, "close_fds": True}
        if IS_WINDOWS:
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, os.path.join(APP_DIR, "focus_trainer.py")], **kwargs)
        self.root.destroy()

    def _after_update_notice(self):
        old = self.settings["updated_from"]
        if old:
            self.settings["updated_from"] = ""
            self._update_msg(f"Updated from {old} to {APP_VERSION}.", "good")
            messagebox.showinfo(APP_NAME, f"{APP_NAME} was updated from version {old} to {APP_VERSION}.",
                                parent=self.root)

    # -- window / events --------------------------------------------------------------
    def show_main(self):
        r = self.root
        r.deiconify()
        r.lift()
        try:
            r.attributes("-topmost", True)
            r.after(300, lambda: r.attributes("-topmost", False))
        except tk.TclError:
            pass
        r.focus_force()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "distraction":
                    self.log_distraction(payload)
                elif kind == "show":
                    self.show_main()
                elif kind == "update_checked":
                    self._on_update_checked(*payload)
                elif kind == "update_installed":
                    self._on_update_installed(*payload)
                elif kind == "hotkey_recorded":
                    self.record_btn.configure(text="Record…", state="normal")
                    if not self.settings["hotkey_enabled"]:
                        self.hotkeys.stop()
                    if payload is None:
                        self.apply_hotkey()
                    else:
                        self.hotkey_var.set(payload)
                        self.save_hotkey()
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def on_close(self):
        if self.session is not None:
            self.show_main()
            ans = messagebox.askyesnocancel(
                APP_NAME, "A session is still running.\n\nSave it as ended early before quitting?\n"
                          "(Choose No to quit without saving it.)", parent=self.root)
            if ans is None:
                return
            if ans:
                self.finish_session(completed=False, reason="quit", confirm_short=False)
            else:
                self.session = None
                self._clear_in_progress()
        self.close_preview()
        self.hotkeys.stop()
        if self.ipc is not None:
            self.ipc.close()
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def enable_dpi_awareness():
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def main(argv):
    if "--log" in argv:
        if ipc_send("distraction"):
            return 0
        sys.stderr.write(f"{APP_NAME} isn't running.\n")
        return 1
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    if ipc_send("show", timeout=0.5):
        print(f"{APP_NAME} is already running; brought it to the front.")
        return 0
    try:
        ipc = IPCServer(queue.Queue())
        ipc.start()
    except OSError:
        ipc = None  # port taken by something else; the app still works without --log
    enable_dpi_awareness()
    root = tk.Tk(className="FocusTrainer")
    App(root, ipc)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
