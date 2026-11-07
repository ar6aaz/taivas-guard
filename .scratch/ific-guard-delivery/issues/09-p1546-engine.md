# 09 P.1546 engine and T/R 25-08 triggers

Status: ready-for-agent
Type: task
Size: L
Blocked by: none
Owns: `ific_guard/engines/p1546.py`, `data/masks/field_strength.json`, `tests/test_p1546.py`, Octave lines in `Dockerfile`, a dependency group in `pyproject.toml` if needed

## Goal

A deterministic field-strength calculation using ITU-R P.1546, and the coordination trigger values as cited data. It takes plain numbers and reads no IFIC, so it can be built alone.

## Do

1. Evaluate the implementation in ARCHITECTURE.md section 4.6: the OFCOM reference `eeveetza/p1546` (Zenodo DOI 10.5281/zenodo.6206015) through `oct2py`. Check its licence and whether Octave installs in the demo environment. Decide, and record why. A different verified implementation is fine if it passes the same validation.
2. Wrap it in one function with explicit inputs: frequency, time percentage, effective transmitter height, receiver height, clutter height, land/sea path segments, notified power. Record the implementation version in the output.
3. Create `data/masks/field_strength.json` from ECC T/R 25-08. **Take values from the document and cite each one; do not write them from memory.** Cover Finland's borders with Russia, Sweden, Estonia and Norway, and Denmark if T/R 25-08 gives values for it. Use ticket 02's answer on time percentage if available.
4. If the source is unavailable, mark the masks as test-only, set this ticket to `needs-info`, and say so in your report.

## Acceptance

- The implementation's own validation profiles run as a test, and the tolerance is stated.
- A hand-checkable case: one land path and one sea path.
- Inputs outside the model's valid range (frequency, distance, height) raise a clear error rather than returning a number.
- A single call's run time is measured and reported, since ticket 10 runs many. If it is slow, suggest batching.

## Not in scope

Reading IFIC data, terrain data (`pathinfo = 1`), and writing cases.
