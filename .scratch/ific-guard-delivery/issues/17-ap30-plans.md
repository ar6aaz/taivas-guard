# 17 AP30, AP30A and AP30B plan screening

Status: needs-info
Type: task
Size: L
Blocked by: none
Priority: Later. Pick up once the questions below have answers. Does not block ticket 14; the gap stays declared in exports.

## Why

The brief names AP30, AP30A and AP30B alongside API/A and CR/C, but nothing screens them. `export.py` reports `ap30_coverage: "not implemented"`, and the gap is listed in `docs/services-architecture.md` section 9.

These are the Radio Regulations plan appendices: each country holds reserved orbital positions, frequencies and service areas. Traficom would look for proposed plan changes that affect Finland's reserved capacity.

- **AP30:** broadcasting satellite downlinks
- **AP30A:** feeder links for those satellites
- **AP30B:** fixed satellite service plan

Take band edges and protection criteria from RR Volume 2 (sources in `data/masks/limit_masks.json`) and cite them. `data/refdocs/passages.jsonl` has no AP30 text yet, and these plans use their own criteria, so Article 21 pfd alone is not enough.

## What we know

- **3074 has nothing to test against.** Its `notice` table has 196 rows, with provisions 9.6, 9.1/IA, 11.2 and RS49 only. `ap30b_ref_agg`, `ap30b_ref_se`, `ap30b_tr_res` and `plan_pub` have zero rows (`outputs/ific_3074/verification.md`). That is not proof the wider publication has none, and 3083's plan tables are empty too.
- **Traficom's pointer.** Asked for Finland's plan positions, Otto (Traficom, Discord, 2026-11-07 8:39) pointed to [ITU Space Explorer](https://www.itu.int/itu-r/space/apps/public/spaceexplorer/networks-explorer): filter the notifying administration to Finland and choose geostationary filings. He noted these are frequency and orbital information, and that one filing can contain multiple satellites.
- **That does not settle it.** The answer covers Finnish filings in general. It does not say they match the plan positions, or give service areas.

## Questions to answer first

1. Do the Finnish filings in Space Explorer include the AP30 plan positions, and where are their service areas? (Ask Traficom, ticket 02.)
2. Which IFIC issue or ITU file contains AP30-family data we can test on? Check whether the plan data ships in a separate database from the free download.
3. Which protection criteria should decide that a change affects Finland?

## Do, once answered

1. Parse AP30-family notices from an issue that contains them, and add the sections to scope in `ific_guard/screening/space.py`.
2. Build Finland's plan reference from a dated, recorded source.
3. Add a rule that flags changes affecting Finland, with cited criteria and the `details` contract in `map.md`.
4. Replace the declared gap in `export.py` and the docs with what is actually covered.

## Acceptance

- Tested on a real issue containing AP30-family notices, spot-checking 3 cases by hand. Do not ship this on synthetic data alone.
- Every limit or criterion carries a citation to a source that was read.

## Not in scope

The Finnish satellite list and the rule for new filings near Finnish satellites. They do not need AP30 data, and are listed in ticket 18.
