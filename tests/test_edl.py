import pytest

from beatcut.clips import detect_events
from beatcut.edl import EDLError, normalize, validate

import numpy as np

BEATS = {"beats": [{"t": t, "s": 1.0} for t in (0.0, 1.0, 2.0, 3.0, 4.0)], "onsets": []}


def clip(cid="c01", dur=10.0, events=()):
    return {"id": cid, "fps": 30, "duration": dur, "windows": [], "events": list(events)}


def shots(*rows, total=4.0):
    return normalize({"shots": [dict(start=s, clip=c, **kw) for s, c, kw in rows]}, total)


def test_legacy_keys_are_accepted():
    s = normalize({"shots": [{"tlStart": 0, "tlEnd": 2, "clip": "c01", "srcIn": 1.5, "zoomStart": 1.1, "zoomEnd": 1.3,
                              "zoomCenter": "40%,60%", "fx": ["shake", "fade_from_black", "none"],
                              "tin": {"type": "dissolve", "dur": 0.2}}]}, 2.0)[0]
    assert s["in"] == 1.5 and s["zoom"] == [1.1, 1.3] and s["anchor"] == [0.4, 0.6]
    assert s["fx"] == ["punch", "fade_in"] and s["transition"]["type"] == "fade" and s["end"] == 2.0


def test_valid_cut_passes_and_ends_are_derived():
    sh = shots((0.0, "c01", {"in": 0}), (2.0, "c01", {"in": 5}))
    rep = validate(sh, BEATS, [clip()], 30, 4.0)
    assert rep["ok"], rep["errors"]
    assert sh[0]["end"] == 2.0 and sh[1]["end"] == 4.0


@pytest.mark.parametrize("rows,needle", [
    ([(0.0, "zz", {"in": 0})], "unknown clip"),
    ([(0.0, "c01", {"in": 0}), (2.0, "c01", {"in": 0.05, "transition": {"type": "fade", "dur": 0.3}})], "handle"),
    ([(0.0, "c01", {"in": 0}), (2.0, "c01", {"in": 3, "fx": ["fade_in"], "transition": "fade"})], "fade_in/flash"),
    ([(0.0, "c01", {"in": 0, "fx": ["sparkles"]})], "unknown fx"),
    ([(0.0, "c01", {"in": 9.9})], "is 10.00s"),
])
def test_errors(rows, needle):
    rep = validate(shots(*rows), BEATS, [clip()], 30, 4.0)
    assert not rep["ok"] and any(needle in e for e in rep["errors"]), rep["errors"]


def test_off_beat_cut_and_event_fix_hint():
    sh = shots((0.0, "c01", {"in": 5}), (2.4, "c01", {"in": 1.0}))
    rep = validate(sh, BEATS, [clip(events=[{"t": 2.0, "kind": "red_on", "mag": 20}])], 30, 4.0)
    assert any("off-beat" in w for w in rep["warnings"])
    ev = [e for e in rep["events"] if e["kind"] == "red_on"][0]
    assert ev["shot"] == 1 and not ev["on_beat"]
    assert abs(ev["tl"] - 3.4) < 1e-6 and abs(ev["fix_in"] - 1.4) < 1e-6  # in=1.4 lands it on 3.0


def test_start_must_be_zero_and_empty_rejected():
    with pytest.raises(EDLError):
        normalize({"shots": []}, 4.0)
    assert not validate(shots((0.5, "c01", {"in": 0})), BEATS, [clip()], 30, 4.0)["ok"]


def test_detect_events_finds_hard_light_switch():
    n = 60
    st = {k: np.zeros(n) for k in ("red", "amber", "white", "motion")}
    st["luma"] = np.full(n, 10.0)
    st["red"][30:] = 12.0
    st["luma"][30:] = 30.0
    ev = detect_events(st, 30)
    assert any(e["kind"] == "red_on" and abs(e["t"] - 1.0) < 1e-6 for e in ev)
