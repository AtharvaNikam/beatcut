"""ffmpeg / ffprobe helpers. Every external process call in beatcut goes through here."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".3gp"}


class MediaError(RuntimeError):
    """ffmpeg/ffprobe missing or failed."""


def require_ffmpeg() -> None:
    for b in ("ffmpeg", "ffprobe"):
        if not shutil.which(b):
            raise MediaError(f"{b} not found on PATH. Install ffmpeg (macOS: brew install ffmpeg, Ubuntu: apt install ffmpeg).")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, **kw)
    if p.returncode:
        err = p.stderr.decode(errors="replace") if isinstance(p.stderr, bytes) else p.stderr
        raise MediaError(f"{cmd[0]} failed ({p.returncode}): {err.strip()[-1500:]}")
    return p


def _rate(s: str | None) -> float:
    try:
        n, d = s.split("/")
        return float(n) / float(d) if float(d) else 0.0
    except Exception:
        return 0.0


@dataclass(frozen=True)
class VideoInfo:
    path: str
    width: int      # display width (rotation applied)
    height: int     # display height
    duration: float
    fps: float
    has_audio: bool


def probe_video(path: str | Path) -> VideoInfo:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height,avg_frame_rate,r_frame_rate:stream_side_data=rotation:format=duration",
               "-of", "json", str(path)], text=True).stdout
    j = json.loads(out)
    streams = j.get("streams", [])
    vs = [s for s in streams if s.get("codec_type") == "video"]
    if not vs:
        raise MediaError(f"no video stream in {path}")
    st = vs[0]
    w, h = int(st["width"]), int(st["height"])
    rot = next((abs(int(float(sd["rotation"]))) % 360 for sd in st.get("side_data_list", []) if "rotation" in sd), 0)
    if rot in (90, 270):
        w, h = h, w
    avg, r = _rate(st.get("avg_frame_rate")), _rate(st.get("r_frame_rate"))
    # phone clips are VFR: dropped frames pull avg below the real capture grid (e.g. 26.07 vs 30) — use the grid
    fps = r if avg and avg < r <= 61 else avg or r or 30.0
    dur = float(j.get("format", {}).get("duration") or 0.0)
    return VideoInfo(str(path), w, h, dur, fps, any(s.get("codec_type") == "audio" for s in streams))


def probe_duration(path: str | Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], text=True).stdout
    try:
        return float(out.strip())
    except ValueError as e:
        raise MediaError(f"cannot read duration of {path}") from e


def decode_frames(path: str | Path, w: int, h: int, fps: float | None = None, pix: str = "rgb24",
                  ss: float | None = None, t: float | None = None, vf: str | None = None) -> np.ndarray:
    """Decode to an (n, h, w, c) uint8 array. fps forces a constant frame rate so index <-> time is exact."""
    chain = [f for f in (vf, f"fps={fps}" if fps else None, f"scale={w}:{h}:flags=area") if f]
    cmd = ["ffmpeg", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-i", str(path), "-an", "-vf", ",".join(chain)]
    if not fps:  # no CFR padding: a select filter must return exactly the frames it picked
        cmd += ["-fps_mode", "passthrough"]
    cmd += ["-f", "rawvideo", "-pix_fmt", pix, "-"]
    raw = run(cmd).stdout
    c = {"rgb24": 3, "gray": 1}[pix]
    a = np.frombuffer(raw, np.uint8)
    n = a.size // (w * h * c)
    return a[: n * w * h * c].reshape(n, h, w, c) if c > 1 else a[: n * w * h].reshape(n, h, w)


def decode_audio(path: str | Path, sr: int = 22050, ss: float = 0.0, t: float | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{ss:.3f}"]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    y = np.frombuffer(run(cmd).stdout, np.float32)
    if y.size == 0:
        raise MediaError(f"no audio decoded from {path}")
    return y


def has_filters(*names: str) -> dict[str, bool]:
    out = run(["ffmpeg", "-hide_banner", "-filters"], text=True).stdout
    have = {line.split()[1] for line in out.splitlines() if len(line.split()) > 2 and line.startswith(" ")}
    return {n: n in have for n in names}


def has_encoder(name: str) -> bool:
    return f" {name} " in run(["ffmpeg", "-hide_banner", "-encoders"], text=True).stdout


def list_footage(folder: str | Path, exclude: list[str] | None = None) -> list[Path]:
    """Video files in folder (not recursive), sorted by name — phone filenames sort chronologically.
    exclude: glob patterns matched against the file name (e.g. finished edits living in the same folder)."""
    p = Path(folder)
    if not p.is_dir():
        raise MediaError(f"footage folder not found: {folder}")
    ex = exclude or []
    return sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTS
                  and not f.name.startswith(".") and not any(f.match(g) for g in ex))
