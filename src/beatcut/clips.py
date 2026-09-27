"""Footage analysis: per-frame stats, light events (tail lights, indicators, headlights, scene light steps),
scored usable windows, and timestamped contact sheets an agent can look at."""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .media import MediaError, decode_frames, list_footage, probe_video

SW = 96  # analysis width (px); height follows the clip aspect


def frame_stats(a: np.ndarray) -> dict[str, np.ndarray]:
    """a: (n, h, w, 3) uint8 -> per-frame series."""
    f = a.astype(np.float32)
    R, G, B = f[..., 0], f[..., 1], f[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    lap = Y[:, 1:-1, 1:-1] * 4 - Y[:, :-2, 1:-1] - Y[:, 2:, 1:-1] - Y[:, 1:-1, :-2] - Y[:, 1:-1, 2:]
    return {
        "luma": Y.mean((1, 2)),
        "red": ((R > 140) & (R > G + 70)).mean((1, 2)) * 100,                      # tail / brake lights
        "amber": ((R > 170) & (G > 80) & (G < 190) & (B < 90) & (R > G + 40)).mean((1, 2)) * 100,  # indicators
        "white": (Y > 200).mean((1, 2)) * 100,                                      # DRL / headlights / screens
        "motion": np.r_[0, np.abs(np.diff(Y, axis=0)).mean((1, 2))],
        "sharp": lap.var((1, 2)),
    }


def detect_events(st: dict[str, np.ndarray], fps: float) -> list[dict]:
    """Hard light switches: value jumps within <= 2 frames. t = first frame of the new state."""
    ev: list[dict] = []
    rules = [("red", 1.0, 2.5), ("amber", 0.05, 3.0), ("white", 0.4, 2.5)]
    for key, abs_thr, ratio in rules:
        v = st[key]
        for k in range(2, len(v)):
            prev = v[k - 2:k]
            if v[k] - prev.max() >= abs_thr and v[k] >= ratio * max(prev.max(), abs_thr / 4):
                ev.append({"t": k / fps, "kind": f"{key}_on", "mag": float(v[k] - prev.max())})
            elif prev.min() - v[k] >= abs_thr and prev.min() >= ratio * max(v[k], abs_thr / 4):
                ev.append({"t": k / fps, "kind": f"{key}_off", "mag": float(prev.min() - v[k])})
    L, M = st["luma"], st["motion"]
    for k in range(1, len(L)):  # scene light step: big brightness change in one frame, camera not whipping
        d = L[k] - L[k - 1]
        if abs(d) >= max(5.0, 0.35 * min(L[k], L[k - 1]) + 3) and M[k] < 3 * (np.median(M) + 1) + abs(d):
            ev.append({"t": k / fps, "kind": "light_up" if d > 0 else "light_down", "mag": float(abs(d))})
    ev.sort(key=lambda e: e["t"])
    merged: list[dict] = []
    for e in ev:  # one event per 0.15 s per direction
        if merged and e["t"] - merged[-1]["t"] < 0.15 and e["kind"].split("_")[-1] == merged[-1]["kind"].split("_")[-1]:
            if e["mag"] > merged[-1]["mag"]:
                merged[-1] = e | {"t": merged[-1]["t"]}
            continue
        merged.append(e)
    return [{"t": round(e["t"], 3), "kind": e["kind"], "mag": round(e["mag"], 2)} for e in merged]


def score_windows(st: dict[str, np.ndarray], fps: float, win: float = 0.5) -> list[dict]:
    """Per-window quality 0..1 from exposure, sharpness and stability. Dark shots with lights still score."""
    n = len(st["luma"])
    step = max(1, int(round(win * fps)))
    sharp_ref = np.percentile(st["sharp"], 90) + 1e-6
    mot_ref = np.median(st["motion"]) + 1.0
    out = []
    for a in range(0, n, step):
        s = slice(a, min(n, a + step))
        L, lights = float(st["luma"][s].mean()), float((st["red"][s] + st["amber"][s] + st["white"][s]).mean())
        expo = 1.0 if 18 <= L <= 190 else (L / 18 if L < 18 else max(0.0, 1 - (L - 190) / 40))
        expo = max(expo, min(0.8, lights / 2))  # a lit tail bar in the dark is a usable shot
        sharp = min(1.0, float(st["sharp"][s].mean()) / sharp_ref)
        shake = float(st["motion"][s].mean()) / mot_ref
        stab = 1.0 if shake < 2 else max(0.0, 1 - (shake - 2) / 4)
        q = 0.45 * expo + 0.35 * sharp + 0.2 * stab
        flags = [f for f, c in (("black", L < 3 and lights < 0.05), ("blurry", sharp < 0.25), ("shaky", stab < 0.4)) if c]
        out.append({"start": round(a / fps, 2), "end": round(min(n, a + step) / fps, 2), "q": round(q, 2),
                    "luma": round(L, 1), "lights": round(lights, 2), "flags": flags})
    return out


def contact_sheet(a: np.ndarray, fps: float, out: Path, step_s: float = 0.25, tile_w: int = 150, cols: int = 10) -> None:
    idx = np.arange(0, len(a), max(1, fps * step_s)).astype(int)
    th = int(round(a.shape[1] * tile_w / a.shape[2]))
    rows = (len(idx) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tile_w + 3), rows * (th + 3)), "black")
    for n, k in enumerate(idx):
        im = Image.fromarray(a[k]).resize((tile_w, th))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 44, 12], fill="black")
        d.text((2, 1), f"{k / fps:.2f}", fill="yellow")
        sheet.paste(im, ((n % cols) * (tile_w + 3), (n // cols) * (th + 3)))
    sheet.save(out, quality=82)


def analyze_clip(cid: str, path: Path, sheets: Path) -> dict:
    info = probe_video(path)
    h = int(round(SW * info.height / info.width / 2)) * 2
    a = decode_frames(path, SW, h, fps=round(info.fps, 3))
    if len(a) < 3:
        raise MediaError("fewer than 3 decodable frames")
    st = frame_stats(a)
    fps = info.fps
    # sheets from a slightly bigger decode so tiles are readable
    bh = int(round(300 * info.height / info.width / 2)) * 2
    big = decode_frames(path, 300, bh, fps=4)
    if not len(big):  # clip shorter than one 4 fps tick
        big = decode_frames(path, 300, bh)[:1]
    contact_sheet(big, 4, sheets / f"{cid}.jpg")
    ev = detect_events(st, fps)
    wins = score_windows(st, fps)
    return {
        "id": cid, "file": str(path), "name": path.name, "width": info.width, "height": info.height,
        "fps": round(fps, 3), "duration": round(len(a) / fps, 3), "sheet": str(sheets / f"{cid}.jpg"),
        "mean_luma": round(float(st["luma"].mean()), 1), "events": ev, "windows": wins,
        "series": {k: [round(float(x), 2) for x in v] for k, v in st.items()},
    }


def analyze_footage(folder: str, sheets: Path, jobs: int | None = None, exclude: list[str] | None = None) -> list[dict]:
    files = list_footage(folder, exclude)
    if not files:
        raise FileNotFoundError(f"no video files in {folder}")
    sheets.mkdir(parents=True, exist_ok=True)
    ids = [f"c{i:02d}" for i in range(1, len(files) + 1)]

    def one(cid: str, path: Path) -> dict | None:
        try:
            return analyze_clip(cid, path, sheets)
        except (MediaError, ValueError, OSError) as e:  # one bad file must not sink the whole job
            print(f"skipped {path.name}: {e} (add --exclude \"{path.name}\" to silence)", file=sys.stderr)
            return None
    with ThreadPoolExecutor(max_workers=jobs or max(1, (os.cpu_count() or 2) // 2)) as ex:
        out = [c for c in ex.map(lambda p: one(*p), zip(ids, files)) if c]
    if not out:
        raise FileNotFoundError(f"no readable video files in {folder}")
    return out


def luma_between(clip: dict, a: float, b: float) -> float:
    s = clip["series"]["luma"]
    i, j = int(a * clip["fps"]), max(int(a * clip["fps"]) + 1, int(b * clip["fps"]))
    seg = s[i:j] or s[-1:]
    return sum(seg) / len(seg)


def clip_brief(c: dict) -> str:
    good = [w for w in c["windows"] if w["q"] >= 0.55 and not w["flags"]]
    bad = [w for w in c["windows"] if w["flags"]]
    ev = ", ".join(f"{e['kind']}@{e['t']:.2f}" for e in c["events"][:24]) or "none"
    best = sorted(good, key=lambda w: -w["q"])[:5]
    best_s = ", ".join("%.1f-%.1f (q%.2f)" % (w["start"], w["end"], w["q"]) for w in best) or "none"
    bad_s = ", ".join("%.1f-%.1f %s" % (w["start"], w["end"], "/".join(w["flags"])) for w in bad) or "nothing flagged"
    return (f"### {c['id']} — {c['name']}\n"
            f"- {c['duration']:.2f}s @ {c['fps']:g} fps, {c['width']}x{c['height']}, mean luma {c['mean_luma']}\n"
            f"- light events (sync these to beats): {ev}\n"
            f"- best windows: {best_s}\n"
            f"- avoid: {bad_s}\n"
            f"- contact sheet: {c['sheet']}\n")
