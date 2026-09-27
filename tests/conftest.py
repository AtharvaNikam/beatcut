"""Synthetic media so the suite runs anywhere with ffmpeg: a 120 BPM click track with a drop at 6 s,
and three short 9:16 clips (one has a red 'tail light' switching on at 1.0 s)."""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

SR = 22050
BPM, DROP, DUR = 120, 6.0, 10.0


def make_song(path: Path) -> None:
    t = np.arange(int(SR * DUR)) / SR
    y = 0.02 * np.random.default_rng(0).standard_normal(t.size)
    for k in range(int(DUR * BPM / 60)):
        bt = 0.25 + k * 60 / BPM
        i = int(bt * SR)
        amp = 0.25 if bt < DROP else 0.9
        n = int(0.03 * SR)
        y[i:i + n] += amp * np.sin(2 * np.pi * 900 * np.arange(n) / SR) * np.exp(-np.arange(n) / (0.006 * SR))
    y[int(DROP * SR):] += 0.25 * np.sin(2 * np.pi * 55 * t[int(DROP * SR):])  # loud bass bed = the drop
    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def make_clip(path: Path, src: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", "3", "-r", "30",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("media")
    foot = d / "footage"
    foot.mkdir()
    make_song(d / "song.wav")
    make_clip(foot / "a_walk.mp4", "testsrc2=s=360x640:r=30")
    make_clip(foot / "b_tail.mp4", "color=c=0x202020:s=360x640:r=30,drawbox=x=90:y=300:w=180:h=40:color=red:t=fill:enable='gte(t,1)'")
    make_clip(foot / "c_bars.mp4", "smptebars=s=360x640:r=30")
    return {"dir": d, "footage": foot, "song": d / "song.wav"}
