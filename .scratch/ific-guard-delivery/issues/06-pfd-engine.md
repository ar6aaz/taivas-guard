# 06 pfd engine and limit masks

Status: ready-for-agent
Type: task
Size: L
Blocked by: none
Owns: `ific_guard/engines/pfd.py`, `data/masks/`, `tests/test_pfd.py`

## Goal

A pure, deterministic pfd calculation with limits held as cited data. It takes plain numbers and reads no database, so it can be built and verified alone. Ticket 07 connects it to filings.

## Do

1. Implement the GSO calculation in ARCHITECTURE.md section 4.5: distance, angle of arrival, pfd in a reference bandwidth, and margin against a piecewise mask. Free-space only. Put units in names.
2. Evaluate over a grid of about 0.5 degrees covering Finland, plus given station points, and return the worst point with its inputs.
3. Build a Finland boundary test. A bounding box plus the Natural Earth polygon is fine, as long as the source is recorded. Include northern Finland, where GSO satellites arrive at low elevation.
4. Create `data/masks/limit_masks.json` for Article 21 limits (compliance) and Appendix 5 thresholds (coordination trigger), labelled separately. Each entry has band, service, orbit type, reference bandwidth, breakpoints, unit and citation.
5. Start with the bands that matter most: 3.4-4.2, 8.025-8.5, 10.7-12.75 and 17.7-19.7 GHz. List the bands you did not cover.

## Rule on values

Take every limit from the ITU Radio Regulations text, using the edition linked in the brief: `https://www.itu.int/pub/r-reg-rr`. **Do not write limits from memory.** If you cannot get the text, build the engine with a clearly marked test-only mask, set this ticket to `needs-info`, and say so in your report. A wrong limit would produce false claims in a letter to another administration.

## Acceptance

- Hand-calculated tests for at least three geometries, including a satellite at Finland's longitude and one near the edge of visibility, showing the working in comments.
- Mask-edge tests at each breakpoint, such as 5 and 25 degrees, and at band edges.
- A satellite below the horizon gives "not visible", not a number.
- A cross-check with ITU's PFD GSO software on 2-3 networks if available. If not, say it was not done.

## Not in scope

Reading IFIC data, non-GSO propagation (ticket 07), and Article 22 epfd.
