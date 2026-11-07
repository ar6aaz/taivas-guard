# 19 Link pfd results to special-interest stations

Status: ready-for-agent
Type: task
Size: L
Blocked by: none
Priority: The user's top priority: `Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx` is the file we care about most.
Owns: `ific_guard/screening/rule_stations.py`, `ific_guard/screening/rule_pfd.py`, `ific_guard/screening/scoring.py`, station and pfd tests, the case page and label in `frontend/src`, the station section of `ific_guard/llm/template.py`

## Why

The station file drives very little of the space screening, and its cases rank low. On Space 3074 at `32b6b0b`:

| Measure | Result |
|---|---:|
| `station-rx-overlap` cases | 51, all fixed at medium |
| Rank of the first one | 102 of 182 |
| Stations named by those cases | 604 of 873 (69%), so the match barely filters |
| pfd cases naming a station | 0 of 76 |
| Space cases with `special_interest` set | 0, so the scoring boost never fires |

Terrestrial already does this: 14 of 29 cases in 3076 are linked, with the field calculated at each station's site.

### How the gap happened

Ticket 05 put "power, geometry or antenna-direction calculations" out of scope and said "Ticket 07 can use your loader for those". Ticket 07 never did, and the coordinator merged it without noticing. So the assumption "Frequency overlap only: beam coverage, power, pfd, geometry and antenna direction are not checked, and the service allocation of the band is not considered" records unfinished work, not a decision.

## What changes for each item in that assumption

| Item | Decision |
|---|---|
| pfd and power | Add. The pfd rule already calculates them in the same run |
| Geometry | Add. The sheet has coordinates, and `gso_geometry` and `evaluate_gso` accept any point |
| Service allocation | Add. The M4 table is already loaded for `m4-no-space-to-earth` |
| Beam coverage | Don't add. `s_beam` has no boresight in 3074, and Traficom said beams "are not relevant" and "the worst case scenario should always be assumed" |
| Antenna direction | Don't add for now. The satellite's isn't in the data. The sheet has station direction, but only 85 of 873 rows have a gain and ticket 02 question 14 is unanswered |

## Do

1. **Find the stations for each pfd calculation.** Use `Station.protected_bands`, both RX and TX, overlapping the assignment's band. Share the code with `rule_stations.py` rather than copying it.
2. **GSO: calculate at each station's coordinates.** Report the value, limit, margin and arrival angle per station. A station that can't see the satellite is a computed result: it gets no margin and is counted separately.
3. **Non-GSO: keep the two kinds of result apart.** Test station points in the one-day flight and report them as sampled results. The upper-bound envelope has no location, so report it as applying to the filing, never as a value at a station. Don't call a bound an exceedance.
4. **Treat a TX match as anywhere in Finland, not at the site.** Traficom (ticket 02): "every TX is received in Finland", broadcasting is received over the "whole coverage area", and a fixed link's TX is received at its other end, so "possible high pfd's within Finland territory might raise concern".
   - For a TX match, use the worst result over the Finnish grid, which the rule already calculates, and name the station.
   - Use the margin at the station's own coordinates only for RX matches.
   - Never present a site value as the value at a TX receiver.
5. **Set `station_ids` and `special_interest`** on linked pfd cases, so the scoring boost applies and the drafts list them.
6. **Add the M4 allocation to station cases:** the primary services in the band, and whether M4 gives it a space-to-Earth allocation in Finland.
7. **Base severity on the evidence,** replacing the fixed medium:
   - High: a calculated exceedance, not a bound, at a listed station.
   - Below that: an overlap with only a bound or no calculation behind it.
   - Lowest, or dropped from the case's station list with a count kept: stations that can't see the satellite.

   This is our choice, not Traficom's, so state that in the README.
8. **Show it to the reviewer.** List the affected stations and their margins on the case page and in the letter. Replace the label "Overlaps a Finnish receiver" (`frontend/src/api/http.ts:50`), since the rule also covers TX. Regenerate `frontend/src/data/` samples if they change.
9. **Rewrite the assumption text** so it names only what stays unchecked: beam coverage and antenna direction, with the reason for each.
10. **Regenerate and recheck.** Export `outputs/ific_3074/` and `outputs/ific_3076/` from a clean Compose run, and extend `tools/hand_check.py` to recompute station margins from raw rows and the sheet.

## Acceptance

- On 3074, report before and after: station-rule cases, stations named, pfd cases naming a station, severity counts, and the rank of the first station case.
- At least one GSO and one non-GSO station margin is recomputed by hand from raw rows and the sheet's coordinates, and matches.
- A test covers each of: a station that can't see a GSO satellite, an RX match, a TX match, a non-GSO sampled result at a station, and a bound that stays a bound.
- No new "exceeds" result is created by a station's location alone. If one appears, explain it, since the grid already takes the worst point over Finland.
- No regulatory value changes. Any new threshold is cited or stated as our choice.
- The time to screen 3074 is measured before and after. Only overlapping stations should be evaluated.
- `uv run pytest` and the frontend tests pass, and every changed count in the README and verification files is updated.

## Not in scope

Beam contours. Finnish satellites as protected systems, which ticket 18 lists separately. Filings around the Moon, unless it's a one-line fix when touching the visibility check.

## Open question

Over-flagging: how much is acceptable (ticket 18, question 4)? Bring this ticket's before-and-after counts to Traficom.
