"""beatcut command line. Every command takes the project folder as its first argument."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .beats import analyze_song, summary
from .clips import analyze_footage, clip_brief
from .draft import draft as make_draft
from .edl import EDLError, load as load_edl, save as save_edl, validate
from .media import MediaError, has_encoder, has_filters, require_ffmpeg
from .project import GRADES, QUALITIES, Project, ProjectError, dumps, load_json, parse_size
from .render import render
from .verify import verify

GUIDE = """## Workflow for the editor (human or agent)
1. Look at every contact sheet listed above (tiles are labelled with source seconds). Note the story: arrival,
   details, the hero moment, motion. Light events are the moments to sync to beats.
2. `beatcut draft "{root}"` writes a first cut to edl.json (story order, cuts on beats, light hits on cuts).
   Edit edl.json by hand to improve it — see the skill's references/edl-format.md for every field.
3. `beatcut validate "{root}"` — fix every ERROR. Warnings tell you off-beat cuts and light events that land
   between beats (with the exact `in` value that would land them).
4. `beatcut render "{root}" --preview` then `beatcut verify "{root}" --video montage_preview.mp4` — then LOOK at
   renders/montage_preview_review/timeline.jpg and shotNN.jpg.
5. `beatcut render "{root}"` for the final file, then `beatcut verify "{root}"` again.
"""


def _proj(args) -> Project:
    return Project.load(args.project)


def _analysis(p: Project) -> tuple[dict, list[dict]]:
    return load_json(p.beats_path, "beats.json"), load_json(p.clips_path, "clips.json")["clips"]


def cmd_init(a) -> int:
    w, h = parse_size(a.size)
    p = Project.create(a.project, a.footage, a.song, song_start=a.song_start, length=a.length, width=w, height=h,
                       fps=a.fps, grade=a.grade, quality=a.quality, exclude=a.exclude or [])
    total = p.total_length()
    print(f"project ready: {p.root}\n  footage {p.footage}\n  song    {p.song} (from {p.song_start}s, montage {total:.2f}s)"
          f"\n  output  {p.width}x{p.height} @ {p.fps} fps, grade {p.grade}, quality {p.quality}")
    if p.length and total < p.length - 0.01:
        print(f"  note: only {total:.2f}s of song after --song-start {p.song_start}s — montage shortened from {p.length:g}s")
    if total > 90:
        print("  note: that is longer than a typical Reel — pass --length 25 (or similar) to use a section of the song")
    print(f'next: beatcut analyze "{p.root}"')
    return 0


def cmd_analyze(a) -> int:
    if getattr(a, "bpm", None) is not None and not 40 <= a.bpm <= 250:
        raise ProjectError(f"--bpm must be 40-250, got {a.bpm}")
    p = _proj(a)
    t = time.time()
    total = p.total_length()
    bm = analyze_song(str(p.resolve(p.song)), p.song_start, total, bpm=a.bpm)
    p.analysis.mkdir(parents=True, exist_ok=True)
    p.beats_path.write_text(dumps(bm, indent=1), encoding="utf-8")
    print(summary(bm).splitlines()[1] + (f" | drop {bm['drop']}s" if bm["drop"] else " | no drop"))
    clips = analyze_footage(str(p.resolve(p.footage)), p.sheets, exclude=p.exclude)
    p.clips_path.write_text(dumps({"clips": clips}), encoding="utf-8")
    brief = [f"# beatcut brief — {p.root.name}", f"Montage: {total:.2f}s, {p.width}x{p.height} @ {p.fps} fps, grade {p.grade}",
             "", "## Song", summary(bm), "", "## Footage (story order = filename order)"]
    brief += [clip_brief(c) for c in clips]
    brief += ["", GUIDE.format(root=p.root)]
    p.brief_path.write_text("\n".join(brief), encoding="utf-8")
    print(f"{len(clips)} clips analysed in {time.time() - t:.0f}s — read {p.brief_path} and the sheets in {p.sheets}")
    return 0


def cmd_draft(a) -> int:
    p = _proj(a)
    edl_out = getattr(a, "edl_out", None)
    out = p.in_root(edl_out) if edl_out else p.edl_path
    if out.suffix.lower() != ".json" or out == p.file or p.analysis in out.parents or p.renders in out.parents:
        raise ProjectError(f"--out must be a .json file in the project (not project.json, analysis/ or renders/): {edl_out}")
    if out.exists() and not a.force:
        print(f"{out} exists — pass --force to overwrite (or --out other.json)", file=sys.stderr)
        return 2
    bm, clips = _analysis(p)
    shots = make_draft(bm, clips, p.total_length(), p.fps)
    save_edl(shots, out, {"generated_by": f"beatcut {__version__} draft"})
    for i, s in enumerate(shots):
        print(f"  {i:02d} {s['start']:6.2f}-{s['end']:6.2f} {s['clip']} in {s['in']:6.2f} {s['transition']['type']:5s} {','.join(s['fx']):14s} {s['note']}")
    print(f'wrote {out} ({len(shots)} shots). next: beatcut validate "{p.root}"')
    return 0


def _load_valid(p: Project, edl_arg: str | None, strict: bool = True) -> tuple[list[dict], dict, list[dict], dict]:
    bm, clips = _analysis(p)
    shots = load_edl(p.in_root(edl_arg) if edl_arg else p.edl_path, p.total_length())
    rep = validate(shots, bm, clips, p.fps, p.total_length())
    return shots, bm, clips, rep


def _print_validation(rep: dict) -> None:
    for e in rep["errors"]:
        print(f"ERROR   {e}")
    for w in rep["warnings"]:
        print(f"warning {w}")
    on = sum(e["on_beat"] for e in rep["events"])
    print(f"light events in the cut: {on}/{len(rep['events'])} on a beat")
    print("OK" if rep["ok"] else f"{len(rep['errors'])} error(s) — fix edl.json before rendering")


def cmd_validate(a) -> int:
    p = _proj(a)
    *_, rep = _load_valid(p, a.edl)
    _print_validation(rep)
    if a.json:
        print(dumps(rep, indent=1))
    return 0 if rep["ok"] else 1


def cmd_render(a) -> int:
    p = _proj(a)
    shots, bm, clips, rep = _load_valid(p, a.edl)
    if not rep["ok"]:
        _print_validation(rep)
        return 1
    out = p.out_video(a.out, "_preview" if a.preview else "")
    only = {int(x) for x in a.only.split(",")} if a.only else None
    t = time.time()
    print(f"rendering {len(shots)} shots -> {out}{' (preview)' if a.preview else ''}")
    render(shots, clips, bm, p, out, preview=a.preview, only=only, jobs=a.jobs)
    a.video = out.name  # `run` verifies exactly the file it rendered
    print(f'done in {time.time() - t:.0f}s: {out}\nnext: beatcut verify "{p.root}" --video {out.name}')
    return 0


def cmd_verify(a) -> int:
    p = _proj(a)
    shots, bm, clips, _ = _load_valid(p, a.edl)
    video = p.in_root(a.video, p.renders) if a.video else p.out_video(None)
    if not video.exists():
        have = sorted(f.name for f in p.renders.glob("*.mp4") if not f.name.endswith(".partial.mp4")) if p.renders.is_dir() else []
        print(f"{video} not found — render first" + (f" (renders/ has: {', '.join(have)} — pass --video NAME)" if have else ""),
              file=sys.stderr)
        return 2
    if video.suffix.lower() != ".mp4":
        print(f"{video} is not a render (.mp4)", file=sys.stderr)
        return 2
    man = video.with_suffix(".json")
    try:
        m = json.loads(man.read_text(encoding="utf-8-sig")) if man.exists() else {}
    except json.JSONDecodeError:
        m = {}
    W, H = m.get("width", p.width), m.get("height", p.height)
    rep = verify(video, shots, bm, clips, p.fps, W, H, review_dir=video.parent / f"{video.stem}_review")
    for line in rep["info"]:
        print(line)
    if rep.get("loudness"):
        print(f"audio: {rep['loudness']['lufs']} LUFS integrated, true peak {rep['loudness']['true_peak']} dBFS")
    on = sum(c["result"] == "on frame" for c in rep["cuts"])
    print(f"hard cuts on their beat frame: {on}/{len(rep['cuts'])} (rest not measurable: similar shots or flat frames — see cuts in the .verify.json)")
    print(f"visible light events on a beat: {sum(e['on_beat'] for e in rep['events'])}/{len(rep['events'])}")
    for f in rep["fail"]:
        print(f"FAIL    {f}")
    for w in rep["warn"]:
        print(f"warning {w}")
    print(f"review images: {video.parent / (video.stem + '_review')}\nSTATUS: {rep['status']}")
    return 1 if rep["status"] == "FAIL" else 0


def cmd_run(a) -> int:
    """analyze -> draft -> render -> verify. With --edl, your hand-made cut is rendered and nothing is re-drafted."""
    for step in (cmd_analyze, cmd_render, cmd_verify) if a.edl else (cmd_analyze, cmd_draft, cmd_render, cmd_verify):
        rc = step(a)
        if rc:
            return rc
    return 0


def cmd_doctor(a) -> int:
    ok = True
    try:
        require_ffmpeg()
        print("ffmpeg/ffprobe: found")
    except MediaError as e:
        print(f"MISSING: {e}")
        return 1
    for name, have in has_filters("minterpolate", "xfade", "lutyuv", "colorbalance", "curves", "vignette", "noise", "rgbashift").items():
        print(f"filter {name}: {'ok' if have else 'MISSING'}")
        ok &= have
    enc = has_encoder("libx264")
    print(f"encoder libx264: {'ok' if enc else 'MISSING'}")
    return 0 if ok and enc else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="beatcut", description="Beat-synced short-form montage editor.")
    ap.add_argument("--version", action="version", version=f"beatcut {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="create a project folder")
    i.add_argument("project")
    i.add_argument("--footage", required=True, help="folder with the clips (not recursive; filename order = story order)")
    i.add_argument("--song", required=True, help="music file (you need the rights to use it commercially)")
    i.add_argument("--song-start", type=float, default=0.0, help="start offset in the song (s)")
    i.add_argument("--length", type=float, default=0.0, help="montage length (s); default = rest of the song")
    i.add_argument("--size", default="1080x1920", help="output WxH (1080x1920 Reels/TikTok/Shorts, 1080x1080, 1920x1080)")
    i.add_argument("--fps", type=int, default=30)
    i.add_argument("--grade", choices=GRADES, default="night")
    i.add_argument("--quality", choices=QUALITIES, default="share", help="share = <~30MB/25s, master = higher bitrate")
    i.add_argument("--exclude", action="append", help="glob of footage files to ignore (repeatable)")
    i.set_defaults(fn=cmd_init)
    for name, fn, hlp in (("analyze", cmd_analyze, "analyse song + footage, write brief + contact sheets"),
                          ("draft", cmd_draft, "write a first cut to edl.json"),
                          ("validate", cmd_validate, "check edl.json against beats + footage"),
                          ("render", cmd_render, "render edl.json"),
                          ("verify", cmd_verify, "measure a render (sync, glitches, audio) + review images"),
                          ("run", cmd_run, "analyze + draft + render + verify in one go")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("project")
        s.set_defaults(fn=fn)
        if name in ("analyze", "run"):
            s.add_argument("--bpm", type=float, help="override detected tempo")
        if name in ("draft", "run"):
            s.add_argument("--force", action="store_true", help="overwrite an existing edl.json")
        if name == "draft":
            s.add_argument("--out", dest="edl_out", help="write to this file inside the project instead of edl.json")
        if name in ("validate", "render", "verify", "run"):
            s.add_argument("--edl", help="EDL file inside the project (default edl.json)")
        if name == "validate":
            s.add_argument("--json", action="store_true", help="also print the full report as JSON")
        if name in ("render", "run"):
            s.add_argument("--preview", action="store_true", help="half-size, fast settings")
            s.add_argument("--out", help="output file name (written to renders/)")
            s.add_argument("--only", help="re-render only these shot indexes, e.g. 3,4 (others reuse parts)")
            s.add_argument("--jobs", type=int, help="parallel shots (default: cpu/3, max 4)")
        if name == "verify":
            s.add_argument("--video", help="render to check (file name in renders/, default montage.mp4)")
    d = sub.add_parser("doctor", help="check ffmpeg features")
    d.set_defaults(fn=cmd_doctor, project=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to the ANSI codepage; clip names can be any script
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.cmd == "run":
        args.video = None  # set by cmd_render to the file it actually wrote
    try:
        if args.cmd != "doctor":
            require_ffmpeg()
        return args.fn(args) or 0
    except (ProjectError, EDLError, MediaError, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
