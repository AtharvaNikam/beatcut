"""Measure a rendered montage: frame-exact cuts, light events on beats, duplicate/black frames, audio.
Zoom precision is guaranteed by the renderer's exact warp and covered by tests/test_warp.py.
Also writes review images (first|mid|last frame per shot + a timeline sheet) for a human/agent eyeball pass."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .project import dumps
from .beats import cut_points, nearest
from .clips import detect_events, frame_stats
from .edl import n_frames, tr_frames
from .media import decode_frames, probe_video, run
from .render import effective_fades


def loudness(video: Path) -> dict:
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr.decode()
    li = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)
    return {"lufs": float(li[-1]) if li else None, "true_peak": float(tp[-1]) if tp else None}


def review_images(video: Path, shots: list[dict], fps: int, out: Path, W: int = 1080, H: int = 1920) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    PW = 240
    PH = int(round(PW * H / W / 2)) * 2  # keep the project's aspect (9:16, 1:1, 16:9)
    mids, files = [], []
    want = sorted({f for s in shots for f in (round(s["start"] * fps), (round(s["start"] * fps) + round(s["end"] * fps)) // 2,
                                                  round(s["end"] * fps) - 1)})
    got = decode_frames(video, PW, PH, vf="select='" + "+".join(f"eq(n\\,{f})" for f in want) + "'")
    frame = dict(zip(want, got))
    blank = Image.new("RGB", (PW, PH))
    for i, s in enumerate(shots):
        a, b = round(s["start"] * fps), round(s["end"] * fps)
        ims = [Image.fromarray(frame[f]) if f in frame else blank for f in (a, (a + b) // 2, b - 1)]
        c = Image.new("RGB", (3 * (PW + 4), PH + 30), "black")
        d = ImageDraw.Draw(c)
        d.text((4, 2), f"shot {i:02d} {s['clip']} {s['start']:.2f}-{s['end']:.2f} in {s['in']:.2f} x{s['speed']} "
                       f"{s['transition']['type']} {','.join(s['fx'])}", fill="yellow")
        d.text((4, 16), "first | mid | last  (first/last may be mid-transition by design)", fill="gray")
        for k, im in enumerate(ims):
            c.paste(im, (k * (PW + 4), 30))
        p = out / f"shot{i:02d}.jpg"
        c.save(p, quality=85)
        files.append(str(p))
        mids.append(ims[1])
    cols, tw = 10, 120
    th = int(PH * tw / PW)
    sheet = Image.new("RGB", (cols * (tw + 3), ((len(mids) + cols - 1) // cols) * (th + 16)), "black")
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(mids):
        x, y = (i % cols) * (tw + 3), (i // cols) * (th + 16)
        sheet.paste(im.resize((tw, th)), (x, y + 14))
        d.text((x + 2, y), f"{i:02d} {shots[i]['start']:.2f}", fill="yellow")
    sheet.save(out / "timeline.jpg", quality=85)
    return files + [str(out / "timeline.jpg")]


def verify(video: Path, shots: list[dict], beats: dict, clips: list[dict], fps: int, W: int, H: int,
           review_dir: Path | None = None) -> dict:
    info = probe_video(video)
    rep: dict = {"video": str(video), "fail": [], "warn": [], "info": []}
    expected = sum(n_frames(s, fps) for s in shots)
    a = decode_frames(video, 90, 160)
    st = frame_stats(a)
    L, d = st["luma"], st["motion"]
    rep["info"].append(f"{len(a)} frames ({len(a) / fps:.3f}s) at {info.width}x{info.height}, {Path(video).stat().st_size / 1e6:.1f} MB")
    if len(a) != expected:
        rep["fail"].append(f"frame count {len(a)} != expected {expected}")
    if (info.width, info.height) != (W, H):
        rep["warn"].append(f"size {info.width}x{info.height} != project {W}x{H} (preview render?)")
    if not info.has_audio:
        rep["fail"].append("no audio stream")
    else:
        lo = loudness(Path(video))
        rep["loudness"] = lo
        if lo["lufs"] is None or lo["lufs"] < -40:
            rep["fail"].append(f"audio is silent (integrated {lo['lufs']} LUFS)")
        elif not -18 <= lo["lufs"] <= -8:
            rep["warn"].append(f"loudness {lo['lufs']} LUFS (Reels/TikTok sit around -14)")
        if lo["true_peak"] is not None and lo["true_peak"] > -0.5:
            rep["warn"].append(f"true peak {lo['true_peak']} dBFS — may clip after platform transcode")
    # A cut changes picture content; a light ramp or fade only changes brightness. So judge cuts by the drop in
    # normalised correlation between consecutive frames, not by raw pixel difference.
    g = a.astype(np.float32).mean(3).reshape(len(a), -1)
    sd = g.std(1)
    z = (g - g.mean(1, keepdims=True)) / (sd[:, None] + 1e-6)
    ncc = np.r_[1.0, (z[1:] * z[:-1]).mean(1)]  # ncc[k] = similarity of frame k to k-1
    cuts = []
    for i, s in enumerate(shots[1:], 1):
        c = round(s["start"] * fps)
        if s["transition"]["type"] != "cut" or c + 1 >= len(d) or c < 2:
            continue
        w = ncc[c - 1:c + 2]
        if "flash" in s["fx"]:  # the white flash frame itself is the cut: biggest brightness jump must be on it
            off = int(np.argmax(np.diff(L[c - 2:c + 2]))) - 1
            res = "on frame" if off == 0 else f"OFF by {off} frame"
            if off:
                rep["fail"].append(f"cut {i} at {s['start']:.3f}s (flash) lands {abs(off)} frame {'early' if off < 0 else 'late'}")
            cuts.append({"shot": i, "t": s["start"], "result": res})
            continue
        if min(sd[c - 2:c + 2]) < 2.0:
            res = "not measurable (cut to/from a flat black/white frame)"
        elif w[1] > 0.9 or min(w) > 0.75:
            res = "similar shots (cut not measurable)"
        elif w[1] <= w[0] and w[1] <= w[2]:
            res = "on frame"
        else:
            early = w[0] < w[2]  # lowest similarity one frame before the planned cut
            res = f"OFF by {-1 if early else 1} frame"
            rep["fail"].append(f"cut {i} at {s['start']:.3f}s lands a frame {'early' if early else 'late'}")
        cuts.append({"shot": i, "t": s["start"], "result": res})
    rep["cuts"] = cuts
    cut_frames = {round(s["start"] * fps) for s in shots}
    fx_spans = [(s["start"], s["start"] + 0.3) for s in shots if {"flash", "pop", "punch", "glitch", "blur_in", "fade_in"} & set(s["fx"])]
    tr_zone = [(round(s["start"] * fps), tr_frames(s, fps) // 2 + 2) for s in shots if tr_frames(s, fps)]
    pts = cut_points(beats)
    evs = []
    for e in detect_events(st, fps):
        k = round(e["t"] * fps)
        if any(abs(k - c) <= 1 for c in cut_frames) or not (e["kind"].endswith("_on") or e["kind"] == "light_up"):
            continue
        if any(a0 <= e["t"] <= a1 for a0, a1 in fx_spans) or any(abs(k - c) <= w for c, w in tr_zone):
            continue  # our own flash/pop/crossfade ramp, not the footage
        p = nearest(pts, e["t"])
        ok = abs(p - e["t"]) <= 1.5 / fps
        evs.append({"t": e["t"], "kind": e["kind"], "beat": p, "on_beat": ok})
        if not ok and e["mag"] > 3:
            rep["warn"].append(f"{e['kind']} at {e['t']:.3f}s is off-beat (nearest beat {p:.3f})")
    rep["events"] = evs
    fades = effective_fades(shots)
    fade_spans = [(s["start"], s["start"] + (f["in"] or 0)) for s, f in zip(shots, fades)] + \
                 [(s["end"] - (f["out"] or 0), s["end"]) for s, f in zip(shots, fades)]
    dups = [k for k in range(1, len(d)) if d[k] < 0.05 and L[k] > 3]
    if dups:
        rep["warn"].append(f"{len(dups)} duplicated frames (stutter) at " + ", ".join(f"{k / fps:.2f}s" for k in dups[:10]))
    run_start = None
    for k in range(len(L) + 1):
        dark = k < len(L) and L[k] < 1.5
        if dark and run_start is None:
            run_start = k
        elif not dark and run_start is not None:
            t0, t1 = run_start / fps, k / fps
            if t1 - t0 > 0.25 and not any(a0 - 0.05 <= t0 and t1 <= a1 + 0.3 for a0, a1 in fade_spans):
                rep["warn"].append(f"near-black {t0:.2f}-{t1:.2f}s (intended? dark footage reads as a dropout)")
            run_start = None
    if review_dir:
        rep["review_images"] = review_images(Path(video), shots, fps, review_dir, W, H)
    rep["status"] = "FAIL" if rep["fail"] else "WARN" if rep["warn"] else "PASS"
    Path(video).with_suffix(".verify.json").write_text(dumps(rep, indent=1), encoding="utf-8")
    return rep
