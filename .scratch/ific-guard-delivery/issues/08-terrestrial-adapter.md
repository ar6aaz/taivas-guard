# 08 Terrestrial adapter, diff and filter

Status: ready-for-agent
Type: task
Size: L
Blocked by: none
Owns: `ific_guard/terrestrial/adapter.py`, `ific_guard/terrestrial/diff.py`, `tests/test_terrestrial_adapter.py`

## Goal

The first half of the side quest: read terrestrial land-mobile notices, identify what is new, modified or withdrawn between consecutive issues, and filter foreign assignments that may be relevant to Finland.

## Inputs

`data/shared/brific3075.mdb` and `brific3076.mdb`. The schema differs from space. Tables as seen in Terrestrial 3083, so confirm them on 3076:

- `fxm_terra`: 7,371 rows, including `terrakey`, `freq_assgn`, `freq_min`, `freq_max`, `bdwdth`, `geo_key`, `adm`. Print the full column list.
- `fxm_geo`: `geo_key`, `long_dec`, `lat_dec`, `site_alt`, `ctry`, `site_name`
- `fxm_ant`: `terrakey`, `pwr_dbw`, `gain_max`, `hgt_agl`, `eff_hgtmax`, `azm_max_e`, `polar`, `tx_rx`
- `fxm_ant_hgt`: effective height by azimuth
- `fxm_nat_serv`: `nat_srv`; `ref_nat_srv` and `ref_stn_cls` give meanings
- `fxm_target`: `terrakey`, `target`, `action`; `fxm_coord`: `adm`, `coord_prov`

Read `ReadMe_3076.pdf` (`.../BRIFIC/ReadMe/ReadMe_3076.pdf`) for structure. Note the zip holds one `.mdb`, despite ARCHITECTURE.md section 4.1 mentioning a converter to SQLite. `MdbReader` in `screening/space.py` reads it directly.

## Do

1. Define land mobile using service and station-class codes from the reference tables. State the exact codes and why.
2. Produce a typed `TerrestrialIssue`, keeping `terrakey` verbatim plus raw source rows.
3. Diff two issues using the data's own `action` and `target` fields if they mean what they appear to, otherwise by comparing rows. Check this: an IFIC is an update, so absence from the next issue may not mean withdrawn. Report what you conclude and why.
4. Filter to foreign stations near Finland: by administration using `settings.neighbour_adms` (`RUS`, `S`, `EST`, `NOR`, `DNK`), and by distance to the Finnish border, with a band-dependent limit in configuration. The distance matters: Russian notices include stations far from Finland, and `DNK` may include Greenland and the Faroes. Do not hard-code the country list.

   **Known data pattern.** Station rows by notifying administration, before any land-mobile or distance filter:

   | Issue | `fxm_terra` RUS | EST | S, NOR, DNK | `fmtv_terra` RUS | EST | S, NOR, DNK |
   |---|---:|---:|---:|---:|---:|---:|
   | 3075 | 499 | 24 | 0 | 78 | 0 | 0 |
   | 3076 | 313 | 0 | 0 | 51 | 0 | 0 |
   | 3083 | 815 | 70 | 0 | 4 | 2 | 0 |

   So in 3076, stations come from Russia alone. Do not treat zero Swedish, Norwegian or Danish notices as a bug or a clean result: state it in the output. `fxm_coord` does name `S`, `EST` and `NOR` on some notices. Work out what those rows mean from the ReadMe, and report whether they add anything relevant to Finland.
5. Add land/sea path information, or a clear interface for ticket 09 to compute it.

## Acceptance

- Counts for 3075 to 3076: new, modified and withdrawn, by administration, and how many pass the filter. Reconcile them against the issue's published totals (`pub_totals`, Article 11 index) where possible.
- Hand check of 5 notices, including one of each change type if present.
- `MdbReader` handles the schema on 3076 without error, with row counts compared to `mdbtools` if available.

## Not in scope

Field strength (ticket 09), cases and export (ticket 10), and trends (ticket 11).

## Comments

2026-11-07: `ific_guard/terrestrial/adapter.py` and `diff.py` arrived with PR #1, and now have tests in `tests/test_terrestrial.py`. Both run on real 3075 and 3076. 3076 has 1,858 land-mobile notices, 114 of them from the five neighbours, all Russian: 103 new and 11 modified.

Still to do here: the filter using `settings.neighbour_adms` and distance to the Finnish border, the land/sea path information, reconciling totals against `pub_totals` and the Article 11 index, working out what `fxm_coord` adds for Sweden and Norway, and a hand check of 5 notices.
