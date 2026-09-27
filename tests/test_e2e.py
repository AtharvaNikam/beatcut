"""Whole pipeline on synthetic media: init -> analyze -> draft -> validate -> render --preview -> verify."""
import json

import pytest

from beatcut.cli import main
from beatcut.project import Project, ProjectError


def test_pipeline(media, tmp_path):
    root = tmp_path / "proj"
    assert main(["init", str(root), "--footage", str(media["footage"]), "--song", str(media["song"]), "--length", "8"]) == 0
    assert main(["analyze", str(root)]) == 0
    clips = json.loads((root / "analysis/clips.json").read_text())["clips"]
    assert [c["name"] for c in clips] == ["a_walk.mp4", "b_tail.mp4", "c_bars.mp4"]
    assert any(e["kind"] == "red_on" and abs(e["t"] - 1.0) < 0.04 for e in clips[1]["events"])
    assert (root / "analysis/brief.md").read_text().count("### c0") == 3
    assert main(["draft", str(root)]) == 0
    assert main(["draft", str(root)]) == 2  # refuses to overwrite without --force
    assert main(["validate", str(root)]) == 0
    assert main(["render", str(root), "--preview"]) == 0
    assert main(["verify", str(root), "--video", "montage_preview.mp4"]) == 0
    rep = json.loads((root / "renders/montage_preview.verify.json").read_text())
    assert rep["status"] != "FAIL"
    assert rep["loudness"]["lufs"] > -40
    assert all(c["result"] != "OFF by 1 frame" for c in rep["cuts"])
    assert (root / "renders/montage_preview_review/timeline.jpg").exists()


def test_outputs_cannot_escape_the_project(media, tmp_path):
    p = Project.create(tmp_path / "p", str(media["footage"]), str(media["song"]))
    assert p.out_video("../../evil.mp4").parent == p.renders
    with pytest.raises(ProjectError):
        p.in_root("../outside.json")
    assert main(["draft", str(p.root), "--out", "../x.json"]) == 2
