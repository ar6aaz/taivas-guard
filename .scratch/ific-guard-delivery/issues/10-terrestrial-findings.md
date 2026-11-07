# 10 Terrestrial findings and 3076 export

Status: ready-for-agent
Type: task
Size: M
Blocked by: 03, 08, 09
Owns: `ific_guard/terrestrial/rule_field_strength.py`, the terrestrial branch in `ific_guard/screening/engine.py`, `tests/test_terrestrial_findings.py`

## Goal

Connect adapter and engine, so that Terrestrial 3076 goes through the same pipeline as space. This demonstrates criterion B: the same architecture handling a second domain.

## Do

1. Replace the `no_rules` branch in `screen_artifact` for terrestrial. Keep the sync, events, cases and notifier code unchanged. Changes needed outside the terrestrial code are evidence about the architecture, so list them.
2. For each filtered notice from ticket 08, determine the applicable framework (ITU, CEPT/ECC, national) and criterion, then calculate field strength at Finnish border points or at protected stations with ticket 09's engine.
3. Fill the `details` contract in `map.md` with `method: "p1546"`: source notice, calculated field strength, model parameters, assumptions and citations. Use `scoring.py` from ticket 07 if merged; otherwise a small placeholder, and tell the coordinator.
4. Decide what the issue-to-issue diff needs from the previous issue, and how a run gets it. Find the previous artifact by issue number, and state what happens when it is missing.
5. Export with ticket 03's exporter to `outputs/ific_3076/`.

## Acceptance

- `screen_status` is `done` for 3076, and a full pipeline test takes a fake terrestrial artifact through to a digest.
- Each exported finding contains everything the brief's side quest lists: source notice, calculated field strength, parameters, assumptions and citations.
- A hand check of 3 findings by recalculating with the engine directly.
- Stated clearly: how many notices were filtered out and why, so that an expert can judge possible false negatives.

## Not in scope

Terrain data, services other than land mobile, and trends (ticket 11).
