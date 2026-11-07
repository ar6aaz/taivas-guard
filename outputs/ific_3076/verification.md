# Terrestrial IFIC 3076 verification

These are possible concerns for expert review. None is confirmed interference or a breach of a regulation.

## Result

29 land-mobile notices passed the screen, all Russian. 21 have a calculated field strength and 8 could not be calculated.

| Assessment at the Finnish boundary | Notices |
|---|---:|
| Above the T/R 25-08 Annex 1 indicative threshold | 6 |
| Below it | 15. Four would cross it under an alternative: Kingisepp at 1% time, and three Ruskeala notices at 793-795 MHz if the 14 km land path were all sea |
| Not calculated: mobile station with no notified antenna height | 8 |

Severity: 1 high, 22 medium, 6 low. Five of the six above the threshold have `fxm_coord` rows saying coordination with FIN is completed, declared by the notifier, so they rank medium until checked against Traficom's records. The one high, 93075637 (KRL LYASKELYA, 152.9 MHz, 9.0 dB above), has no such row.

Above 470 MHz, where T/R 25-08 stops, the 400-470 MHz Annex 1 threshold is applied, as Traficom said (ticket 20). Six notices at 793-795 MHz are now compared. They have 5 and 10 MHz channels, so the bandwidth conversion lifts the threshold from 20 to 43.0 and 44.6 dB(uV/m), and all six are 7.9 to 63.1 dB below it. Before ticket 20 they had no criterion. Severity counts didn't change.

## Filter funnel

| Stage | Kept | Removed | Why |
|---|---:|---:|---|
| Published in 3076 (`fxm_pub_hist`) | 3,481 | | |
| Land mobile, classes FB and ML | 1,858 | 1,623 | FX 1,602, FL 9, LR 7, AL 4, AM 1. 176 are Russian FX, of which 27 are inside the distance screen |
| Configured administrations | 114 | 1,744 | SVN 1,127, KAZ 412, CHN 194, POL 9, F 2 |
| Within 300 km of the polyline | 29 | 85 | All Russian. No position was missing |
| Calculated | 21 | 8 | Flagged findings, not clear results |

Sweden, Estonia, Norway and Denmark notified no land-mobile station in 3076. Nothing of theirs was checked, so this is not a clean result. Broadcasting tables (`fmtv_*`) are out of scope and were not read.

The 300 km limit and its polyline are engineering approximations. Against the Natural Earth boundary used for calculation, the nearest removed notice is 376.4 km away, so the polyline's errors removed nothing in this issue. Seven Pskov notices passed at 247 km on the polyline but are about 301 km on Natural Earth. Polyline distances can be well off: Vyborg is 10.5 km on the polyline and 29.9 km on Natural Earth.

All 10 notices in 3076 naming FIN in `fxm_coord` are inside the funnel. Article 11 totals exceed notice rows by 29, which is unreconciled.

## Hand check

From source rows read directly from the database, with `field_strength` called directly: time 10%, rural receiver at 10 m.

| Notice | Source rows | Path | Direct | Finding |
|---|---|---|---:|---:|
| 93082383 KOSTOMUKSHA | 152.025 MHz, 15.9 dBW e.r.p., 28 m above ground | 31.531 km land | 25.00 | 25.00, 13.00 above 12 |
| 93075624 KINGISEPP | 425.825 MHz, 18.2 dBW e.r.p., 45 m above ground | 29.63 km land, 98.78 km cold sea | 11.11 | 11.11, 8.89 below 20 |
| 93082392 RUSKEALA | 793.5 MHz, 4.8 dBW e.r.p., 55 m above ground, `eff_hgt` 72 m at azimuth 310 | 13.94 km land, bearing 305.9 | 35.10 | 35.10, 7.91 below 43.01 (20 + 23.01 for a 5 MHz channel) |

Distances and the azimuth were recalculated separately from the coordinates. The land and sea split in the Kingisepp row comes from the rule, not from a separate calculation.

## Upstream engine cross-check

All 21 calculated findings were recalculated at their worst points with the upstream MATLAB code (`eeveetza/p1546` commit 0e42639, `P1546FieldStrMixed`) in GNU Octave 9.2.0, in the `gnuoctave/octave:9.2.0` Docker image under amd64 emulation. Inputs were the frequency, effective height, height above ground, e.r.p. and path segments in `cases.json`, at 10% time with a rural receiver at 10 m. The largest difference was 0.005 dB, which is the rounding of the two-decimal exported values.

This checks the Python port for these inputs only. It does not check the inputs, the land/sea split or the choice of worst point, because they come from this rule. The upstream run is not part of `pytest`. Reproduce it with `tools/crosscheck_p1546_octave.py`.

## Reproducibility

Exported at code commit 909f2de (ticket 20), recorded clean in the manifest. Generated on macOS from a fresh SQLite database with the shared `brific3075.zip` and `brific3076.zip` imported, because the Compose ports were in use. Those archives have the same hashes as the ITU downloads, so only `source_url` differs from the clean Compose run (`import:` rather than the ITU address).

| File | SHA-256 |
|---|---|
| cases.csv | 0f0ccec28922cd2d8597982ff00fe70d2f1e28d874cd7e3734d936f3157db1c9 |
| cases.json | e58fbce05a92b4c693f0ccd78b2994a0d4041b201eea33e9374377b3364ae642 |
| funnel.json | f4ab5f538bc7465a3b81ef2cfb4f6f9521cd153e97f6569fd38b2376b1a8a205 |

## Effect of protecting TX frequencies (ticket 16)

Traficom said "every TX is received in Finland" (Discord, 2026-11-07), so special-interest stations now match on RX and TX bands. No case was added or lost.

| Measure | RX only | RX and TX |
|---|---:|---:|
| Cases | 29 | 29 |
| Cases listing a special-interest station | 6 | 14 |
| Severity | 1 high, 20 medium, 8 low | 1 high, 22 medium, 6 low |

In 8 of the 14, no listed station has an RX match, so each listed field is at the station's site and is marked as not being the field at a receiver. Two cases rose from low to medium, LEN KINGISEPP at 424.075 and 424.475 MHz, only because a listed station now overlaps. Both remain 13.1 dB below the indicative threshold at the Finnish boundary, and the team decided a TX-only match may raise severity (2026-11-07).

## Not verified

- No expert or Traficom result has been compared with these findings.
- `fxm_coord`, `action`, `target`, `pwr_eiv` and `pwr_xyz` meanings are inferred, because the ITU ReadMe could not be read.
- T/R 25-08 does not say whether its values apply on the Russian border, and no bilateral agreement was checked.
