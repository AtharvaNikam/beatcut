from beatcut.beats import analyze_song, cut_points

from conftest import BPM, DROP


def test_tempo_drop_and_snapped_beats(media):
    bm = analyze_song(str(media["song"]))
    assert abs(bm["bpm"] - BPM) < 1.5
    assert bm["drop"] is not None and abs(bm["drop"] - DROP) < 0.3
    clicks = [0.25 + k * 0.5 for k in range(20)]
    snapped = [b["t"] for b in bm["beats"] if b["onset"]]
    assert len(snapped) >= 15
    for t in snapped:  # every snapped beat sits on a real click (the analysis window centre adds < 1 frame)
        assert min(abs(t - c) for c in clicks) < 0.035
    assert all(0 <= p <= bm["duration"] for p in cut_points(bm))
