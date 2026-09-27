"""Renderer. Per shot: ffmpeg A (denoise, retime, grade @ source res) -> rgb24 -> exact float affine warp (zoom/punch)
-> ffmpeg B (finish + fx). Then stitch with frame-exact hard cuts / centred crossfades and mux the song.

Why the Python warp: ffmpeg's zoompan snaps its crop window to whole (chroma-aligned) pixels, so every zoom shimmers
~1.2 px/frame; `perspective` is worse. One bicubic resample from source to output is exact and sharper."""
from __future__ import annotations

import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .project import dumps
from .clips import luma_between
from .edl import first_src_frame_time, handles, n_frames, tr_frames
from .grade import auto_gamma, post_chain, pre_chain
from .media import MediaError, run

PUNCH_FRAMES, PUNCH_AMT = 8, 0.05
TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
XFADE = {"fade": "fade", "whip": "hblur"}  # xfade's own "dissolve" is per-pixel dither noise — never use it


def effective_fades(shots: list[dict]) -> list[dict]:
    """Resolve 'dip' transitions into fade_out/fade_in on the two shots around the cut (hard cut at black)."""
    fades = [{"in": None, "out": None} for _ in shots]
    for i, s in enumerate(shots):
        d = s["end"] - s["start"]
        if "fade_in" in s["fx"]:
            fades[i]["in"] = min(0.5, d / 2)
        if "fade_out" in s["fx"]:
            fades[i]["out"] = float(s.get("fade_out") or min(0.4, d / 2))
        if i and s["transition"]["type"] == "dip":
            half = s["transition"]["dur"] / 2
            fades[i]["in"] = fades[i]["in"] or min(half, d / 2)
            fades[i - 1]["out"] = fades[i - 1]["out"] or min(half, (shots[i - 1]["end"] - shots[i - 1]["start"]) / 2)
    return fades


def warp_rect(s: dict, sw: int, sh: int, W: int, H: int, k: int, n: int, hf: int) -> tuple[float, float, float, float]:
    """Source-space crop rectangle (x0, y0, cw, ch) for output frame k — floats, never rounded."""
    zs, ze = s["zoom"]
    p = min(max((k - hf) / max(n - 1, 1), 0.0), 1.0)
    z = zs * (ze / zs) ** p  # log-space ramp = constant perceived zoom speed
    q = (k - hf) / PUNCH_FRAMES
    if "punch" in s["fx"] and 0 <= q < 1:
        z *= 1 + PUNCH_AMT * (1 - q) ** 2  # beat hit: quick push-in that eases out, no positional rattle
    ax, ay = s["anchor"]
    s0 = max(W / sw, H / sh)  # cover the output frame
    cw, ch = W / (s0 * z), H / (s0 * z)
    return (sw - cw) * ax, (sh - ch) * ay, cw, ch


def _post_fx(s: dict, fade: dict, n: int, hf: int, fps: int) -> list[str]:
    d, t0 = n / fps, hf / fps
    ch = []
    if "glitch" in s["fx"]:
        ch.append(f"rgbashift=rh=-9:bh=9:enable='between(t,{t0:.4f},{t0 + 0.16:.4f})'")
    if "pop" in s["fx"]:
        k = f"(gte(t,{t0:.4f})*max(0,1-(t-{t0:.4f})/0.267))"
        ch.append(f"eq=brightness='0.07*{k}':contrast='1+0.08*{k}':eval=frame")
    if "blur_in" in s["fx"]:
        ch.append(f"gblur=sigma=14:enable='between(t,{t0:.4f},{t0 + 0.1:.4f})',gblur=sigma=5:enable='between(t,{t0 + 0.1:.4f},{t0 + 0.2:.4f})'")
    if fade["in"]:
        ch.append(f"fade=t=in:st={t0:.4f}:d={fade['in']:.3f}")
    if "flash" in s["fx"]:
        ch.append(f"fade=t=in:st={t0:.4f}:d=0.14:color=white")
    if fade["out"]:
        ch.append(f"fade=t=out:st={t0 + d - fade['out']:.4f}:d={fade['out']:.3f}")
    return ch


def render_shot(i: int, s: dict, clip: dict, fade: dict, hf: int, tf: int, cfg: dict, out: Path) -> None:
    W, H, fps, grade, preview = cfg["W"], cfg["H"], cfg["fps"], cfg["grade"], cfg["preview"]
    n = n_frames(s, fps)
    total = hf + n + tf
    sp = s["speed"]
    # anchor on the exact source frame validate reasons about (ffmpeg -ss lands on the first frame at/after `in`),
    # seek half a source frame early so float formatting can never skip it, then shift that frame to t=0
    t0 = max(0.0, first_src_frame_time(s["in"], clip["fps"]) - hf / fps * sp)
    src_in = max(0.0, t0 - 0.5 / clip["fps"])
    src_len = total / fps * sp
    sw, sh = clip["width"], clip["height"]
    gamma = float(s["gamma"]) if s.get("gamma") else auto_gamma(luma_between(clip, s["in"], s["in"] + n / fps * sp), grade)
    pre = [] if preview else ["deblock=filter=strong:block=8", "hqdn3d=1.5:1.5:5:5"]
    pre.append(f"setpts=(PTS-{t0 - src_in:.6f}/TB)/{sp}")
    if clip["fps"] * sp < fps - 0.5 and not preview:  # slow-mo / low-fps source: motion-interpolate instead of duplicating
        pre.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd_threshold=3")
    else:
        pre.append(f"fps={fps}:start_time=0")
    pre += pre_chain(grade, gamma, cfg["drop"] is not None and s["start"] >= cfg["drop"] - 0.01)
    pre.append("tpad=stop_mode=clone:stop_duration=3")  # hold last frame if the source runs short
    post = post_chain(grade) if not preview else post_chain("none")[:1]
    post += _post_fx(s, fade, n, hf, fps)
    enc_q = ["-preset", "ultrafast", "-crf", "20"] if preview else ["-preset", "medium", "-crf", "12"]
    derr, eerr = tempfile.TemporaryFile(), tempfile.TemporaryFile()  # files, not pipes: a chatty ffmpeg can't deadlock us
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{src_in:.6f}", "-t", f"{src_len + 0.6:.3f}", "-i", clip["file"],
                            "-an", "-vf", ",".join(pre), "-frames:v", str(total), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE, stderr=derr)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(fps), "-color_range", "pc", "-i", "-", "-vf", ",".join(post), "-frames:v", str(total),
                            "-c:v", "libx264", *enc_q, "-pix_fmt", "yuv420p", *TAGS, str(out)],
                           stdin=subprocess.PIPE, stderr=eerr)
    fb = sw * sh * 3
    try:
        for k in range(total):
            buf = dec.stdout.read(fb)
            if len(buf) < fb:
                dec.wait()
                derr.seek(0)
                raise MediaError(f"shot {i}: decoder produced {k}/{total} frames: {derr.read().decode(errors='replace')[-500:]}")
            x0, y0, cw, chh = warp_rect(s, sw, sh, W, H, k, n, hf)
            im = Image.frombuffer("RGB", (sw, sh), buf, "raw", "RGB", 0, 1)
            enc.stdin.write(im.transform((W, H), Image.AFFINE, (cw / W, 0, x0, 0, chh / H, y0), Image.BICUBIC).tobytes())
    finally:
        dec.stdout.close()
        enc.stdin.close()
        dec.wait()
        rc = enc.wait()
        eerr.seek(0)
        err = eerr.read().decode(errors="replace")
        derr.close()
        eerr.close()
    if rc:
        raise MediaError(f"shot {i}: encoder failed: {err[-800:]}")


def render(shots: list[dict], clips: list[dict], beats: dict, proj, out: Path, preview: bool = False,
           only: set[int] | None = None, jobs: int | None = None, log=print) -> dict:
    by_id = {c["id"]: c for c in clips}
    fps = proj.fps
    W, H = (proj.width // 4 * 2, proj.height // 4 * 2) if preview else (proj.width, proj.height)
    cfg = {"W": W, "H": H, "fps": fps, "grade": proj.grade, "preview": preview, "drop": beats.get("drop")}
    parts_dir = out.parent / f"{out.stem}_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    fades = effective_fades(shots)
    hts = [handles(shots, i, fps) for i in range(len(shots))]
    parts = [parts_dir / f"s{i:02d}.mp4" for i in range(len(shots))]
    keys = [dumps([shots[i], by_id[shots[i]["clip"]]["file"], fades[i], hts[i], cfg], sort_keys=True) for i in range(len(shots))]
    fresh = [parts[i].exists() and parts[i].with_suffix(".key").is_file() and parts[i].with_suffix(".key").read_text(encoding="utf-8-sig") == keys[i]
             for i in range(len(shots))]
    todo = [i for i in range(len(shots)) if only is None or i in only or not fresh[i]]  # stale parts always re-render
    for i in todo:
        parts[i].with_suffix(".key").unlink(missing_ok=True)
    workers = jobs or max(1, min(4, (os.cpu_count() or 4) // 3))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {i: ex.submit(render_shot, i, shots[i], by_id[shots[i]["clip"]], fades[i], *hts[i], cfg, parts[i]) for i in todo}
        for i in sorted(futs):
            futs[i].result()
            parts[i].with_suffix(".key").write_text(keys[i], encoding="utf-8")
            s = shots[i]
            log(f"  shot {i:02d} {s['clip']} {s['start']:6.2f}-{s['end']:6.2f} x{s['speed']} {s['transition']['type']:4s} {','.join(s['fx'])}")
    total = sum(n_frames(s, fps) for s in shots)
    inputs, fc, cur = [], [], "[p0]"
    for i, p in enumerate(parts):
        inputs += ["-i", str(p)]
        fc.append(f"[{i}:v]fps={fps},settb=AVTB,setpts=PTS-STARTPTS[p{i}]")
    for i in range(1, len(parts)):
        cut, T = round(shots[i]["start"] * fps), tr_frames(shots[i], fps)
        if T:  # centred on the cut; half a frame early so the float offset can never round a frame late
            fc.append(f"{cur}[p{i}]xfade=transition={XFADE[shots[i]['transition']['type']]}:duration={T / fps:.4f}"
                      f":offset={(cut - T // 2 - 0.5) / fps:.6f}[r{i}]")
        else:
            fc.append(f"{cur}[p{i}]concat=n=2:v=1:a=0[r{i}]")
        cur = f"[r{i}]"
    dur = total / fps
    tmp = out.with_name(out.stem + ".partial.mp4")
    afade = f"afade=t=out:st={max(0.0, dur - 0.08):.3f}:d=0.08" + (",afade=t=in:d=0.02" if proj.song_start > 0 else "")
    q = (["-preset", "veryfast", "-crf", "24"] if preview else
         ["-preset", "slow", "-crf", "15", "-tune", "grain"] if proj.quality == "master" else
         ["-preset", "slow", "-crf", "18", "-maxrate", "9M", "-bufsize", "18M", "-tune", "grain"])
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-ss", f"{proj.song_start:.3f}", "-t", f"{dur:.3f}", "-i", str(proj.resolve(proj.song)),
         "-filter_complex", ";".join(fc), "-map", cur, "-map", f"{len(parts)}:a", "-af", afade, "-frames:v", str(total),
         "-c:v", "libx264", *q, "-pix_fmt", "yuv420p", "-profile:v", "high", *TAGS,
         "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
         "-r", str(fps), "-c:a", "aac", "-b:a", "256k", "-t", f"{dur:.3f}", "-movflags", "+faststart", str(tmp)])
    os.replace(tmp, out)  # never leave a half-written montage behind
    manifest = {"video": str(out), "frames": total, "fps": fps, "width": W, "height": H, "preview": preview,
                "grade": proj.grade, "shots": shots}
    out.with_suffix(".json").write_text(dumps(manifest, indent=1), encoding="utf-8")
    return manifest
