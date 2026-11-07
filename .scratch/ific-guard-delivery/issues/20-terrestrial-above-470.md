# 20 Terrestrial criterion above 470 MHz

Status: ready-for-agent
Type: task
Size: S
Blocked by: none
Owns: `data/masks/field_strength.json`, the criterion lookup in `ific_guard/terrestrial/rule_field_strength.py`, their tests, `outputs/ific_3076/`

## Why

T/R 25-08 stops at 470 MHz, and most land-mobile records are above it. In `outputs/ific_3076/`, 6 of the 21 calculated notices (793-797 MHz) say no criterion is in the repository, so they aren't compared at all.

Jaakko (Traficom), recorded in ticket 02 Q10:

> For frequencies above 470 MHz, you can apply the same criteria as those specified in CEPT Recommendation T/R 25-08 for the frequency range above 400 MHz.

## Do

1. **Use the 400-470 MHz criterion above 470 MHz.** That's the Annex 1 indicative threshold `tr2508_a1_400_470`: 20 dB(uV/m) in 25 kHz, 10% time, 50% locations, receiver at 10 m, with the same bandwidth conversion. Don't copy the row. Record the extension in its own entry, citing both T/R 25-08 Annex 1 A1.2.3 (p.6) and Traficom's answer.
2. **Make each case say where its criterion comes from.** A case above 470 MHz must state that the value is applied there on Traficom's instruction, not by the Recommendation itself.
3. **Stop at the P.1546-6 range, 30-4000 MHz.** Above that, keep "no criterion". Traficom gave no upper limit, so state the cut-off as ours.
4. **Use Annex 1 only.** Annexes 3 and 4 need the Finnish system type and stay unused, as now. If "the criteria above 400 MHz" is meant to include them, that's a question for Traficom. Ask it rather than assume.
5. **Update the text that says no criterion exists above 470 MHz:**
   - `rule_field_strength.py:643` and `:763`
   - the README limitation and `docs/services-architecture.md` known gaps
   - `outputs/ific_3076/verification.md`
6. **Regenerate `outputs/ific_3076/`** and report before and after counts: assessment classes and severity.

## Acceptance

- No calculated 3076 notice is left without a criterion below 4000 MHz.
- A test covers 469, 470, 471 and 4001 MHz.
- Each new comparison names its basis.
- One recomputed case is checked by hand.
- `uv run pytest` passes.
