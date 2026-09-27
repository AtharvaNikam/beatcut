# edl.json — the cut list

```json
{
  "version": 1,
  "shots": [
    {"start": 0.0,   "clip": "c01", "in": 0.40, "speed": 1.0, "zoom": [1.0, 1.15], "anchor": [0.5, 0.45],
     "fx": ["fade_in"], "note": "walk towards the car"},
    {"start": 3.62,  "clip": "c02", "in": 6.14, "speed": 1.25, "zoom": [1.0, 1.08], "anchor": [0.72, 0.5],
     "transition": {"type": "fade", "dur": 0.3}, "note": "unlock flash lands on 4.67"},
    {"start": 15.67, "clip": "c11", "in": 8.85, "fx": ["flash", "punch"], "note": "DROP: headlights on"},
    {"start": 24.68, "clip": "c09", "in": 4.50, "fx": ["pop"], "gamma": 1.35, "note": "finale"}
  ]
}
```

| field | meaning |
|---|---|
| `end` | written by draft, informational only — a shot always ends where the next starts |
| `file` | source file name, written by draft; validate errors if the clip id now maps to a different file |
| `start` | timeline second the shot starts. Must be a beat/onset from brief.md (validate warns otherwise). First shot = 0. A shot ends where the next starts; the last ends at the montage length. |
| `clip` | clip id from brief.md (`c01`…) |
| `in` | source second of the clip that appears at `start` |
| `speed` | 1.0 real time; 0.5–0.9 slow-mo (motion-interpolated); 1.1–2.0 faster. Source used = shot length × speed |
| `zoom` | `[start, end]` scale, 1.0 = frame filled. Ramp is smooth (log-space). Keep ≤ 1.6 on sharp detail, ≤ 1.8 on dark shots (sources are often 576 px wide) |
| `anchor` | `[x, y]` 0–1: the point that stays fixed while zooming — put it on the subject (a tail light, the mirror) |
| `fx` | any of `fade_in` (from black), `fade_out` (to black; length via `"fade_out": 0.45` seconds, > 0 and ≤ the shot), `flash` (white flash on the hit), `punch` (quick 5 % push-in on the hit), `pop` (brief brightness pop), `glitch` (0.16 s RGB split), `blur_in` |
| `transition` | into this shot: `cut` (default), `fade` (crossfade, centred on the cut), `dip` (fade out → hard cut at black on the beat → fade in), `whip` (motion-blur wipe). `{"type": "fade", "dur": 0.3}` — dur is rounded to an even number of frames, halves up (0.3 s at 30 fps = 10 frames) |
| `gamma` | optional exposure lift override; validate enforces 0.5–2.0 (1.0–1.6 recommended); default is automatic |
| `note` | free text: say why the shot is there |

Rules the validator enforces: starts increase; source (plus transition handles) stays inside the clip; `fade_in`/`flash`
need a hard cut or dip into the shot; `fade_out` needs a cut/dip out of it; a transition can't be longer than half of
either neighbouring shot; `in` must leave room before it for a centred transition (≥ half the transition × speed).
Unknown keys are ignored with a warning (catches typos like `zooom`).

Landing a light event on a beat: event timeline time = `start + (event_src_time − in) / speed`. `validate` prints the
exact `in` that lands an off-beat event (`set in=… to land it`). Two events in one shot (e.g. two indicator blinks 1.0 s
apart onto beats 1.0 s apart) → keep speed 1.0; to put a second event on the next beat, solve for speed.

Legacy keys also load: `tlStart/tlEnd`, `srcIn`, `zoomStart/zoomEnd`, `zoomCenter: "48%,52%"`, `tin`, `fadeOut`,
fx `shake/exposure_pop/rgb_split/flash_in/fade_from_black/fade_to_black`, transitions `dissolve/fadeblack/hblur`.
