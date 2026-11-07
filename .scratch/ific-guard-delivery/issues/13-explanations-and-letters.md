# 13 Explanations and draft letters

Status: ready-for-agent
Type: task
Size: M
Blocked by: none
Owns: `ific_guard/llm/`, `data/refdocs/`, a read endpoint `GET /cases/{id}/draft` in `ific_guard/screening/app.py`, `tests/test_llm.py`

## Goal

Explain a finished case in plain language, and draft correspondence to the notifying administration for expert review, as in ARCHITECTURE.md section 4.8.

## Do

1. **Deterministic template first.** `draft(case)` fills a letter from case data alone: notice ID, administration, provision, bands, calculated values and deadline. This must work with no API key, and it is the fallback.
2. Add an LLM layer behind one interface. It may only rewrite and explain, using the case's data and retrieved passages.
3. Build a small retrieval index by clause over documents you can obtain: Radio Regulations, M4 (`files/finland-regulations.pdf`), T/R 25-08. Record which you could not get.
4. **Guardrail:** after generation, check that every number and citation in the text appears in the case or in the retrieved passages. On failure, return the deterministic template and record why.
5. Mark every draft as requiring expert review. Nothing here sends a letter or email, and LLM text stays out of the digest.
6. Build against the `details` contract in `map.md`, with sample cases until tickets 07 and 10 merge.
7. Before writing Claude API code, load the `claude-api` skill for current model IDs and SDK usage. Read the key from the environment.

## Acceptance

- Tests with a fake model: an invented number or citation is rejected, and the fallback is returned.
- Drafts for 3 real cases from IFIC 3083 in your report: one from each rule, plus one with a calculation if ticket 07 has merged.
- A screening-only case's draft must not claim a limit was exceeded.
- The full test suite passes with no API key and no network.

## Not in scope

Sending letters, submitting through SpaceCom, and any LLM role in relevance or severity.
