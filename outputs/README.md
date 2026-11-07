# Evaluation exports

`ific_3074/` contains Space IFIC 3074 screening results, and `ific_3076/`
the Terrestrial IFIC 3076 land-mobile results. These are possible concerns requiring
expert review, not confirmed interference. Read `manifest.json` for the source hash,
code hashes, configuration and coverage gaps.

After importing an archive, reproduce with:

```sh
python -m ific_guard.cli export --domain space --issue 3074 --out outputs/ific_3074
python -m ific_guard.cli export --domain terrestrial --issue 3076 --out outputs/ific_3076
```

Import Terrestrial 3075 as well. Without it, the 3076 export still runs and says on
every finding that changes were not checked against the previous issue.

The command selects the latest imported revision of that issue. Pass
`--sha256 <archive-hash>` to pin a revision. It evaluates the source again without
modifying review decisions or sending notifications. This prevents later issues'
mutable review cases from leaking into historical evaluation files.

- `cases.csv`: ranked, flat results; notice IDs are separated by semicolons.
- `cases.json`: the same results with full evidence and assumptions.
- `manifest.json`: provenance, configuration, executed rules, runtime and output hashes.
- `funnel.json` (terrestrial): how many notices each filter removed and why, and which
  were not calculated.

Ranking is severity, estimated deadline, severity score, margin, rule ID, then
notice-family ID.
Case files are deterministic for fixed inputs and code. Manifest generation time
and measured runtime vary. Traficom accepts CSV, and any open machine-readable format; JSON is preferred over MDB, and no fields are mandatory (ticket 02).

`hand-check.md` rechecks a sample of each rule from raw rows, and `neighbour-trends.md` counts neighbouring notices across issues 3074-3083.

Raw ITU archives and databases remain in ignored `data/shared/`, not in these outputs.
