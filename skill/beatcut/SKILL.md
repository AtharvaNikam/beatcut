---
name: beatcut
description: Edit beat-synced short-form montages (Instagram Reels, TikTok, YouTube Shorts) from a folder of client clips plus a song — analyze beats and footage, draft a story-order cut list, render with a cinematic grade and glitch-free zooms, and verify sync. Use when asked to make a montage/edit/reel/clip synced to music, "cut these videos to this song", a car/real-estate/event/product edit, or to fix sync, zoom or transition glitches in such an edit.
---

# beatcut — beat-synced montage editor

`beatcut` is a CLI (Python + ffmpeg). You do the creative judgement (story, shot choice); the tool does the frame-exact
maths (beats, light events, transitions, zoom warp, rendering, measurement). Never hand-roll ffmpeg for these edits —
every glitch class we hit doing that by hand is fixed inside the tool (see "Why the tool, not raw ffmpeg").

## 0. Check the install
```bash
beatcut doctor
```
If `beatcut` is not found, install it (needs git, Python 3.10+ and ffmpeg with libx264):
```bash
git clone https://github.com/AtharvaNikam/beatcut.git ~/beatcut && bash ~/beatcut/scripts/install.sh
```
If the repo is already cloned somewhere, run its `scripts/install.sh` instead (ask the user where it lives).
`~/.local/bin` must be on PATH; the installer says so if it isn't. On Windows run the installer from Git Bash; it adds
`~/.local/bin` to the user PATH, which only takes effect after Claude Code restarts — until then call `~/.local/bin/beatcut`.

## 1. Set up a project (one folder per client job — never inside the footage folder)
```bash
beatcut init ~/beatcut-jobs/<client>-<date> --footage "<clips folder>" --song "<song file>" \
  [--song-start 42.5] [--length 25] [--size 1080x1920] [--grade night|natural|none] [--exclude "final*"]
```
- Clip order = filename order (phone names sort by time) = story order. Rename files (01_, 02_ …) to change the story.
  Clip ids (c01…) follow that order: after renaming/adding/excluding files re-run `beatcut analyze`, then
  `beatcut draft --force` (or fix ids by hand) — validate errors on any shot whose id now points at a different file.
- `init` refuses a project folder inside the footage folder. You can re-run `init` (or edit project.json
  song_start/length/grade) after `analyze` once you know where the drop is.
- `--song-start/--length`: use the best 15–30 s section of the song (the drop inside it).
- `--grade night` = moody teal/warm night look (cars, city at night); `natural` for daylight; `none` to keep source colours.
- **Choosing the song:** if the user gave no song, or asks for a trending/suitable one, use the sibling skill
  `../trending-audio/` (next to this skill in the skills folder): read its `reference/beatcut-video-editing-workflow.md`
  and run `node "<skills>/trending-audio/scripts/pick-audio.mjs" ...` (e.g. `--list phonk` for car/gym, `--list film --region in`
  for Indian weddings, `--list bgm` for real estate, `--new` for rising tracks). Propose 2-3 tracks with the reason
  (chart, region, freshness, licence fit), let the user pick and supply the song file, and set `--song-start` to the
  section the trend uses. Its table also gives the `--grade` per job type.
- Music rights: remind the user that baked-in copyrighted music gets muted/blocked on business accounts. Safer: licensed
  track, or deliver the cut and add the song in the Instagram/TikTok editor at the same start point.

## 2. Analyze, then LOOK at the footage
```bash
beatcut analyze <project>
```
Read `<project>/analysis/brief.md` (tempo, drop, bar starts, accents, per-clip light events, best/avoid windows), then
open every contact sheet `<project>/analysis/sheets/cNN.jpg` (4 frames/s, tiles labelled with source seconds).
Claude Code: Read tool on the .jpg. Codex: view the image file (view_image / image attach). Understand what each clip
shows before editing — the numbers can't tell a mirror unfolding from a car reversing.

## 3. Draft, then edit the cut list
```bash
beatcut draft <project>            # writes <project>/edl.json (add --force to overwrite)
```
The draft is a starting point: story order, cuts on the strong accents before the drop (bar starts are worked out
separately for the build and the drop; the drop itself may fall mid-bar) and ~1 s after it, light events placed on
cuts, flash+punch on the drop. Improve it by editing `edl.json` — format in [references/edl-format.md](references/edl-format.md),
editing rules in [references/editing-playbook.md](references/editing-playbook.md). Ask the user for the story order they
want when it isn't obvious (e.g. "walk up → unlock → reverse out → start → drive").

## 4. Validate → preview → verify → final
```bash
beatcut validate <project>                    # fix every ERROR; warnings include exact `in` values to land light events
beatcut render <project> --preview            # half-size, fast
beatcut verify <project> --video montage_preview.mp4
beatcut render <project>                      # final: renders/montage.mp4 (share quality ≈ 30 MB per 25 s)
beatcut verify <project>
```
After `verify`, open `renders/<name>_review/timeline.jpg` and the `shotNN.jpg` strips (first|mid|last frame) and check
with your own eyes: black/blurry frames, subject cut off by the zoom anchor, a shot too short to read, a transition that
looks like a glitch. Re-render single shots with `--only 3,7` while iterating (parts whose settings changed re-render
automatically). `STATUS: FAIL` must be fixed; read every warning and decide.
Shortcut: `beatcut run <project> [--preview] [--out NAME]` = analyze + draft + render + verify of renders/NAME.mp4;
`beatcut run <project> --edl mine.json` renders your hand-made cut without re-drafting (edl.json is left alone).

## 5. Deliver
Sound effects (whoosh on fade/whip, riser into the drop, impact on flash/punch) go on the finished render, timed from edl.json:
`node "<skills>/trending-audio/scripts/sfx-cues.mjs" <project>/edl.json --video <project>/renders/montage.mp4 --whoosh … --impact … --riser … [--run]`
(video is stream-copied; details in trending-audio's workflow doc).
Send `renders/montage.mp4`. Extra jobs around the montage (captions or a logo on the finished file, compressing to a
size limit, converting or trimming sources first) are ordinary edits: use the `video-editing` skill if it is installed,
and never re-cut the montage itself outside beatcut. Report: story order, where the big hits land (drop time, light
events), anything you couldn't do with the footage (e.g. "no clip actually shows the car moving — used a push-in on the reverse lamps"), and
privacy items (readable number plates, faces of bystanders).

## Why the tool, not raw ffmpeg (bugs it already fixes)
- `zoompan` jitters ~1.2 px/frame (integer crop grid) → beatcut warps each frame with an exact float affine transform.
- xfade `dissolve` is random pixel noise, not a crossfade → beatcut uses `fade`; `fadeblack` snaps → `dip` is two in-shot fades.
- Float xfade offsets round a frame late and shift every later cut → offsets are placed half a frame early.
- Full-quality slow-mo (motion interpolation) can blend a small light switch across 1–2 frames — keep light hits in
  speed-1.0 shots or check that shot's strip in the review images. Short clips fill with slow-mo, never a frozen frame.
- "Shake" reads as a glitch → `punch` (5 % push-in easing out over 8 frames).
- `ffmpeg -ss` lands on the next source frame and phone clips are variable-frame-rate → each shot is anchored on the
  exact source frame `validate` reasons about; `validate` reports where every light event lands.
- Review stills, analysis and verify all read exact frames (no constant-frame-rate padding).
