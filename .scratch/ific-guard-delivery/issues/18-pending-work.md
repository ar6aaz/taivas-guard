# 18 Pending work before submission

Status: ready-for-human
Type: task
Size: L
Blocked by: none
Owner: the coordinator

Everything still open as of 2026-11-07, checked against `integration/wave-3` at `59fc0d0` and the brief. This is a checklist: split an item into its own ticket when someone picks it up. Tickets 16 (TX frequencies, do now) and 17 (AP30, later) are tracked separately.

## Traficom's answers not yet applied

Answers came on Discord, 2026-11-07 8:39-8:55, from Pekka, Otto and the Traficom account.

- [x] **Record the answers in ticket 02,** with who answered and when. Its answer section is empty.
- [ ] **Search CEPT sources for pfd concerns.** Traficom: the RR table is "the main source", but "CEPT for instance can elaborate on the defined limits in Europe as you might want to focus on concerns instead of just hard limits". `limit_masks.json` cites only the RR. The brief lists `https://docdb.cept.org/implementation/overview`. Add only values read at the source.
- [ ] **Remove GIMS as a future step.** Docs done; the `rule_pfd.py` docstring remains. Traficom: beams "are not relevant" and "the worst case scenario should always be assumed". Update `ARCHITECTURE.md:235` and `:243`, `docs/services-architecture.md:238`, and `rule_pfd.py:9`, and cite Traficom for worst case. Keep "exceeds under worst-case assumptions". The `:238` row also still says there is no pfd check, which ticket 07 fixed.
- [ ] **State that the 3 dB watch band is our choice.** `WATCH_DB` comes from `ARCHITECTURE.md` section 4.7, not from Traficom.

## Gaps against the brief

- [ ] **Finnish satellites are not protected.** The brief asks about systems "which may interfere Finnish satellites already in use", and no rule does this. Build a dated list of Finnish filings from ITU Space Explorer in `data/national/`, then flag new filings close in frequency and orbit, taking the separation from RR Appendix 5. Getting the data may need someone to use the site or an ITU account. Also check Space Explorer's earth-station and radio-astronomy sections for Finnish entries.
- [ ] **Older issues are rejected.** Only database version 10 screens; versions 8, 7 and 5 (samples 2925, 2809, 2559) return an error, so "all existing IFICs" is not met. Needs a decision: build them, or declare the limit.
- [ ] **5,429 records are unevaluated,** because we do not know which columns supply the X and Y mask parameters. Each one is a possible missed case. Ask Traficom, or find it in the Space Preface.
- [ ] **No space-side trends.** The brief mentions "trends and planned usage of new satellites systems"; `trends.py` covers terrestrial only. Decide whether to add a small version.
- [ ] **pfd results never count as special interest.** Now ticket 19.
- [ ] **No ITU tool cross-check is recorded.** `ARCHITECTURE.md:152` plans to compare some 3074 networks with ITU's PFD GSO software, and the brief names GIBC. Nothing in `outputs/` records this. Run it if the tools are available, or say it was not done.
- [ ] **M15 is unused.** `ARCHITECTURE.md:111` plans low-severity notes only, and the brief calls it less relevant. Decide, and state it either way.

## Integration and delivery

- [ ] **Regenerate `frontend/src/data/` samples.** They predate ticket 16.
- [ ] **Push `agent/12-drafts-ui` (`1422f80`), then merge it.** It exists only in this Codespace and connects the case page to the draft API. On `wave-3` the UI still keeps letters in the browser (`http.ts:260`), and ticket 14's demo needs a draft letter. Expect frontend conflicts.
- [ ] **Compare `01d4341` with `wave-3`'s terrestrial filter.** It is also local only, on `agent/08-terrestrial`, `agent/10-terrestrial-findings` and `agent/11-trends`. `wave-3` uses the version in `adapter.py`, and lacks this commit's `filter.py`, `geography.py` and `summary.py`. Keep anything better, then drop it.
- [ ] **Check local worktrees for lost work.** `wt-07`, `wt-11` and `wt-15` have uncommitted files on commits older than their remote branches. Compare them before deleting.
- [x] **Write the root `README.md`.** There is none, and the brief asks for input and output information.
- [x] **Merge to `main`.** It is 59 commits behind `wave-3`. Open a PR from `wave-3` once it is ready, and close PR #1, whose content is already included.
- [x] **Delete `origin/agent/12-review-ui`.** Its two commits are already in `wave-3` under other hashes.
- [ ] **Fix Git access in the Codespace.** `GITHUB_TOKEN` cannot see the repository, so plain `git fetch` returns 403. Use the stored `ar6aaz` login.
- [ ] **Tidy the tracker.** Tickets 04-11, 13 and 15 still say `ready-for-agent` although the map marks them done, and the map still says wave 3 is not pushed.

## Waiting on Traficom

Ask these first, because they change what is submitted:

- [x] **Output format:** answered. Xls/csv or doc/pdf preferred, any open machine-readable format is fine, JSON preferred over MDB, and no mandatory fields named.
- [ ] **Terrestrial calculation points:** the Finnish border or coast, or the Excel station locations. Passed to Jaakko, no reply seen. `rule_field_strength.py:84` uses boundary and coast points, stated as an assumption.
- [ ] **Terrestrial limits:** trigger values and time percentage per neighbour, the Russian border and bilateral agreements (Q6, Q8, Q9). Above 470 MHz is answered (Q10): ticket 20.
- [x] **Demo video:** yes, "as you see best".
- [ ] **Over-flagging:** how much is acceptable (Q4)? Bring ticket 16's counts: no new cases, but 75% more station links on 3074.

Useful, but work can continue without them:

- [ ] Whether `fxm_coord` matters for Sweden, Norway and Denmark (Q7).
- [ ] Spreadsheet details: ERP or EIRP and the 67 implausible power rows, `E`, `MVV`, the blank class, receive gain, and the bandwidth mismatch (Q11-14, Q16).
- [ ] Priorities and calibration: bands watched, BR-listed or missed cases, the comment window, and past examples (Q2, Q3, Q5, Q17).
- [ ] The items in `map.md` added for ticket 02: notifier-declared FIN coordination, mobile station height, the `pwr_ds_max` unit, and the `orbit_lnk`, `long_asc` and `phase_ang` meanings.

Answered, so no need to ask again: Q15 (TX frequencies, ticket 16), worst-case screening, RR Table 21-4 as the main limit, and broadcast coverage.
