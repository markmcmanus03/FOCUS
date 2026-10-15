#!/usr/bin/env bash
# Sets up Focus Trainer on Zorin OS (or Ubuntu) and adds it to your app menu.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

has_module() { python3 -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$1') else 1)"; }

if ! has_module tkinter; then
  echo "Installing Tk for Python (you'll be asked for your password)..."
  sudo apt install -y python3-tk
fi
if ! has_module pynput; then
  echo "Installing pynput for the keyboard shortcut..."
  sudo apt install -y python3-pynput || echo "Couldn't install pynput. The app works without it, but the keyboard shortcut won't."
fi

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/focus-trainer.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Focus Trainer
Comment=Log distractions while you read and track your concentration
Exec=python3 "$DIR/focus_trainer.py"
Icon=$DIR/icon.png
Terminal=false
Categories=Education;Utility;
StartupWMClass=FocusTrainer
DESKTOP

echo
echo "Done. Open Focus Trainer from your app menu, or run:  python3 \"$DIR/focus_trainer.py\""
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  echo
  echo "Note: you're in a Wayland session. For the keyboard shortcut to work while you read in other apps,"
  echo "add a custom shortcut in Settings > Keyboard > Custom Shortcuts with this command:"
  echo "  python3 \"$DIR/focus_trainer.py\" --log"
fi
