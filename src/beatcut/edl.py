"""Edit decision list: load/normalise, validate against beats + footage, report where light events land."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .project import dumps
from .beats import cut_points, nearest

FX = {"fade_in", "fade_out", "flash", "punch", "pop", "glitch", "blur_in"}
FX_ALIAS = {"fade_from_black": "fade_in", "fade_to_black": "fade_out", "flash_in": "flash", "shake": "punch",
            "exposure_pop": "pop", "rgb_split": "glitch", "none": None}
TRANSITIONS = {"cut", "fade", "dip", "whip"}
TR_ALIAS = {"dissolve": "fade", "fadeblack": "dip", "hblur": "whip"}
ON_BEAT = 0.035  # s — about one frame at 30 fps
KNOWN_KEYS = {"start", "end", "clip", "in", "speed", "zoom", "anchor", "fx", "transition", "gamma", "fade_out", "note",
              "file", "tlStart", "tlEnd", "srcIn", "zoomStart", "zoomEnd", "zoomCenter", "tin", "fadeOut", "why"}


class EDLError(ValueError):
    pass


def _anchor(v) -> list[float]:
    if isinstance(v, str):  # legacy "48%,52%"
        v = [float(x.strip().rstrip("%")) / 100 for x in v.split(",")]
    return [min(max(float(v[0]), 0.0), 1.0), min(max(float(v[1]), 0.0), 1.0)]


def normalize(raw: dict, total: float) -> list[dict]:
    """Accepts the documented format (start/in/zoom/anchor/transition) and legacy keys (tlStart/srcIn/zoomStart/tin)."""
    shots_in = raw.get("shots") if isinstance(raw, dict) else None
    if not shots_in:
        raise EDLError("EDL has no shots")
    shots = []
    for i, s in enumerate(shots_in):
        if not isinstance(s, dict):
            raise EDLError(f"shot {i}: must be an object like {{\"start\": 0, \"clip\": \"c01\", \"in\": 0}}")
        try:
            start = float(s.get("start", s.get("tlStart")))
            clip = str(s["clip"])
            src_in = float(s.get("in", s.get("srcIn", 0.0)))
        except (TypeError, KeyError, ValueError) as e:
            raise EDLError(f"shot {i}: needs start, clip, in ({e})") from e
        try:
            zoom = s.get("zoom") or [s.get("zoomStart", 1.0), s.get("zoomEnd", s.get("zoomStart", 1.0))]
            if isinstance(zoom, (int, float)):
                zoom = [zoom, zoom]
            fx = []
            for f in s.get("fx") or []:
                f = FX_ALIAS.get(f, f)
                if f:
                    fx.append(str(f) if isinstance(f, str) else f"{f!r}")
            tr = s.get("transition", s.get("tin")) or {"type": "cut"}
            if isinstance(tr, str):
                tr = {"type": tr}
            ttype = str(tr.get("type", "cut"))
            tr = {"type": TR_ALIAS.get(ttype, ttype), "dur": float(tr.get("dur", 0.3) if tr.get("dur") is not None else 0.3)}
            fo = s.get("fade_out", s.get("fadeOut"))
            shots.append({"start": start, "end": s.get("end", s.get("tlEnd")), "clip": clip, "in": src_in,
                          "speed": float(s.get("speed", 1.0)), "zoom": [float(zoom[0]), float(zoom[1])],
                          "anchor": _anchor(s.get("anchor", s.get("zoomCenter", [0.5, 0.5]))), "fx": fx, "transition": tr,
                          "gamma": float(s["gamma"]) if s.get("gamma") else None,
                          "fade_out": None if fo is None else float(fo),
                          "note": str(s.get("note", s.get("why", ""))), "file": s.get("file"),
                          "unknown": sorted(set(s) - KNOWN_KEYS)})
        except EDLError:
            raise
        except (TypeError, KeyError, ValueError, IndexError, AttributeError) as e:
            raise EDLError(f"shot {i}: bad value ({type(e).__name__}: {e}) — see references/edl-format.md") from e
    for i, s in enumerate(shots):
        s["end"] = shots[i + 1]["start"] if i + 1 < len(shots) else float(s["end"] or total)
    return shots


def load(path: str | Path, total: float) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        raise EDLError(f"EDL not found: {p} — run `beatcut draft` or write one (see references/edl-format.md)")
    try:
        return normalize(json.loads(p.read_text()), total)
    except json.JSONDecodeError as e:
        raise EDLError(f"{p} is not valid JSON: {e}") from e


def save(shots: list[dict], path: str | Path, meta: dict | None = None) -> None:
    out = []
    for s in shots:
        d = {k: s[k] for k in ("start", "end", "clip", "in", "speed", "zoom", "anchor", "fx", "transition", "note")}
        d["start"], d["end"], d["in"] = round(d["start"], 3), round(d["end"], 3), round(d["in"], 3)
        d["anchor"] = [round(a, 3) for a in d["anchor"]]
        if s.get("gamma"):
            d["gamma"] = s["gamma"]
        if s.get("fade_out"):
            d["fade_out"] = s["fade_out"]
        if s.get("file"):
            d["file"] = s["file"]
        out.append(d)
    Path(path).write_text(dumps({"version": 1, **(meta or {}), "shots": out}, indent=1))


# ---- frame math shared by validate / render / verify -------------------------------------------
def tr_frames(shot: dict, fps: int) -> int:
    """Frames of the transition INTO this shot, centred on the cut. 0 for cut and dip (dip is two in-shot fades)."""
    t = shot["transition"]
    return 0 if t["type"] in ("cut", "dip") else 2 * max(1, int(t["dur"] * fps / 2 + 0.5))  # halves round up


def handles(shots: list[dict], i: int, fps: int) -> tuple[int, int]:
    head = tr_frames(shots[i], fps) // 2 if i else 0
    tail = tr_frames(shots[i + 1], fps) // 2 if i + 1 < len(shots) else 0
    return head, tail


def n_frames(shot: dict, fps: int) -> int:
    return round(shot["end"] * fps) - round(shot["start"] * fps)


def first_src_frame_time(t: float, src_fps: float) -> float:
    """ffmpeg -ss lands on the first source frame at or after t."""
    return math.ceil(t * src_fps - 1e-6) / src_fps


def events_in_shot(shot: dict, clip: dict, fps: int) -> list[dict]:
    a = first_src_frame_time(shot["in"], clip["fps"])
    b = shot["in"] + n_frames(shot, fps) / fps * shot["speed"]
    t0 = round(shot["start"] * fps) / fps  # the shot really starts on this output frame
    return [e | {"tl": t0 + (e["t"] - a) / shot["speed"]} for e in clip["events"] if a <= e["t"] < b]


def validate(shots: list[dict], beats: dict, clips: list[dict], fps: int, total: float) -> dict:
    by_id = {c["id"]: c for c in clips}
    pts = cut_points(beats)
    errors, warnings, landing = [], [], []
    used: dict[str, list[tuple[float, float, int]]] = {}
    if abs(shots[0]["start"]) > 1e-6:
        errors.append("shot 0 must start at 0")
    if abs(shots[-1]["end"] - total) > 0.02:
        errors.append(f"last shot ends at {shots[-1]['end']:.3f}s but the montage is {total:.3f}s")
    for i, s in enumerate(shots):
        tag = f"shot {i} ({s['clip']} @{s['start']:.2f})"
        c = by_id.get(s["clip"])
        if c is None:
            errors.append(f"{tag}: unknown clip id {s['clip']} (known: {', '.join(by_id)})")
            continue
        if s.get("file") and c.get("name") and c["name"] != s["file"]:
            now = next((k for k, v in by_id.items() if v.get("name") == s["file"]), None)
            errors.append(f"{tag}: {s['clip']} is now {c['name']} but this shot was cut from {s['file']} (footage changed "
                          f"since the draft) — " + (f"use clip {now}" if now else "that file is gone"))
            continue
        if s.get("unknown"):
            warnings.append(f"{tag}: unknown key(s) {s['unknown']} ignored (typo?)")
        n = n_frames(s, fps)
        if n < 2:
            errors.append(f"{tag}: shorter than 2 frames (starts must increase)")
            continue
        if not 0.25 <= s["speed"] <= 4:
            errors.append(f"{tag}: speed {s['speed']} outside 0.25-4")
        if not all(1.0 <= z <= 3.0 for z in s["zoom"]):
            errors.append(f"{tag}: zoom {s['zoom']} outside 1.0-3.0")
        if s["gamma"] is not None and not 0.5 <= s["gamma"] <= 2.0:
            errors.append(f"{tag}: gamma {s['gamma']} outside 0.5-2.0")
        if s["fade_out"] is not None and not 0 < s["fade_out"] <= s["end"] - s["start"]:
            errors.append(f"{tag}: fade_out {s['fade_out']} must be > 0 and no longer than the shot")
        for f in s["fx"]:
            if f not in FX:
                errors.append(f"{tag}: unknown fx {f!r} (use {sorted(FX)})")
        if s["transition"]["type"] not in TRANSITIONS:
            errors.append(f"{tag}: unknown transition {s['transition']['type']!r} (use {sorted(TRANSITIONS)})")
            continue
        head, tail = handles(shots, i, fps)
        if head and {"fade_in", "flash"} & set(s["fx"]):
            errors.append(f"{tag}: fade_in/flash need a hard cut or dip into the shot, not {s['transition']['type']}")
        if tail and "fade_out" in s["fx"]:
            errors.append(f"{tag}: fade_out needs the next shot to start with a cut or dip")
        if head and (head > n // 2 or (i and head > n_frames(shots[i - 1], fps) // 2)):
            errors.append(f"{tag}: {s['transition']['type']} of {s['transition']['dur']}s is longer than half a neighbouring shot")
        a = s["in"] - head / fps * s["speed"]
        b = s["in"] + (n + tail) / fps * s["speed"]
        if a < -1e-6:
            errors.append(f"{tag}: in={s['in']:.3f} leaves no room for the {head}-frame transition handle (needs in >= {head / fps * s['speed']:.3f})")
        if b > c["duration"] + 0.05:
            (errors if b > c["duration"] + 0.5 else warnings).append(
                f"{tag}: needs source up to {b:.2f}s but {s['clip']} is {c['duration']:.2f}s" + (" (last frame will be held)" if b <= c["duration"] + 0.5 else ""))
        if i and abs(nearest(pts, s["start"]) - s["start"]) > ON_BEAT:
            warnings.append(f"{tag}: cut is off-beat — nearest beat/onset is {nearest(pts, s['start']):.3f}")
        for (ua, ub, j) in used.get(s["clip"], []):
            if min(ub, b) - max(ua, a) > 0.3:
                warnings.append(f"{tag}: reuses {s['clip']} {max(ua, a):.2f}-{min(ub, b):.2f}s already used in shot {j}")
        used.setdefault(s["clip"], []).append((a, b, i))
        bad = [w for w in c["windows"] if w["flags"] and w["start"] < b and w["end"] > s["in"]]
        if bad and sum(min(w["end"], b) - max(w["start"], s["in"]) for w in bad) > 0.5 * (b - s["in"]):
            warnings.append(f"{tag}: mostly in footage flagged {sorted({f for w in bad for f in w['flags']})}")
        for e in events_in_shot(s, c, fps):
            p = nearest(pts, e["tl"])
            ok = abs(p - e["tl"]) <= 1.5 / fps
            fix = s["in"] + (e["tl"] - p) * s["speed"]
            landing.append({"shot": i, "kind": e["kind"], "src": e["t"], "tl": round(e["tl"], 3), "beat": p, "on_beat": ok,
                            "fix_in": None if ok or fix < 0 else round(fix, 3)})
            if not ok and (e["kind"].endswith("_on") or e["kind"] == "light_up"):
                hint = f"set in={fix:.3f} to land it" if fix >= 0 else "move the cut (in would have to be < 0)"
                warnings.append(f"{tag}: {e['kind']} lands at {e['tl']:.3f}, off-beat (nearest {p:.3f}) — {hint}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "events": landing}
