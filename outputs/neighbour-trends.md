# Neighbour trends

Descriptive counts across Terrestrial IFIC 3074-3083, from the clean run. They make no predictions. In the commands below, give the path to each file.

- Assumption: The 300 km distance screen is an engineering approximation, not a regulatory value.
- Assumption: The Finnish border polyline used by the adapter is an engineering approximation, not a legal border or coastline.
- Assumption: Only the configured administrations were included: RUS, S, EST, NOR, DNK.
- Assumption: With only the compared issues, observations are weak evidence and make no predictions.
- Assumption: Published Article 11 totals do not fully reconcile to notice rows, so counts may not match them (see TerrestrialIssue.article11_reconciliation).
- Assumption: The meanings of fxm_coord, action and target are inferred from the data, not confirmed against the ITU ReadMe.

## administration-activity
Neighbour notice activity across 10 issues: RUS 3074:43, 3075:90, 3076:114, 3077:98, 3078:100, 3079:105, 3080:39, 3081:57, 3082:71, 3083:27; S 3074:0, 3075:0, 3076:0, 3077:0, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:0; EST 3074:0, 3075:24, 3076:0, 3077:19, 3078:0, 3079:0, 3080:0, 3081:0, 3082:19, 3083:70; NOR 3074:0, 3075:0, 3076:0, 3077:0, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:0; DNK 3074:0, 3075:0, 3076:0, 3077:0, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:0. This is descriptive evidence from the compared issues, not a prediction.
Reproduce: `uv run python -m ific_guard.terrestrial.trends --observation administration-activity --issues brific3074.mdb brific3075.mdb brific3076.mdb brific3077.mdb brific3078.mdb brific3079.mdb brific3080.mdb brific3081.mdb brific3082.mdb brific3083.mdb`

## screened-activity
Notices passing the configured distance screen (default engineering limit 300 km): RUS 3074:25, 3075:13, 3076:29, 3077:18, 3078:11, 3079:11, 3080:8, 3081:15, 3082:9, 3083:10; S 3074:0, 3075:0, 3076:0, 3077:0, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:0; EST 3074:0, 3075:24, 3076:0, 3077:19, 3078:0, 3079:0, 3080:0, 3081:0, 3082:19, 3083:70; NOR 3074:0, 3075:0, 3076:0, 3077:0, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:0; DNK 3074:0, 3075:0, 3076:0, 3077:0, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:0. This is descriptive evidence from the compared issues, not a prediction.
Reproduce: `uv run python -m ific_guard.terrestrial.trends --observation screened-activity --issues brific3074.mdb brific3075.mdb brific3076.mdb brific3077.mdb brific3078.mdb brific3079.mdb brific3080.mdb brific3081.mdb brific3082.mdb brific3083.mdb`

## frequency-band
Screened notice frequency-band counts for issues 3074, 3075, 3076, 3077, 3078, 3079, 3080, 3081, 3082, 3083; 100-200 MHz 3074:9, 3075:37, 3076:16, 3077:12, 3078:7, 3079:0, 3080:0, 3081:0, 3082:1, 3083:5; 400-500 MHz 3074:7, 3075:0, 3076:7, 3077:0, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:0; 700-800 MHz 3074:9, 3075:0, 3076:6, 3077:23, 3078:4, 3079:9, 3080:8, 3081:14, 3082:27, 3083:43; 800-900 MHz 3074:0, 3075:0, 3076:0, 3077:2, 3078:0, 3079:2, 3080:0, 3081:1, 3082:0, 3083:32. Categories use 100 MHz bins and are descriptive, not predictive.
Reproduce: `uv run python -m ific_guard.terrestrial.trends --observation frequency-band --issues brific3074.mdb brific3075.mdb brific3076.mdb brific3077.mdb brific3078.mdb brific3079.mdb brific3080.mdb brific3081.mdb brific3082.mdb brific3083.mdb`

## station-class
Screened station-class counts for issues 3074, 3075, 3076, 3077, 3078, 3079, 3080, 3081, 3082, 3083: FB 3074:19, 3075:21, 3076:21, 3077:33, 3078:9, 3079:11, 3080:8, 3081:15, 3082:27, 3083:78; ML 3074:6, 3075:16, 3076:8, 3077:4, 3078:2, 3079:0, 3080:0, 3081:0, 3082:1, 3083:2. Station classes are retained as notified.
Reproduce: `uv run python -m ific_guard.terrestrial.trends --observation station-class --issues brific3074.mdb brific3075.mdb brific3076.mdb brific3077.mdb brific3078.mdb brific3079.mdb brific3080.mdb brific3081.mdb brific3082.mdb brific3083.mdb`

## bandwidth
Screened bandwidth categories for issues 3074, 3075, 3076, 3077, 3078, 3079, 3080, 3081, 3082, 3083: 12.5-25 kHz 3074:9, 3075:29, 3076:16, 3077:3, 3078:7, 3079:0, 3080:0, 3081:0, 3082:1, 3083:0; <=12.5 kHz 3074:7, 3075:8, 3076:7, 3077:9, 3078:0, 3079:0, 3080:0, 3081:0, 3082:0, 3083:5; >25 kHz 3074:9, 3075:0, 3076:6, 3077:25, 3078:4, 3079:11, 3080:8, 3081:15, 3082:27, 3083:75. Bins are <=12.5 kHz, 12.5-25 kHz and >25 kHz.
Reproduce: `uv run python -m ific_guard.terrestrial.trends --observation bandwidth --issues brific3074.mdb brific3075.mdb brific3076.mdb brific3077.mdb brific3078.mdb brific3079.mdb brific3080.mdb brific3081.mdb brific3082.mdb brific3083.mdb`
