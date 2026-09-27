#!/usr/bin/env bash
# Install beatcut: a venv inside the repo, the `beatcut` command on PATH, and the skill for Claude Code + Codex.
# Re-run after pulling changes to update the command and both skills.
# Works on macOS, Linux and Windows (run it from Git Bash).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${BEATCUT_BIN_DIR:-$HOME/.local/bin}"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) WIN=1 ;; *) WIN=0 ;; esac
unixpath() { if [ "$WIN" = 1 ]; then cygpath -u "$1"; else printf '%s\n' "$1"; fi; }

# Python 3.10+: $PYTHON if set, else the first candidate that really runs (on Windows `python3` is often a Store stub).
py_ok() { "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
if [ -n "${PYTHON:-}" ]; then PY=("$PYTHON")
elif py_ok python3; then PY=(python3)
elif py_ok python; then PY=(python)
elif py_ok py -3; then PY=(py -3)
elif command -v uv >/dev/null && UVPY="$(uv python find '>=3.10' 2>/dev/null)"; then PY=("$UVPY")
else PY=(python3); fi
py_ok "${PY[@]}" || { echo "beatcut needs Python 3.10+ (none found; set PYTHON=/path/to/python)" >&2; exit 1; }
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || {
  echo "ffmpeg/ffprobe not found. macOS: brew install ffmpeg   Ubuntu/Debian: sudo apt install ffmpeg   Windows: winget install Gyan.FFmpeg" >&2; exit 1; }

"${PY[@]}" -m venv "$REPO/.venv"
VBIN="$REPO/.venv/bin"; [ "$WIN" = 1 ] && VBIN="$REPO/.venv/Scripts"
"$VBIN/python" -m pip install -q --upgrade pip
"$VBIN/python" -m pip install -q -e "$REPO"

mkdir -p "$BIN_DIR"
if [ "$WIN" = 1 ]; then
  # Git Bash can't make real symlinks by default: write shims instead (bash, and .cmd for PowerShell/cmd).
  rm -f "$BIN_DIR/beatcut"
  printf '#!/usr/bin/env bash\nexec "%s/beatcut.exe" "$@"\n' "$VBIN" > "$BIN_DIR/beatcut"
  printf '@"%s\\beatcut.exe" %%*\r\n' "$(cygpath -w "$VBIN")" > "$BIN_DIR/beatcut.cmd"
  chmod +x "$BIN_DIR/beatcut"
else
  ln -sf "$VBIN/beatcut" "$BIN_DIR/beatcut"
fi

for root in "$(unixpath "${CLAUDE_CONFIG_DIR:-$HOME/.claude}")/skills" "$(unixpath "${CODEX_HOME:-$HOME/.codex}")/skills"; do
  mkdir -p "$root/beatcut"
  if command -v rsync >/dev/null; then
    rsync -a --delete "$REPO/skill/beatcut/" "$root/beatcut/"
  else
    rm -rf "${root:?}/beatcut" && cp -R "$REPO/skill/beatcut" "$root/beatcut"
  fi
  echo "skill installed: $root/beatcut"
done

"$BIN_DIR/beatcut" doctor
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) if [ "$WIN" = 1 ]; then
       WBIN="$(cygpath -w "$BIN_DIR")"
       powershell.exe -NoProfile -Command "\$p=[Environment]::GetEnvironmentVariable('Path','User'); if ((\$p -split ';') -notcontains '$WBIN') { [Environment]::SetEnvironmentVariable('Path', (\$p.TrimEnd(';') + ';$WBIN'), 'User') }"
       echo "NOTE: added $WBIN to your user PATH — open a new terminal (and restart Claude Code) to pick it up"
     else
       echo "NOTE: add $BIN_DIR to PATH (e.g. echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc)"
     fi ;;
esac
echo "beatcut ready: $(command -v beatcut || echo "$BIN_DIR/beatcut")"
