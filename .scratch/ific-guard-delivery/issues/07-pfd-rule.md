# 07 pfd rule on real filings

Status: ready-for-agent
Type: task
Size: M
Blocked by: 06
Owns: `ific_guard/screening/rule_pfd.py`, `ific_guard/screening/scoring.py`, additive reads in `ific_guard/screening/space.py`, `tests/test_rule_pfd.py`

## Goal

Run the pfd engine on filings, so that a case can state a calculated margin against a cited limit. This is what moves a case from "possible concern" to "exceeds this limit under these assumptions".

## Data

From IFIC 3083's schema; confirm against 3074 and the Space Preface:

- `emiss.pwr_ds_max`: maximum power density, joined on `grp_id`. Values run from -96.8 to 23. Confirm the unit before using it.
- `s_beam.gain`: joined on `ntc_id`, `emi_rcp` and `beam_name`, which `grp` also carries.
- `geo.long_nom`, `freq.freq_min`, `freq.freq_max`, `freq.bdwdth`.

## Do

1. Extend `SpaceIssue` with these fields without changing what existing rules read. Keep reads efficient, since 3074 is large.
2. For visible GSO space-to-Earth groups in masked bands, evaluate using worst-case inputs from ARCHITECTURE.md section 7: boresight gain towards Finland and maximum power density.
3. Fill the `details` contract in `map.md`, including `calc_inputs` and `assumptions`. Label Article 21 results and Appendix 5 results separately.
4. Add `scoring.py`, implementing ARCHITECTURE.md section 4.7, with weights as configuration. Show the reasons for each score. Ticket 10 reuses it.
5. Non-GSO: implement the one-minute orbit propagation in ARCHITECTURE.md section 4.5, since the brief names non-GSO pfd as being of special interest. Where orbit data is missing, screen on frequency and inclination, set `method: "screening"`, and flag for expert review.

## Wording

Say "exceeds under worst-case assumptions". Without beam contours, boresight gain over-estimates pfd where Finland is off-axis, so a result here is not proof of non-compliance.

## Acceptance

- A test recalculates one real filing's worst point by hand from its source rows.
- Rerunning on the same data gives identical numbers.
- On 3074: counts by exceeds, within 3 dB and below limit, and by band, plus 3 worked examples in your report.
- Groups with missing power or gain are counted and reported, not silently skipped.

## Not in scope

GIMS beam contours and Article 22 epfd.
