# 04 M4 bands from Traficom's API

Status: ready-for-agent
Type: task
Size: M
Blocked by: none
Owns: `tools/build_m4_bands.py`, `data/national/`, `ific_guard/screening/m4.py`, `tests/test_m4.py`, the band-loading part of `rules.py`

## Goal

Close a known false negative. `generetated-files/m4_satellite_filter.csv` merges 3400-4200 MHz into one band marked as shared, so `m4-no-space-to-earth` cannot flag 3.4-3.8 GHz. ARCHITECTURE.md section 4.4 names that range as a key national case: mobile and fixed, with no space-to-Earth allocation in Finland.

## Inputs

- `https://opendata.traficom.fi/api/v13/Taajuusjakotaulukko`: 5,917 JSON rows at last check, with fields including `Services_in_Finland`, `Sub_band_lower_limit__Hz_`, `Sub_band_upper_limit__Hz_`, `Sub_band_usage`, `Direction`. Inspect the full field list.
- `files/finland-regulations.pdf`: the regulation, in the main checkout, for checking and for page citations.
- `generetated-files/m4_satellite_filter.csv`: the current file, for comparison. Do not delete it.

## Do

1. Write a repeatable tool that fetches the API once, saves the raw snapshot with its date, and builds a band table in `data/national/`.
2. Keep sub-band resolution where it changes the answer, and keep citations (table page, or API row ID).
3. Derive flags for space-to-Earth allocation, terrestrial primary use, and passive use from the service text. Log any text you cannot classify rather than guessing.
4. Change `load_m4_conflict_bands` to use the new table.
5. Compare old and new results on IFIC 3083, and report which cases appear and disappear.

## Acceptance

- A test proves that a GSO downlink at 3500 MHz, visible from Finland, is flagged, and that one at 3900 MHz is judged by its own sub-band.
- Hand-check 3.4-3.8, 8.025-8.4, 10.7-12.75 and 17.7-19.7 GHz against ARCHITECTURE.md section 4.4 and the PDF. Report any disagreement.
- The build works from the saved snapshot with no network.

## Not in scope

Calculating limits, and changing severity beyond the existing passive-band rule.
