# IFIC Guard

IFIC Guard screens each ITU BR IFIC (space and terrestrial) for items that could matter to Finland, so a spectrum expert sees a short, ranked list with the evidence behind each item. Built for the Traficom challenge at Since AI 2026.

It raises **possible concerns for expert review**. It doesn't decide that a filing breaks a regulation, and it sends nothing to the ITU or to another administration.

## What it does

1. **Fetches issues.** A cron schedule downloads new Space and Terrestrial IFICs from the ITU. You can also upload an `.mdb` or a `.zip` containing one.
2. **Screens them.** Deterministic rules run against the Finnish reference data. The results are the same for the same input, and no language model takes part.
3. **Notifies.** One digest email per issue, listing new or changed cases.
4. **Supports review.** A web UI shows each case with its evidence. The expert accepts or rejects it and can ask for a draft letter, which is never sent.

| Rule | Domain | Raises a case when |
|---|---|---|
| `fin-named-by-br` | Space | The ITU's own data names Finland on a filing |
| `m4-no-space-to-earth` | Space | A satellite that can reach Finland transmits in a band where Regulation M4 gives no space-to-Earth allocation |
| `station-rx-overlap` | Space | A transmission overlaps the RX or TX band of a Traficom special-interest station |
| `pfd-gso-art21`, `pfd-gso-app5` | Space | Calculated GSO power flux-density against Article 21 limits and Appendix 5 coordination triggers |
| `pfd-ngso-art21`, `pfd-ngso-app5` | Space | The same for non-GSO, as an upper bound and a one-day flight of the notified orbit |
| `terrestrial-p1546` | Terrestrial | ITU-R P.1546-6 field strength on the Finnish boundary, for land-mobile notices from Russia, Sweden, Estonia, Norway and Denmark |
| `terrestrial-not-calculated` | Terrestrial | A nearby notice can't be calculated, for example with no antenna height |

## Cached analysis CLI

For repeated offline analysis, the optional cache workflow imports IFIC tables
once into DuckDB and screens receiver frequency overlaps with a sorted index.
Run `python -m ific_guard --help` or the installed `ific-cache` command. See
[the cache workflow guide](docs/scalable-ific.md) and
[measured timings](docs/performance.md). The operational APIs and
`python -m ific_guard.cli` admin commands continue to use the existing workflow.

## How to run it

You need Docker with Compose. The first full sync with `IFIC_MIN_ISSUE=3074` downloads 20 issues in about 3 minutes, then screens them in about 8 minutes, and uses about 2.3 GB of disk.

```sh
cp .env.example .env     # set IFIC_API_KEY: openssl rand -hex 32
docker compose up --build
```

- Sync API: <http://localhost:8001>
- Screening API: <http://localhost:8002>
- Notifier API: <http://localhost:8003>

A new install syncs at once, then follows `IFIC_SYNC_CRON`. Keep `IFIC_EMAIL_BACKEND` at `console` or `file` unless you mean to send real email. The `file` backend writes to `data/outbox/` in the `ific-data` volume.

To load a file yourself:

```sh
curl -X POST -H "X-API-Key: $IFIC_API_KEY" -F file=@ific3074.zip http://localhost:8001/issues/upload
```

For the review UI (the services must be running):

```sh
cd frontend && npm install && npm run dev    # http://localhost:5173
```

To work on the Python code, with Python 3.11 to 3.13:

```sh
uv sync
uv run pytest
IFIC_SHARED_DATA=/path/to/ific-files uv run pytest    # adds tests that read the real issues
```

To export one issue for evaluation:

```sh
python -m ific_guard.cli export --domain space --issue 3074 --out outputs/ific_3074
python -m ific_guard.cli export --domain terrestrial --issue 3076 --out outputs/ific_3076
```

[docs/demo.md](docs/demo.md) is the rehearsed demo script.

## Inputs

| Input | Source | Where |
|---|---|---|
| Space IFIC `.mdb` | ITU, or upload | Downloaded at run time, not in the repository |
| Terrestrial IFIC `.mdb` | ITU, or upload | Downloaded at run time, not in the repository |
| Frequency allocation table (Regulation M4) | Traficom open-data API | `data/national/` |
| Special-interest stations (synthetic) | Traficom | `docs/challenge/` |
| Article 21 and Appendix 5 limit masks | ITU Radio Regulations | `data/masks/limit_masks.json`, with a citation on each row |
| T/R 25-08 indicative thresholds | CEPT | `data/masks/field_strength.json` |
| Finnish boundary and land mask | Natural Earth | `data/masks/` |

## Outputs

| Output | What it is |
|---|---|
| [`outputs/ific_3074/`](outputs/ific_3074) | Space 3074: 182 cases (30 high, 123 medium, 29 low) |
| [`outputs/ific_3076/`](outputs/ific_3076) | Terrestrial 3076: 29 cases (1 high, 22 medium, 6 low) |
| [`outputs/hand-check.md`](outputs/hand-check.md) | Independent recheck of a sample, with the false positives and misses we could measure |
| [`outputs/neighbour-trends.md`](outputs/neighbour-trends.md) | Counts of neighbouring notices across Terrestrial 3074-3083 |
| Review cases | Stored in the database, served by the Screening API and the UI |
| Digest email | One per issue |
| Draft letter | `GET /cases/{id}/draft`; marked as a draft and never sent |

Each export folder holds `cases.csv`, `cases.json` (with the full evidence and assumptions), `manifest.json` (source hash, code hashes, configuration) and `verification.md`. Terrestrial also has `funnel.json`, which shows how many notices each filter removed. See [`outputs/README.md`](outputs/README.md).

Traficom accepts this: they prefer Xls/csv or doc/pdf for quick evaluation, accept any open machine-readable format, prefer JSON over MDB, and named no mandatory fields.

## What was verified

From an empty database with `docker compose up --build`:

- The scheduled first sync finished `ok`, with all 20 issues (Space and Terrestrial 3074-3083) screened and a digest written for each. No real email was sent.
- A second empty database, loaded by upload (`ific3074.zip`, `brific3075.mdb`, `brific3076.mdb`), gave the same cases as the download. The only differences were the source URL, and the hash and size of whichever file was uploaded.
- A demo was rehearsed from a clean start, including a draft for each of the 224 cases in 3083. This ran just before the TX protection was merged, which changes station links but not the steps.

The rehearsals found faults that the unit tests had not, all now fixed:

- every terrestrial issue failed in the container
- one bad date cell lost all of Space 3077
- draft letters crashed on most pfd cases
- letters described an upper bound as an exceedance

The hand check recomputed 13 pfd results from raw rows with no difference above 0.0001 dB. All 21 P.1546 values match the upstream reference code in Octave to within 0.006 dB. It also found no in-scope misses in its sweeps. See [`outputs/hand-check.md`](outputs/hand-check.md).

**Accuracy against expert judgement has not been measured.** No result has been compared with Traficom's own assessment.

## Known limitations

### False positives

- **Worst-case gain.** Every pfd result applies the maximum notified gain towards Finland, as Traficom said to assume (beams "are not relevant", Discord, 2026-11-07). So "exceeds" always means under worst-case assumptions. `s_beam` holds no boresight position for any of the 2,304 rows in 3074, so we can't say how many of the high pfd cases a real beam would reach.
- **Frequency-only rules.** `m4-no-space-to-earth` and `station-rx-overlap` ignore power, antenna direction and station location. A slight edge overlap counts. They over-flag by design. Protecting TX as well as RX, as Traficom asked, adds no cases but 75% more station links on 3074.
- **Filings around the Moon.** In 3074, six networks (LUNAR-R1 to R5, USASAT-MK1) have an orbit referred to the Moon. They still get M4 and station cases as if they were Earth satellites. The pfd rule sets them aside as not evaluated.
- **Synthetic stations.** Traficom's station list is synthetic, so station cases show that the rule works, not real interference.

### Things not evaluated

- **5,429 non-GSO records in 3074 have no calculation** because their masks need parameters X or Y, which the IFIC data can't supply (5,398 and 31). They are counted, and shown for expert review.
- **Only API/A and CR/C space filings are screened.** Notification-stage (`N`) filings aren't, which includes three 3074 notices naming Finland: 125500060, 125500061 and 125500066. AP30, AP30A and AP30B aren't screened, and 3074 has no rows to test them with.
- **Only Space database version 10 screens.** Issues in versions 8, 7 and 5 are rejected with a clear error, so "all existing issues" isn't met for older issues. Space discovery walks back to 1998, and 3074-3083 are all version 10.
- **Terrestrial covers land mobile only.** Other services and terrain (`pathinfo = 1`) aren't calculated.
- **Article 22 epfd isn't calculated.** Neither is the sum of several satellites at one point.
- **Finnish satellites aren't protected.** The brief also asks about systems that could interfere with Finnish satellites, and no rule does this yet.
- **No space-side trends.** `outputs/neighbour-trends.md` covers terrestrial only.

### Assumptions an expert should check

- **`pwr_ds_max` in dBW/Hz** rests on a secondary note. The primary ITU Space Preface could not be retrieved. Each pfd case states this.
- **`pep_max` caps power in the reference bandwidth**, treated as dBW. In 3074 it lowers the worst margin of 74 of 1,234 records and raises none.
- **Inferred field meanings.** `orbit_lnk`, `long_asc`, `phase_ang`, `fxm_coord`, `action`, `target`, `pwr_eiv` and `pwr_xyz` are inferred from the data, because the ITU ReadMe could not be read.
- **Non-GSO flights** are two-body with no J2, over one day, for one satellite per orbit shape from an assumed start. They can miss the worst case. The upper bound covers the notified altitudes for any node, phase or epoch, but it may describe a state the satellite never occupies.
- **T/R 25-08 thresholds are indicative, and all 15 comparisons in 3076 are provisional.** The document doesn't say whether they apply on the Russian border, and no bilateral agreement was checked. Above 470 MHz the case currently says no criterion is available. Traficom has since said to apply the T/R 25-08 criteria for above 400 MHz there, which is ticket 20.
- **The 300 km terrestrial screen and the Finnish border polyline are engineering approximations.** On 3076 the polyline dropped nothing within 300 km of the Natural Earth boundary and kept 7 notices at about 306 km.
- **Draft letters say "limit" for every comparison**, including Appendix 5 triggers and T/R 25-08 thresholds. An expert should correct the term when editing a draft.
- **Comment deadlines are estimates**: publication date plus four months.

### Data and operations

- **Article 11 totals don't reconcile with notice rows**: 106 unreconciled in 3075 and 29 in 3076.
- **Invalid dates read as null.** Space 3077 has 22 such cells in `grp`; each logs a warning.
- **Severity scoring.** The configured boosts for stations and passive bands don't fire on the evaluated issues, so they don't affect ranking there.
- **The MDB parser `access-parser` is a young library**, and row counts haven't been compared with `mdbtools`.
- **A first sync sends one digest per issue**, so a low `IFIC_MIN_ISSUE` sends a burst of them.
- **Not tested:** real SMTP (mocked only), the language-model path with a real model, and ITU's own PFD software as a cross-check.
- **Security is minimal.** The API key is a basic guard and the ports bind to localhost, so this isn't ready to face the internet.
- **Schema changes** use `create_all`, with no migrations.
- **The normalised DuckDB store was deferred.** Rules read the `.mdb` directly. See [ARCHITECTURE.md](ARCHITECTURE.md) section 4.2.

## More documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): pipeline, engines and principles
- [docs/services-architecture.md](docs/services-architecture.md): services, data flow and current status
- [docs/demo.md](docs/demo.md): demo script
- [frontend/README.md](frontend/README.md): the review UI
