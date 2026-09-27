# Editing playbook (what clients notice)

## Story first
- Clients complain about "abrupt clips" when the order doesn't tell a story. Build a sequence a stranger can follow:
  arrival → reveal/details → the action → the payoff. Car example that was approved:
  walk towards the car → unlock flash → mirror unfolds → car in reverse (push-in on reverse lamps = "coming towards you")
  → driving out of the parking → hand on gear / cluster wakes → DROP: headlights snap on → highway + exterior light hits
  → tail-light finale on the last big accent.
- Real estate: street/exterior → door opening → hero living space → kitchen/details → bedroom/bath → view/terrace → exterior dusk.
- Events: arrival/venue → people/prep → main moment (drop) → reactions/dance → closing wide.
- If the footage lacks a beat the client asked for, say so and use the closest honest substitute; don't fake it silently.

## Beat sync (non-negotiable)
- Every cut on a beat/onset from brief.md. Before the drop: cut on the Strong accents / bar starts of the build
  (~every 2 s at 120 BPM). After the drop:
  ~every 1 s on the strong accents; faster (0.5 s) only for short bursts.
- The drop gets the single hardest visual: a light switching on, a reveal, a door opening — with `flash` + `punch`.
- Light events (tail lights, indicators, headlights, a screen booting, scene light snapping on) land exactly on beats.
  Put the event on the cut (in = event time) or on an onset inside the shot (validate shows the `in` to use).
- Big accents inside the drop: give every 1-s accent either a hard cut, a light event, or a punch. A soft crossfade on a
  big accent reads as "the beat was missed".

## Smooth flow without losing sync
- Crossfades (`fade`, 0.2–0.3 s, centred on the beat) between different scenes before the drop; hard cuts on hits.
- Never crossfade into a shot whose light event sits on the cut — it halves the hit. Hard cut there.
- `dip` (to black and back) for chapter changes (e.g. garage → night exterior); black lands exactly on the beat.
- `whip` (0.2 s) between two moving shots in the drop section; don't use it more than every third cut.
- Same-clip jump forward on a downbeat with a slight zoom step reads as an intentional punch-in; flag it to the user —
  some clients see it as a glitch.

## Shot craft
- 0.7–2.5 s per shot before the drop, 0.5–1.2 s after. Under 0.4 s is unreadable unless it's a flash frame.
- Never hold a frozen frame; slow-mo (0.6–0.9) a short clip instead or drop it.
- Slow-mo only on smooth motion or static shots; avoid it across a hard light switch unless the switch is the shot's point.
- Gentle push-ins (1.0→1.1) keep static shots alive; anchor the zoom on the subject. Stronger push (1.2→1.75) sells
  "coming closer" on a static shot.
- Avoid: black/blurry/shaky windows (brief "avoid" list), phone fumbling, the first 0.5 s of most phone clips.
- End on the last big accent with the strongest image; let the lights/scene end on the final kick or fade out.

## Delivery checklist
- `beatcut verify` STATUS PASS/WARN reviewed; timeline.jpg eyeballed.
- 1080x1920, 30 fps, audio present (~ -14 LUFS), file ≈ 30 MB for 25 s (`--quality master` for a larger master).
- Music rights and privacy (number plates, bystander faces) mentioned to the client.
