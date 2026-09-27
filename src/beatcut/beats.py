"""Song analysis: onsets, tempo, beat grid (snapped to real transients), bars, energy, drop."""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from .media import decode_audio

SR, N_FFT, HOP = 22050, 2048, 256


def _norm(x: np.ndarray) -> np.ndarray:
    x = x - np.median(x)
    return x / (np.percentile(x, 99) + 1e-9)


def _spectral_flux(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pad = np.pad(y, (N_FFT // 2, N_FFT // 2))
    win = np.hanning(N_FFT).astype(np.float32)
    fr = np.lib.stride_tricks.sliding_window_view(pad, N_FFT)[::HOP] * win
    # scale by window sum (as scipy.signal.stft) so log1p(100*|X|) stays in its near-linear range -> crisp transients
    mag = np.log1p(100 * np.abs(np.fft.rfft(fr, axis=1)) / win.sum()).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    d = np.maximum(0, np.diff(mag, axis=0))
    flux = np.r_[0, d.sum(1)]
    low = np.r_[0, d[:, freqs < 150].sum(1)]
    t = np.arange(len(flux)) * HOP / sr
    return t, _norm(flux), _norm(low)


def _attacks(y: np.ndarray, times: list[float], sr: int = SR) -> list[float]:
    """Refine window-centred STFT onsets (~20 ms early) to the attack: first point where a 3 ms envelope is
    halfway from the pre-onset floor to the local peak. Half-rise stays on the leading edge of dense mixes."""
    k = max(1, int(0.003 * sr))
    env = np.convolve(np.abs(y), np.ones(k) / k, mode="same")
    out = []
    for t0 in times:
        i0 = int(t0 * sr)
        pre = env[max(0, i0 - int(0.04 * sr)):max(1, i0)]
        seg_a, seg_b = max(0, i0 - int(0.02 * sr)), min(len(env), i0 + int(0.045 * sr))
        if seg_b - seg_a < 4 or pre.size == 0:
            out.append(t0)
            continue
        floor, peak = float(pre.min()), float(env[seg_a:seg_b].max())
        above = np.nonzero(env[seg_a:seg_b] >= floor + 0.5 * (peak - floor))[0]
        out.append((seg_a + int(above[0])) / sr if above.size else t0)
    return out


def _tempo(env: np.ndarray, fr: float, bpm_range: tuple[float, float]) -> float:
    e = np.maximum(env, 0) - np.maximum(env, 0).mean()
    ac = np.fft.irfft(np.abs(np.fft.rfft(e, 2 * len(e))) ** 2)[: len(e)]
    lo, hi = int(fr * 60 / bpm_range[1]), int(np.ceil(fr * 60 / bpm_range[0]))
    k = lo + int(np.argmax(ac[lo:hi + 1]))
    if 1 <= k < len(ac) - 1:  # parabolic refinement
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        k = k + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
    return float(k / fr)  # beat period (s)


def _phase(env: np.ndarray, fr: float, period: float, dur: float) -> float:
    best, best_s = 0.0, -1e9
    for ph in np.arange(0, period, 1 / fr):
        idx = np.round((ph + np.arange(0, dur - ph, period)) * fr).astype(int)
        s = env[idx[idx < len(env)]].sum()
        if s > best_s:
            best, best_s = ph, s
    return best


def analyze_song(path: str, start: float = 0.0, length: float | None = None,
                 bpm: float | None = None, bpm_range: tuple[float, float] = (80, 160)) -> dict:
    y = decode_audio(path, SR, start, length or None)
    dur = len(y) / SR
    t, flux, low = _spectral_flux(y, SR)
    fr = SR / HOP

    pk, _ = find_peaks(flux, height=0.3, distance=max(1, int(0.1 * fr)), prominence=0.15)
    att = _attacks(y, [float(t[i]) for i in pk])
    onsets = [{"t": round(a, 3), "s": round(float(flux[i]), 2)} for a, i in zip(att, pk)]
    kp, _ = find_peaks(low, height=0.4, distance=max(1, int(0.2 * fr)))
    kicks = [round(float(t[i]), 3) for i in kp]

    period = float(60 / bpm) if bpm else _tempo(flux, fr, bpm_range)
    ph = _phase(np.maximum(flux, 0) + 2 * np.maximum(low, 0), fr, period, dur)  # kicks anchor the grid, not off-beat hats
    grid = np.arange(ph, dur, period)
    on_t = np.array([o["t"] for o in onsets]) if onsets else np.array([])
    beats = []
    on_s = np.array([o["s"] for o in onsets]) if onsets else np.array([])
    for g in grid:
        near = np.nonzero(np.abs(on_t - g) <= 0.06)[0] if on_t.size else []
        snap = len(near) > 0
        bt = float(on_t[near[np.argmax(on_s[near])]]) if snap else float(g)  # strongest transient near the grid beat
        i = int(round(bt * fr))
        s = float(flux[max(0, i - 2): i + 3].max()) if i < len(flux) else 0.0
        beats.append({"t": round(bt, 3), "s": round(s, 2), "onset": bool(snap)})
    w = int(0.25 * SR)
    rms = np.array([float(np.sqrt(np.mean(y[i:i + w] ** 2))) for i in range(0, len(y) - w + 1, w)] or [0.0])
    energy = [{"t": round(k * 0.25, 2), "rms": round(v, 4)} for k, v in enumerate(rms)]

    drop, ratio = None, 1.0
    W = 16  # 4 s of 0.25 s windows
    for b in beats:
        k = int(b["t"] / 0.25)
        if k < 8 or k > len(rms) - 8:
            continue
        r = rms[k:k + W].mean() / (rms[max(0, k - W):k].mean() + 1e-9)
        if r > ratio and rms[k:k + W].mean() >= np.percentile(rms, 50):
            drop, ratio = b["t"], float(r)
    if ratio < 1.35:
        drop = None
    if drop is not None and on_t.size:  # land exactly on the strongest transient near the chosen beat
        near = [o for o in onsets if abs(o["t"] - drop) <= 0.15]
        if near:
            drop = max(near, key=lambda o: o["s"])["t"]

    # bars (4/4): strongest beat phase, chosen separately for the build and the drop — the drop often lands mid-bar
    k0 = next((k for k, b in enumerate(beats) if drop is not None and b["t"] >= drop - 0.01), len(beats))
    for seg in (beats[:k0], beats[k0:]):
        if len(seg) >= 4:
            off = int(np.argmax([np.mean([b["s"] for b in seg[o::4]]) for o in range(4)]))
            for k, b in enumerate(seg):
                b["bar"] = (k - off) % 4 == 0
    strengths = np.array([b["s"] for b in beats]) if beats else np.array([0.0])
    thr = float(np.percentile(strengths, 70))
    accents = [b["t"] for b in beats if b["s"] >= thr and b["s"] > 0.3]
    sections = ([{"name": "build", "start": 0.0, "end": drop}, {"name": "drop", "start": drop, "end": round(dur, 3)}]
                if drop else [{"name": "main", "start": 0.0, "end": round(dur, 3)}])
    return {"song": str(path), "start": start, "duration": round(dur, 3), "bpm": round(60 / period, 2),
            "beat_period": round(period, 4), "beats": beats, "onsets": onsets, "kicks": kicks, "accents": accents,
            "drop": drop, "drop_ratio": round(ratio, 2), "sections": sections, "energy": energy}


def cut_points(bm: dict, min_strength: float = 0.3) -> list[float]:
    """Times a cut may legally land on: every beat plus every real onset."""
    pts = {b["t"] for b in bm["beats"]} | {o["t"] for o in bm["onsets"] if o["s"] >= min_strength}
    return sorted(pts)


def nearest(points: list[float], t: float) -> float:
    return min(points, key=lambda p: abs(p - t)) if points else t


def summary(bm: dict) -> str:
    lines = [f"Song section: {bm['duration']:.2f}s from {bm['start']:.2f}s of {bm['song']}",
             f"Tempo: {bm['bpm']} BPM (beat {bm['beat_period']:.3f}s, bar {4 * bm['beat_period']:.2f}s)"]
    if bm["drop"]:
        lines.append(f"DROP at {bm['drop']:.2f}s (energy x{bm['drop_ratio']}) — the hardest-hitting visual goes here")
    else:
        lines.append("No clear drop detected — energy is fairly even")
    bars = [b["t"] for b in bm["beats"] if b.get("bar")]
    lines.append("Bar starts (downbeats): " + ", ".join(f"{x:.2f}" for x in bars))
    lines.append("Strong accents (best hard-cut / light-hit points): " + ", ".join(f"{x:.2f}" for x in bm["accents"]))
    lines.append("All beats: " + ", ".join(f"{b['t']:.2f}" for b in bm["beats"]))
    return "\n".join(lines)
