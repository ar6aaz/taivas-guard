# 05 Station loader and overlap rule

Status: ready-for-agent
Type: task
Size: M
Blocked by: none
Owns: `ific_guard/screening/stations.py`, `ific_guard/screening/rule_stations.py`, `tests/test_stations.py`

## Goal

Use the mandatory special-interest dataset: flag filings that transmit on a protected station's receive frequency, and list the affected stations on the case.

## Input

`docs/challenge/Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx`. It is synthetic; Traficom says coordinates and heights may be impossible, and to apply common sense.

Known layout and problems:

- Row 11 is the English header, row 12 Finnish, and data starts at row 13. There are 874 rows; row 21 is an exact duplicate.
- Station number is not unique: 626855 is on two rows with different locations. Use sheet row plus station number as the key.
- Blank cells mix empty text and None. Some numbers are text. Decimals can use commas (`7,6k`).
- Frequencies appear in kHz, MHz and GHz. Bandwidths appear as `300.000 kHz`, `8M00`, `5000k`. 2 rows have no channel bandwidth, so fall back to transmission bandwidth.
- 61 rows have no RX frequency, and 3 have no TX frequency.
- MVV gain (15-77 dB) counts up by 1 per row, so treat it as not real. 67 no-class rows have power from 133 MW to 9.4 GW. Row 108 has an antenna height of -28 m.

## Do

1. Write a loader with a typed record and a `quality_flags` list per row. Keep bad rows and flag them; do not drop them silently.
2. Add a rule: a visible satellite's space-to-Earth assignment overlaps a station's receive band. Reuse the visibility logic in `M4NoDownlinkRule` rather than copying it (a small shared helper is fine).
3. Keep cases readable: one case per notice family, listing station IDs and bands. Frequency overlap alone gives 390 stations on IFIC 3083, including all 320 FX stations.
4. Use the `details` contract in `map.md`, with `method: "screening"`.
5. Treat missing RX per ticket 02 if answered. Otherwise skip those rows, record the assumption, and report the count.

## Acceptance

- The loader returns 873 records and tests cover each listed problem.
- On IFIC 3083 with existing rules enabled, the new rule adds cases for these notices, which no current rule catches: WSDRN-5M (123520041, 323520041), LANDTEST, WSERIES7, GOMX-5, GHGSAT-CX. Explain any difference from this list.
- Spot-check 3 cases by hand against the spreadsheet and the `.mdb`.

## Not in scope

Power, geometry or antenna-direction calculations. Ticket 07 can use your loader for those.

## Comments

2026-11-07: The power, geometry and antenna-direction work this ticket handed to ticket 07 was never picked up. It is now tracked in ticket 19.
