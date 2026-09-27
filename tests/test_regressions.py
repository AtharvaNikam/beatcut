"""One test per bug found by the hardening pass (cold-start agent, edge-case matrix, code review)."""
import json
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from beatcut import render as R
from beatcut.beats import analyze_song
from beatcut.cli import GUIDE, main
from beatcut.draft import draft
from beatcut.edl import EDLError, normalize, tr_frames, validate
from beatcut.media import decode_frames, probe_video
from beatcut.verify import review_images, verify

BEATS = {"beats": [{"t": float(t), "s": 1.0} for t in range(5)], "onsets": []}


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def clip(cid="c01", dur=10.0, events=(), name=None):
    return {"id": cid, "fps": 30, "duration": dur, "windows": [], "events": list(events), "name": name or f"{cid}.mp4"}


@pytest.fixture(scope="module")
def proj(media, tmp_path_factory):
    root = tmp_path_factory.mktemp("rp") / "p"
    assert main(["init", str(root), "--footage", str(media["footage"]), "--song", str(media["song"]), "--length", "8"]) == 0
    assert main(["analyze", str(root)]) == 0
    assert main(["draft", str(root)]) == 0
    return root


# --- media / verify -----------------------------------------------------------------------------------------
def rgb_video(path: Path, size="90x160") -> None:
    ff("-f", "lavfi", "-i", f"color=red:s={size}:r=30:d=1", "-f", "lavfi", "-i", f"color=lime:s={size}:r=30:d=1",
       "-f", "lavfi", "-i", f"color=blue:s={size}:r=30:d=1", "-filter_complex", "[0][1][2]concat=n=3:v=1[v]",
       "-map", "[v]", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-qp", "0", str(path))


def three_shots():
    return [{"start": float(k), "end": float(k + 1), "clip": "c01", "in": 0.0, "speed": 1.0, "fx": [],
             "transition": {"type": "cut", "dur": 0}} for k in range(3)]


def test_review_images_show_each_shot(tmp_path):
    v = tmp_path / "rgb.mp4"
    rgb_video(v)
    review_images(v, three_shots(), 30, tmp_path / "rev", 90, 160)
    for k, want in enumerate(([250, 0, 0], [0, 250, 0], [0, 0, 250])):
        im = np.asarray(Image.open(tmp_path / "rev" / f"shot{k:02d}.jpg"), np.float32)
        mid = im[30 + 100, 1 * (240 + 4) + 120]  # centre of the mid panel
        assert np.abs(mid - want).max() < 40, (k, mid)


def test_review_stills_keep_the_project_aspect(tmp_path):
    v = tmp_path / "wide.mp4"
    rgb_video(v, "192x108")
    review_images(v, three_shots(), 30, tmp_path / "rev", 1920, 1080)
    assert Image.open(tmp_path / "rev" / "shot00.jpg").size == (3 * 244, 136 + 30)


def test_vfr_clip_reports_its_real_frame_grid(tmp_path):
    v = tmp_path / "vfr.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=4", "-vf", "select='not(eq(mod(n\\,7)\\,3))'", "-fps_mode", "vfr",
       "-pix_fmt", "yuv420p", str(v))
    assert abs(probe_video(v).fps - 30) < 0.01


# --- render ---------------------------------------------------------------------------------------------------
def gray_ramp_clip(path: Path, n=60) -> None:
    """Frame k is solid gray level 4k (lossless) — the frame index can be read back from the pixels."""
    frames = np.stack([np.full((64, 36, 3), 4 * k, np.uint8) for k in range(n)])
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "36x64", "-r", "30",
                          "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(path)], stdin=subprocess.PIPE)
    p.communicate(frames.tobytes())


def _cfg():
    return {"W": 36, "H": 64, "fps": 30, "grade": "none", "preview": True, "drop": None}


def _shot(**kw):
    s = {"start": 1.0, "end": 2.0, "clip": "c01", "in": 6 / 30, "speed": 1.0, "zoom": [1.0, 1.0], "anchor": [0.5, 0.5],
         "fx": [], "transition": {"type": "fade", "dur": 0.3}, "gamma": None}
    return s | kw


def test_transition_handle_starts_on_the_planned_source_frame(tmp_path):
    src = tmp_path / "ramp.mp4"
    gray_ramp_clip(src)
    c = {"file": str(src), "fps": 30.0, "width": 36, "height": 64, "series": {"luma": [50.0] * 60}}
    out = tmp_path / "part.mp4"
    R.render_shot(1, _shot(), c, {"in": None, "out": None}, 4, 0, _cfg(), out)
    g = decode_frames(out, 36, 64, pix="gray")[:, 32, 18].astype(int)
    assert abs(g[0] - 4 * 2) <= 3 and abs(g[4] - 4 * 6) <= 3  # handle = source frames 2..5, the cut shows frame 6


def test_render_shot_does_not_hang_on_a_chatty_decoder(tmp_path, monkeypatch):
    src = tmp_path / "ramp.mp4"
    gray_ramp_clip(src)
    real = subprocess.Popen

    def chatty(cmd, **kw):  # decoder logs at trace level: > 64 KB of stderr
        if "-f" in cmd and cmd[-1] == "-":
            cmd = ["ffmpeg", "-v", "trace"] + cmd[3:]
        return real(cmd, **kw)
    monkeypatch.setattr(R.subprocess, "Popen", chatty)
    c = {"file": str(src), "fps": 30.0, "width": 36, "height": 64, "series": {"luma": [50.0] * 60}}
    th = threading.Thread(target=R.render_shot, args=(0, _shot(transition={"type": "cut", "dur": 0}), c,
                                                       {"in": None, "out": None}, 0, 0, _cfg(), tmp_path / "o.mp4"), daemon=True)
    th.start()
    th.join(60)
    assert not th.is_alive(), "render_shot deadlocked on stderr"


def test_only_rerenders_parts_whose_handles_changed(proj):
    edl = json.loads((proj / "edl.json").read_text())
    for s in edl["shots"]:
        s["transition"] = {"type": "cut", "dur": 0}
    (proj / "h.json").write_text(json.dumps(edl))
    assert main(["render", str(proj), "--edl", "h.json", "--preview", "--out", "h.mp4"]) == 0
    edl["shots"][2]["transition"] = {"type": "fade", "dur": 0.4}
    edl["shots"][2]["in"] = max(edl["shots"][2]["in"], 0.25)
    (proj / "h.json").write_text(json.dumps(edl))
    assert main(["render", str(proj), "--edl", "h.json", "--preview", "--out", "h.mp4", "--only", "2"]) == 0
    s1 = edl["shots"][1]
    n1 = round(edl["shots"][2]["start"] * 30) - round(s1["start"] * 30)
    assert len(decode_frames(proj / "renders/h_parts/s01.mp4", 16, 28, pix="gray")) == n1 + 6  # tail handle added


# --- edl / draft ----------------------------------------------------------------------------------------------
def test_validate_catches_a_clip_id_that_now_names_another_file():
    sh = normalize({"shots": [{"start": 0, "clip": "c01", "in": 0, "file": "a_walk.mp4"}]}, 4.0)
    rep = validate(sh, BEATS, [clip("c01", name="0_new_intro.mp4"), clip("c02", name="a_walk.mp4")], 30, 4.0)
    assert not rep["ok"] and "use clip c02" in rep["errors"][0]


@pytest.mark.parametrize("raw", [
    {"shots": [{"start": 0, "clip": "c01", "in": 0, "zoom": [1.2]}]},
    {"shots": [{"start": 0, "clip": "c01", "in": 0, "anchor": [0.5]}]},
    {"shots": [{"start": 0, "clip": "c01", "in": 0, "transition": 5}]},
    {"shots": [1, 2]},
    {"shots": [{"start": 0, "clip": "c01", "in": 0, "gamma": "bright"}]},
])
def test_malformed_values_raise_edl_errors(raw):
    with pytest.raises(EDLError):
        normalize(raw, 4.0)


@pytest.mark.parametrize("kw", [{"gamma": -2}, {"gamma": 0.001}, {"fx": ["fade_out"], "fade_out": 9}, {"fx": ["fade_out"], "fade_out": -1}])
def test_validate_rejects_bad_gamma_and_fade_out(kw):
    sh = normalize({"shots": [dict(start=0, clip="c01", **{"in": 0}, **kw)]}, 4.0)
    assert not validate(sh, BEATS, [clip()], 30, 4.0)["ok"]


def test_unknown_keys_warned_and_negative_fix_in_never_suggested():
    sh = normalize({"shots": [{"start": 0, "clip": "c02", "in": 0, "zooom": [1, 1.5]}, {"start": 0.9, "clip": "c01", "in": 0}]}, 4.0)
    rep = validate(sh, BEATS, [clip(events=[{"t": 0.01, "kind": "red_on", "mag": 9}]), clip("c02")], 30, 4.0)
    assert any("zooom" in w for w in rep["warnings"])
    assert rep["events"] and all(e["fix_in"] is None or e["fix_in"] >= 0 for e in rep["events"])


def test_tr_frames_rounds_halves_up():
    assert [tr_frames({"transition": {"type": "fade", "dur": d}}, 30) for d in (0.3, 0.5)] == [10, 16]


def test_draft_never_splices_a_clip_into_itself():
    bm = {"beats": [{"t": 0.5 * k, "s": 1.0, "bar": k % 4 == 0} for k in range(1, 24)], "accents": [2.0 * k for k in range(1, 6)],
          "beat_period": 0.5, "drop": None, "onsets": []}
    wins = [{"start": 0.5 * k, "end": 0.5 * (k + 1), "q": 0.9, "flags": []} for k in range(40)]
    c = {"id": "c01", "name": "a.mp4", "duration": 20.0, "fps": 30, "windows": wins, "events": []}
    shots = draft(bm, [c], 11.0)
    for a, b in zip(shots, shots[1:]):
        assert abs(b["in"] - (a["in"] + (a["end"] - a["start"]) * a["speed"])) > 0.1, (a, b)
    assert all(s["file"] == "a.mp4" for s in shots)


# --- analysis -------------------------------------------------------------------------------------------------
def kick_hat_song(path: Path, drop=10.0, dur=16.0, sr=22050) -> None:
    t = np.arange(int(sr * dur)) / sr
    y = 0.01 * np.random.default_rng(1).standard_normal(t.size)
    n = int(0.08 * sr)
    kick = np.sin(2 * np.pi * 60 * np.arange(n) / sr) * np.exp(-np.arange(n) / (0.03 * sr))
    hat = np.random.default_rng(2).standard_normal(int(0.02 * sr)) * np.exp(-np.arange(int(0.02 * sr)) / (0.004 * sr))
    for k in range(int(dur * 2)):
        loud = 1.0 if k * 0.5 >= drop else 0.35
        i = int(k * 0.5 * sr)
        y[i:i + n] += loud * kick[: len(y) - i]
        j = int((k * 0.5 + 0.25) * sr)
        y[j:j + hat.size] += 0.6 * hat[: max(0, len(y) - j)]
    y[int(drop * sr):] += 0.2 * np.sin(2 * np.pi * 55 * t[int(drop * sr):])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


def test_beats_follow_the_kick_not_the_offbeat_hat(tmp_path):
    kick_hat_song(tmp_path / "s.wav")
    bm = analyze_song(str(tmp_path / "s.wav"))
    grid = np.array([b["t"] for b in bm["beats"]])
    assert np.median(np.abs(((grid + 0.25) % 0.5) - 0.25)) < 0.04  # on multiples of 0.5 s (kicks), not 0.25 + k/2
    assert bm["drop"] is not None and abs(bm["drop"] - 10.0) < 0.1


def test_analyze_survives_tiny_and_broken_clips(media, tmp_path):
    foot = tmp_path / "f"
    foot.mkdir()
    ff("-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=0.1", "-pix_fmt", "yuv420p", str(foot / "a_tiny.mp4"))
    (foot / "b_broken.mp4").write_bytes(b"not a video")
    (foot / "c_good.mp4").write_bytes((media["footage"] / "a_walk.mp4").read_bytes())
    root = tmp_path / "p"
    assert main(["init", str(root), "--footage", str(foot), "--song", str(media["song"]), "--length", "5"]) == 0
    assert main(["analyze", str(root)]) == 0
    names = [c["name"] for c in json.loads((root / "analysis/clips.json").read_text())["clips"]]
    assert "c_good.mp4" in names and "b_broken.mp4" not in names


# --- cli ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("target", ["project.json", "analysis/clips.json", "."])
def test_draft_out_refuses_reserved_targets(proj, target):
    before = (proj / "project.json").read_text()
    assert main(["draft", str(proj), "--out", target, "--force"]) == 2
    assert (proj / "project.json").read_text() == before


def test_run_with_edl_keeps_hand_edits_and_verifies_what_it_rendered(proj):
    edl = json.loads((proj / "edl.json").read_text())
    edl["shots"][0]["note"] = "HAND EDIT"
    (proj / "mine.json").write_text(json.dumps(edl))
    assert main(["run", str(proj), "--edl", "mine.json", "--preview", "--out", "mine.mp4"]) == 0
    assert "HAND EDIT" in (proj / "mine.json").read_text()
    assert (proj / "renders/mine.verify.json").exists()


def test_guide_and_cli_guards(proj, media, tmp_path, capsys):
    assert "--video montage_preview.mp4" in GUIDE
    assert main(["analyze", str(proj), "--bpm", "-120"]) == 2
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders/x.txt").write_text("hi")
    (proj / "renders/x.json").write_text("{}")
    assert main(["verify", str(proj), "--video", "x.txt"]) == 2
    assert main(["init", str(media["footage"] / "job"), "--footage", str(media["footage"]), "--song", str(media["song"])]) == 2
    assert main(["init", str(tmp_path / "j"), "--footage", str(media["footage"]), "--song", str(media["song"]), "--length", "30"]) == 0
    assert "shortened" in capsys.readouterr().out


def test_verify_measures_the_flash_cut_and_ignores_crossfade_ramps(tmp_path):
    v = tmp_path / "v.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=90x160:r=30:d=1", "-f", "lavfi", "-i", "smptebars=s=90x160:r=30:d=1",
       "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-filter_complex",
       "[1:v]fade=t=in:st=0:d=0.14:color=white[b];[0:v][b]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a",
       "-t", "2", "-pix_fmt", "yuv420p", str(v))
    shots = [{"start": 0.0, "end": 1.0, "clip": "c01", "in": 0.0, "speed": 1.0, "fx": [], "transition": {"type": "cut", "dur": 0}, "anchor": [.5, .5], "zoom": [1, 1]},
             {"start": 1.0, "end": 2.0, "clip": "c01", "in": 0.0, "speed": 1.0, "fx": ["flash"], "transition": {"type": "cut", "dur": 0}, "anchor": [.5, .5], "zoom": [1, 1]}]
    rep = verify(v, shots, BEATS, [clip()], 30, 90, 160)
    assert rep["cuts"][0]["result"] == "on frame"
