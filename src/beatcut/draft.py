"""Heuristic first cut: beat-grid cut times + story-order clip allocation + light events landed on cuts.
It is a starting point for the agent/editor, not the final word — edit edl.json, then validate/render."""
from __future__ import annotations

import math

ON_KINDS = ("red_on", "amber_on", "white_on", "light_up")


def cut_times(bm: dict, total: float) -> list[float]:
    beats = [b["t"] for b in bm["beats"] if 0.2 < b["t"] < total - 0.2]
    acc, bars = set(bm["accents"]), {b["t"] for b in bm["beats"] if b.get("bar")}
    P, drop = bm["beat_period"], bm.get("drop")
    pre = P * (4 if P <= 0.6 else 2)     # ~2 s before the drop (a bar at 120 BPM)
    post = P * (2 if P <= 0.6 else 1)    # ~1 s after it
    cuts = [0.0]
    while True:
        last = cuts[-1]
        hot = drop is not None and last >= drop - 0.01
        tgt = post if hot else pre
        if drop is not None and last < drop <= last + 1.5 * tgt and drop - last >= 0.25:
            nxt = drop
        else:
            cands = [p for p in beats if last + 0.6 * tgt <= p <= last + 1.5 * tgt]
            if not cands:
                break
            nxt = min(cands, key=lambda p: abs(p - last - tgt) - 0.35 * tgt * ((p in acc) + 0.5 * (p in bars)))
        if total - nxt < 0.5 * tgt:
            break
        cuts.append(nxt)
    return cuts


def _runs(clip: dict, qmin: float = 0.45) -> list[tuple[float, float, float]]:
    """Contiguous usable stretches (start, end, mean q)."""
    out, cur = [], None
    for w in clip["windows"]:
        good = w["q"] >= qmin and not w["flags"]
        if good and cur and abs(cur[1] - w["start"]) < 1e-6:
            cur = [cur[0], w["end"], cur[2] + [w["q"]]]
        elif good:
            if cur:
                out.append(cur)
            cur = [w["start"], w["end"], [w["q"]]]
        elif cur:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return [(a, min(b, clip["duration"]), sum(q) / len(q)) for a, b, q in out]


def _value(clip: dict) -> float:
    runs = _runs(clip)
    secs = sum(b - a for a, b, _ in runs)
    best = max((q for *_, q in runs), default=0)
    return math.sqrt(secs) * (0.5 + best) + 0.5 * sum(e["kind"] in ON_KINDS for e in clip["events"])


def allocate(clips: list[dict], n_slots: int, min_span: float = 0.0) -> list[str]:
    """Story order: clips keep filming order; better/longer clips get more slots.
    Clips whose longest usable stretch can't fill ~half a typical shot (even in 0.5x slow-mo) are skipped."""
    usable = [c for c in clips if _runs(c) and max(b - a for a, b, _ in _runs(c)) >= 0.5 * min_span]
    if not usable:
        raise ValueError("no usable footage found (everything black/blurry/shaky?)")
    if n_slots <= len(usable):
        keep = sorted(sorted(usable, key=_value, reverse=True)[:n_slots], key=lambda c: c["id"])
        return [c["id"] for c in keep]
    counts = {c["id"]: 1 for c in usable}
    vals = {c["id"]: _value(c) for c in usable}
    cap = {c["id"]: max(1, int(sum(b - a for a, b, _ in _runs(c)) / 0.8)) for c in usable}
    for _ in range(n_slots - len(usable)):
        cid = max((k for k in counts if counts[k] < cap[k]), key=lambda k: vals[k] / (counts[k] + 1), default=None)
        if cid is None:
            cid = max(counts, key=lambda k: vals[k] / (counts[k] + 1))
        counts[cid] += 1
    return [c["id"] for c in usable for _ in range(counts[c["id"]])]


def _place(clip: dict, span: float, after: float, want_event: bool,
           avoid: tuple[float, float] | None = None) -> tuple[float, float, str]:
    """(in-point, speed, note) for a shot of `span` seconds, not before `after` (story order inside the clip).
    Never holds a frozen frame: if nothing fits, reuse an earlier stretch, else fill with gentle slow-mo."""
    runs = _runs(clip)
    for floor in (after, 0.0):
        best = None
        for a, b, q in runs:
            lo, hi = max(a, floor), b - span
            if hi < lo - 1e-6:
                continue
            cands = [(lo, q, ""), ((lo + hi) / 2, q, "")]
            for e in clip["events"]:
                if e["kind"] in ON_KINDS and lo <= e["t"] <= hi:
                    cands.append((e["t"], q + (0.6 if want_event else 0.3), f"{e['kind']} on the cut"))
            for t, score, why in cands:
                if avoid and t < avoid[1] and t + span > avoid[0]:  # never replay what the previous shot just showed
                    continue
                if best is None or score > best[1]:
                    best = (t, score, why if floor == after else (why or "reuses an earlier stretch"))
        if best:
            return best[0], 1.0, best[2]
    a, b, _ = max(runs, key=lambda r: r[1] - r[0])
    speed = max(0.5, math.floor((b - a) / span * 0.98 * 20) / 20)
    return a, speed, f"short clip — {speed}x slow-mo to fill"


def draft(bm: dict, clips: list[dict], total: float, fps: int = 30) -> list[dict]:
    cuts = cut_times(bm, total)
    starts, ends = cuts, cuts[1:] + [total]
    spans = sorted(e - s for s, e in zip(starts, ends))
    order = allocate(clips, len(starts), spans[len(spans) // 2])
    by_id = {c["id"]: c for c in clips}
    drop, acc = bm.get("drop"), set(bm["accents"])
    pos: dict[str, float] = {}
    shots = []
    for i, (st, en, cid) in enumerate(zip(starts, ends, order)):
        c, span = by_id[cid], en - st
        hot = drop is not None and st >= drop - 0.01
        is_drop = drop is not None and abs(st - drop) < 0.02
        prev = shots[-1] if shots and shots[-1]["clip"] == cid else None
        # same clip back to back: skip ahead so the cut reads as a jump, not an invisible splice
        after = pos.get(cid, 0.0) + (0.5 * span if prev else 0.0)
        avoid = (prev["in"], prev["in"] + (prev["end"] - prev["start"]) * prev["speed"]) if prev else None
        src_in, speed, why = _place(c, span, after, want_event=is_drop or st in acc, avoid=avoid)
        pos[cid] = src_in + span * speed
        on_cut = why.endswith("on the cut")
        fx = []
        if i == 0:
            fx.append("fade_in")
        if is_drop:
            fx += ["flash", "punch"]
        elif hot and on_cut:
            fx.append("punch")
        if i == len(starts) - 1 and span > 0.8:
            fx.append("fade_out")
        prev_clip = shots[-1]["clip"] if shots else None
        tr = {"type": "cut", "dur": 0.0}
        if i and not hot and not is_drop and not on_cut and prev_clip != cid and span >= 0.8 \
                and shots[-1]["end"] - shots[-1]["start"] >= 0.8 and "fade_in" not in fx:
            tr = {"type": "fade", "dur": 0.3}
        elif i and hot and not is_drop and not on_cut and i % 3 == 0 and span >= 0.6 and "fade_out" not in shots[-1]["fx"]:
            tr = {"type": "whip", "dur": 0.2}
        z = [1.04, 1.12] if hot else [1.0, 1.08]
        shots.append({"start": round(st, 3), "end": round(en, 3), "clip": cid, "in": round(src_in, 3), "speed": speed,
                      "zoom": z, "anchor": [0.5, 0.5], "fx": fx, "transition": tr, "gamma": None, "fade_out": None,
                      "note": f"{c['name']}" + (f" — {why}" if why else "") + (" — DROP" if is_drop else ""),
                      "file": c["name"], "_event": on_cut})
    _fit_handles(shots, by_id, fps)
    for s in shots:
        s.pop("_event")
    return shots


def _fit_handles(shots: list[dict], by_id: dict, fps: int) -> None:
    """A centred transition needs source frames before the incoming shot's in-point and after the outgoing shot's end.
    Nudge the in-point when that is harmless, otherwise fall back to a hard cut (still on the beat)."""
    from .edl import n_frames, tr_frames
    for i in range(1, len(shots)):
        s, p = shots[i], shots[i - 1]
        T = tr_frames(s, fps)
        if not T:
            continue
        head = T // 2 / fps * s["speed"]
        tail_ok = p["in"] + (n_frames(p, fps) + T // 2) / fps * p["speed"] <= by_id[p["clip"]]["duration"] - 0.02
        if s["in"] < head and not s["_event"] and s["in"] + head + (s["end"] - s["start"]) * s["speed"] <= by_id[s["clip"]]["duration"]:
            s["in"] = math.ceil(head * 1000) / 1000  # round up: rounding down would leave the handle 1 frame short
        if s["in"] < head or not tail_ok:
            s["transition"] = {"type": "cut", "dur": 0.0}
