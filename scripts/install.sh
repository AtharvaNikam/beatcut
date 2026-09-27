#!/usr/bin/env bash
# Install beatcut: a venv inside the repo, the `beatcut` command on PATH, and the skill for Claude Code + Codex.
# Re-run after pulling changes to update the command and both skills.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
BIN_DIR="${BEATCUT_BIN_DIR:-$HOME/.local/bin}"

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "beatcut needs Python 3.10+")'
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || {
  echo "ffmpeg/ffprobe not found. macOS: brew install ffmpeg   Ubuntu/Debian: sudo apt install ffmpeg" >&2; exit 1; }

"$PY" -m venv "$REPO/.venv"
"$REPO/.venv/bin/python" -m pip install -q --upgrade pip
"$REPO/.venv/bin/python" -m pip install -q -e "$REPO"

mkdir -p "$BIN_DIR"
ln -sf "$REPO/.venv/bin/beatcut" "$BIN_DIR/beatcut"

for root in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
  mkdir -p "$root/beatcut"
  rsync -a --delete "$REPO/skill/beatcut/" "$root/beatcut/"
  echo "skill installed: $root/beatcut"
done

"$BIN_DIR/beatcut" doctor
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "NOTE: add $BIN_DIR to PATH (e.g. echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc)";; esac
echo "beatcut ready: $(command -v beatcut || echo "$BIN_DIR/beatcut")"
