# beatcut

Beat-synced short-form montage editor for Instagram Reels, TikTok and YouTube Shorts.
Give it a folder of clips and a song; it finds the beats, the drop and the "light events" in your footage
(tail lights, indicators, headlights switching on), drafts a story-order cut list, renders a graded 9:16 video with
glitch-free zooms and beat-centred transitions, and then **measures** the result: every hard cut on its beat frame,
audio present and at Reels loudness, no frozen or black frames.

It works three ways:

- **Claude Code** — ask in plain English ("make a reel from these clips to this song"); the bundled skill drives it.
- **Codex** — same skill, same prompts.
- **By hand** — a normal command-line tool.

---

## 1. Requirements

| | macOS | Ubuntu / Debian | Windows |
|---|---|---|---|
| ffmpeg (with libx264) | `brew install ffmpeg` | `sudo apt install ffmpeg` | use WSL2 (Ubuntu), then the Linux column |
| Python 3.10+ | `brew install python` | `sudo apt install python3 python3-venv` | ↑ |
| git + rsync | preinstalled / `brew install git` | `sudo apt install git rsync` | ↑ |

This repository is **private**, so the device needs access to your GitHub account first. Either run
`gh auth login` (GitHub CLI) or add the device's SSH key to GitHub.

## 2. Install on any device

```bash
git clone https://github.com/AtharvaNikam/beatcut.git ~/beatcut
bash ~/beatcut/scripts/install.sh
beatcut doctor
```

`install.sh` does four things:

1. Creates a private Python environment inside the repo (`.venv/`) and installs beatcut into it.
2. Puts the `beatcut` command on your PATH (`~/.local/bin/beatcut`).
3. Installs the skill for **Claude Code** (`~/.claude/skills/beatcut/`).
4. Installs the same skill for **Codex** (`~/.codex/skills/beatcut/`).

If it prints `NOTE: add ~/.local/bin to PATH`, run the line it shows (e.g.
`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc`) and open a new terminal.

`beatcut doctor` must show every ffmpeg filter as `ok`.

### Update to the latest version

```bash
cd ~/beatcut && git pull && bash scripts/install.sh
```

Re-running the installer is always safe; it also refreshes both skills.

## 3. Use it with Claude Code

After installing, open Claude Code anywhere (terminal, desktop app or IDE) and just ask:

> Make a 25-second beat-synced reel from the clips in ~/Downloads/client-shoot to ~/Music/song.mp3.
> Story: walk up to the car → unlock → reverse out → start it → drive on the highway.

Claude loads the **beatcut** skill automatically (you can also type `/beatcut`). It will:

1. Create a project folder, analyse the song and footage, and look at contact sheets of every clip.
2. Draft the cut list, adjust it to your story, and validate it (off-beat cuts are reported with the exact fix).
3. Render a fast preview, measure it with `verify`, check the review images, then render the final file.

Tell it the story order you want, the song section (e.g. "start at 0:42, 25 seconds"), and the look
(`night` for moody night footage, `natural` for daylight, `none` to keep the original colours).

**Claude Code on the web / cloud sessions:** the environment needs ffmpeg. In the session, run
`sudo apt-get install -y ffmpeg && bash scripts/install.sh` once (or add it to the environment's setup script).

## 4. Use it with Codex

Same install. Codex picks up `~/.codex/skills/beatcut` automatically — check with `codex` → "list your skills",
then ask the same way as in Claude Code.

## 5. Use it by hand

```bash
beatcut init ~/beatcut-jobs/client-job --footage ~/Downloads/client-shoot --song ~/Music/song.mp3 --length 25
beatcut analyze ~/beatcut-jobs/client-job      # beats, drop, light events, contact sheets, brief.md
beatcut draft ~/beatcut-jobs/client-job        # first cut -> edl.json
beatcut validate ~/beatcut-jobs/client-job     # errors + off-beat warnings with exact fixes
beatcut render ~/beatcut-jobs/client-job --preview
beatcut verify ~/beatcut-jobs/client-job --video montage_preview.mp4
beatcut render ~/beatcut-jobs/client-job       # final: renders/montage.mp4
beatcut verify ~/beatcut-jobs/client-job
```

Shortcut: `beatcut run <project> [--preview]` does analyze → draft → render → verify in one go.

| Option (on `init`) | Meaning |
|---|---|
| `--song-start 42.5 --length 25` | use a 25 s section of the song starting at 0:42.5 |
| `--size 1080x1920` | 9:16 (default). Also `1080x1080`, `1920x1080` |
| `--grade night / natural / none` | colour look |
| `--quality share / master` | share ≈ 30 MB per 25 s (default); master = higher bitrate |
| `--exclude "final*"` | ignore files in the footage folder (e.g. old edits) |

Clip order = **filename order**, which is shooting order for phone files. Rename files `01_…`, `02_…` to change the
story, then re-run `analyze` and `draft --force`.

The cut list format and editing rules live in [`skill/beatcut/references/edl-format.md`](skill/beatcut/references/edl-format.md)
and [`skill/beatcut/references/editing-playbook.md`](skill/beatcut/references/editing-playbook.md).

### Project folder layout

```
client-job/
  project.json          settings from init
  edl.json              the cut list (draft writes it; edit it by hand or let the agent)
  analysis/brief.md     tempo, drop, accents, per-clip light events, best/avoid windows
  analysis/sheets/      contact sheet per clip (4 frames/s, labelled with seconds)
  renders/montage.mp4   final video (+ montage.verify.json and montage_review/ images)
```

Keep project folders **outside** the footage folder (`init` refuses otherwise).

## 6. Client-work notes

- **Music rights.** Songs baked into client videos can be muted or blocked (business accounts especially). Use a
  licensed/royalty-free track, or deliver the cut and add the song inside Instagram/TikTok at the same start point.
- **Source quality.** WhatsApp shrinks video to ~576p. Ask clients for the original files (AirDrop, Drive).
- **Privacy.** Check for readable number plates or bystanders' faces before posting.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `beatcut: command not found` | add `~/.local/bin` to PATH (see §2), open a new terminal |
| `doctor` shows a filter MISSING | install a full ffmpeg build (`brew install ffmpeg` / distro ffmpeg), not a minimal one |
| Claude/Codex doesn't offer the skill | re-run `bash scripts/install.sh`, then restart Claude Code / Codex |
| `verify` says FAIL | read the listed item; `validate` usually shows the exact `in` value to fix |
| Render feels slow | use `--preview` while iterating and `--only 3,7` to re-render single shots |

## 8. Development

```bash
cd ~/beatcut
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest -q        # 42 tests, synthetic media, ~1 minute
```

Source: `src/beatcut/` (`beats.py` song analysis, `clips.py` footage analysis, `edl.py` cut list + validation,
`draft.py` first cut, `render.py` renderer, `verify.py` measurement, `cli.py` commands).
