"""Drafts and their guardrail. Every test runs without an API key and without a network."""
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ific_guard.db import session_scope
from ific_guard.llm import ClaudeModel, ModelError, ModelOutput, configured_model, draft, explain, generate
from ific_guard.llm.drafts import build_prompt, evidence_for
from ific_guard.llm.guardrail import Evidence, check_text, citations_in, dates_in, numbers_in
from ific_guard.llm.retrieval import Passage, load_index, retrieve, wanted_provisions
from ific_guard.llm.template import CaseView, margin_of, draft_letter_body, explain_case, is_calculated, levels_of, review_notes
from ific_guard.models import Case, Event, Notification
from ific_guard.screening.app import app as screening_app

KEY = {"X-API-Key": "test-key"}
NOTICE = "125520065"
SECRET = "sk-ant-test-not-a-real-key"


def make_case(rule_id="fin-named-by-br", **details) -> Case:
    return Case(
        id=7,
        fingerprint="fp",
        domain="space",
        rule_id=rule_id,
        severity="high",
        title="BR names Finland in TESTSAT (ARS) under 9.14",
        adm="ARS",
        network="TESTSAT",
        notice_ids=[NOTICE],
        details=details,
        material_hash="m",
        first_issue=3083,
        last_issue=3083,
        changed_artifact_id=1,
        source_sha256="ab12" * 16,
        comment_deadline=date(2027, 2, 27),
        review_status="pending",
    )


def named_case() -> Case:
    return make_case(provisions=["9.14"], group_ids=[125780383], sections=["9.6"], basis="FIN appears in provn.")


def m4_case() -> Case:
    band = {
        "low_mhz": 13400.0,
        "high_mhz": 13750.0,
        "m4_primary_services": "EARTH EXPLORATION-SATELLITE; RADIOLOCATION",
        "finnish_use": "(SRD) Equipment for detecting movement and for alert",
        "passive": False,
        "m4_table_pages": "170-170",
        "group_ids": [125735144],
    }
    return make_case(
        "m4-no-space-to-earth",
        method="screening",
        bands=[band],
        sections=["9.1/IA"],
        visibility=["GSO at 14.7 deg, inside the arc visible from Finland"],
        assumptions=["Beam coverage over Finland is not checked.", "No power or pfd calculation was done."],
    )


def calculated_case(margin_db=4.5, value_db=-124.5, limit_db=-129.0) -> Case:
    return make_case(
        "pfd-gso",
        method="pfd_gso",
        calc_inputs={"p_dens_max_dbw_hz": -40.0},
        assumptions=["Free-space propagation."],
        worst_point={"lat": 68.0, "lon": 26.0, "angle_deg": 8.5},
        value_db=value_db,
        limit_db=limit_db,
        margin_db=margin_db,
        mask_id="rr-t21-4-sample",
        citations=[{"doc": "RR", "clause": "Art. 21, Table 21-4", "page_or_url": "sample"}],
        sections=["9.6"],
        provisions=["9.14"],
    )


@dataclass
class FakeModel:
    """Returns fixed text and records its prompts."""

    letter_body: str = "A clear rewrite naming notice 125520065."
    explanation: str = "Raised because the Bureau names Finland under No. 9.14."
    name: str = "fake-model"
    error: Exception | None = None
    prompts: list[str] = field(default_factory=list)

    def rewrite(self, prompt: str) -> ModelOutput:
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return ModelOutput(explanation=self.explanation, letter_body=self.letter_body)


def assert_is_the_template(result, case):
    assert result.source == "template"
    view = CaseView.of(case)
    assert draft_letter_body(view) in result.letter
    assert result.explanation == explain_case(view)


# --- the deterministic template


def test_template_carries_the_case_data_and_needs_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = draft(calculated_case())
    assert result.source == "template" and result.fallback_reason is None
    for value in (NOTICE, "ARS", "TESTSAT", "3083", "9.14", "2027-02-27", "-124.5", "-129.0", "4.5", "rr-t21-4-sample", "Free-space propagation."):
        assert value in result.letter, value
    assert "Art. 21, Table 21-4" in result.letter


def test_template_lists_bands_and_m4_pages():
    result = draft(m4_case())
    assert "13400-13750 MHz" in result.letter and "170-170" in result.letter
    assert "M4" in result.letter and "Beam coverage over Finland is not checked." in result.letter


def test_every_draft_is_marked_for_expert_review_and_never_sent():
    for result in (draft(named_case()), draft(m4_case()), draft(calculated_case()), generate(named_case(), FakeModel())):
        assert result.requires_expert_review is True and result.sent is False
        assert result.status == "draft_requires_expert_review"
        assert result.letter.startswith("DRAFT FOR EXPERT REVIEW. NOT SENT.")


@pytest.mark.parametrize("make", [named_case, m4_case, calculated_case, lambda: calculated_case(-2.0, -131.0, -129.0)])
def test_the_template_itself_passes_the_guardrail(make):
    """So the template cannot add a number, citation or claim of its own, and is a safe fallback."""
    view = CaseView.of(make())
    passages, _ = retrieve(view.domain, view.details)
    evidence = evidence_for(view, passages)
    margin = view.details.get("margin_db")
    calculated = is_calculated(view.details)
    for text in (draft_letter_body(view), explain_case(view), *review_notes(view, passages, [])):
        assert check_text(text, evidence, calculated, margin, levels_of(view.details)) == [], text


def test_template_for_calculation_below_the_limit_does_not_say_exceeded():
    result = draft(calculated_case(-2.0, -131.0, -129.0))
    assert "below the limit" in result.letter and "exceeds" not in result.letter
    assert result.possible_concern_only is True


def test_template_reports_exceeded_only_with_a_positive_margin():
    result = draft(calculated_case())
    assert "exceeds the limit" in result.letter and result.possible_concern_only is False


@pytest.mark.parametrize(
    "case",
    [
        named_case(),
        m4_case(),
        make_case(method="pfd_gso", provisions=["9.14"]),  # method but no results
        make_case(method="pfd_gso", value_db=1.0, limit_db=0.0),  # incomplete results
        make_case(method="screening", value_db=1.0, limit_db=0.0, margin_db=1.0),  # results but screening
        make_case(method="made-up", value_db=1.0, limit_db=0.0, margin_db=1.0),  # unknown method
    ],
)
def test_a_case_without_full_results_is_only_a_possible_concern(case):
    result = draft(case)
    assert result.method == "screening" and result.possible_concern_only is True
    assert "possible concern" in result.letter.lower()
    for claim in ("exceed", "violat", "breach", "non-complian"):
        assert claim not in result.letter.lower() and claim not in result.explanation.lower()


def test_explain_is_plain_and_states_no_calculation():
    text = explain(m4_case())
    assert "13400-13750 MHz" in text and "No calculation has been made" in text


# --- the guardrail, through a fake model


def test_clean_rewrite_is_used():
    model = FakeModel()
    result = generate(named_case(), model)
    assert result.source == "llm" and result.model == "fake-model" and result.fallback_reason is None
    assert model.letter_body in result.letter and result.explanation == model.explanation


def test_rewrite_cannot_remove_the_banner_or_the_reference_data():
    result = generate(named_case(), FakeModel(letter_body="Plain text."))
    assert result.letter.startswith("DRAFT FOR EXPERT REVIEW.") and f"Notice ID(s): {NOTICE}" in result.letter


@pytest.mark.parametrize(
    "bad_text, kind, value",
    [
        ("The filing has a margin of 57.3 units.", "number", "57.3"),
        ("Notice 125520066 is affected.", "number", "125520066"),
        ("Seventy beams are affected.", "number", "70"),
        ("Respond by 28 February 2027.", "date", "2027-02-28"),
        ("Respond by 2027-03-27.", "date", "2027-03-27"),
        ("Respond by March 2027.", "date", "2027-03"),
        ("This engages No. 9.99.", "citation", "rr:9.99"),
        ("See Article 22 of the regulations.", "citation", "article:22"),
        ("See Table 21-4.", "citation", "table:21-4"),
        ("See Resolution 76.", "citation", "resolution:76"),
        ("See ECC/DEC/(06)04.", "citation", "ecc:ecc/dec/(06)04"),
        ("See T/R 25-08 for trigger values.", "citation", "doc:T/R 25-08"),
        ("Write to admin@example.org.", "address", "admin@example.org"),
    ],
)
def test_invented_values_are_rejected_and_the_template_returned(bad_text, kind, value):
    case = named_case()
    result = generate(case, FakeModel(letter_body=bad_text))
    assert_is_the_template(result, case)
    assert "guardrail" in result.fallback_reason and result.model == "fake-model"
    assert any(v.kind == kind and v.value == value for v in result.violations), result.violations
    assert bad_text not in result.letter


def test_an_invented_number_in_the_explanation_alone_also_fails():
    case = named_case()
    result = generate(case, FakeModel(explanation="Raised for 43 reasons."))
    assert_is_the_template(result, case)


@pytest.mark.parametrize(
    "claim",
    [
        "The filing exceeds the limit.",
        "The limit was exceeded.",
        "This is in excess of the mask.",
        "The network violates the Radio Regulations.",
        "This filing is a breach of No. 9.14.",
        "The filing is non-compliant.",
        "The transmission is above the limit.",
        "The satellite will cause harmful interference.",
        "Emissions are prohibited here.",
    ],
)
@pytest.mark.parametrize("make", [named_case, m4_case])
def test_a_screening_case_cannot_claim_a_limit_passed(claim, make):
    case = make()
    result = generate(case, FakeModel(letter_body=claim))
    assert_is_the_template(result, case)
    assert any(v.kind == "claim" for v in result.violations)


def test_a_screening_case_cannot_quote_a_level():
    case = named_case()
    result = generate(case, FakeModel(letter_body="The level is 9.14 dB."))
    assert_is_the_template(result, case)


def test_calculated_case_may_report_its_values_and_a_limit_passed():
    text = "The calculated value of -124.5 dB exceeds the limit of -129.0 dB by 4.5 dB under Table 21-4."
    result = generate(calculated_case(), FakeModel(letter_body=text, explanation="Margin 4.5 dB under No. 21.16."))
    assert result.source == "llm", result.violations


def test_calculated_case_with_negative_margin_cannot_claim_exceeded():
    case = calculated_case(-2.0, -131.0, -129.0)
    result = generate(case, FakeModel(letter_body="The value exceeds the limit."))
    assert_is_the_template(result, case)


def test_calculated_case_cannot_say_a_regulation_was_broken():
    case = calculated_case()
    result = generate(case, FakeModel(letter_body="The value of -124.5 dB violates Table 21-4."))
    assert_is_the_template(result, case)


def test_negative_margin_cannot_be_reported_without_its_sign():
    case = calculated_case(-2.0, -131.0, -129.0)
    result = generate(case, FakeModel(letter_body="The margin is 2.0 dB."))
    assert_is_the_template(result, case)
    assert any(v.kind == "label" for v in result.violations)


def test_value_and_limit_cannot_swap_labels():
    case = calculated_case()
    text = "The calculated value of -129.0 dB is set against a limit of -124.5 dB."
    result = generate(case, FakeModel(letter_body=text))
    assert_is_the_template(result, case)
    assert {v.value for v in result.violations if v.kind == "label"} == {"value -129.0 dB", "limit -124.5 dB"}


def test_number_from_a_retrieved_passage_is_allowed_and_one_not_retrieved_is_not():
    passage = Passage(id="rr-9.14", doc="RR", clause="Article 9, No. 9.14", page_or_url="u", text="Wait 77 days.", provision="9.14")
    result = generate(named_case(), FakeModel(letter_body="The passage says 77 days."), index=[passage])
    assert result.source == "llm", result.violations
    result = generate(named_case(), FakeModel(letter_body="The passage says 78 days."), index=[passage])
    assert result.source == "template"


def test_digits_in_the_file_hash_are_not_evidence():
    case = make_case(provisions=["9.14"])
    case.source_sha256 = "a1234567" * 8
    assert "1234567" not in evidence_for(CaseView.of(case), []).numbers


def test_number_formats_compare_by_value():
    assert numbers_in("13,400 MHz") == {"13400"} and numbers_in("1,5 dB") == {"1.5"}
    assert numbers_in("14.70") == {"14.7"} and numbers_in("four months") == {"4"}
    assert numbers_in("−3.5 dB") == {"-3.5"} and numbers_in("13400-13750") == {"13400", "13750"}
    assert numbers_in("No. 9.52.1") == {"9.52.1"}
    assert dates_in("6 June 2025, June 7, 2025 and 2027-02-27") == {"2025-06-06", "2025-06-07", "2027-02-27"}
    assert {"rr:9.7", "rr:9.14", "rr:9.21"} <= citations_in("Nos. 9.7 to 9.14 and 9.21")


def test_empty_output_fails():
    assert check_text("  ", Evidence(), False, None)[0].kind == "empty"


def test_model_failure_falls_back_with_the_reason():
    case = named_case()
    result = generate(case, FakeModel(error=ModelError("the API rate limit was reached")))
    assert_is_the_template(result, case)
    assert result.fallback_reason == "the API rate limit was reached" and result.violations == []


def test_no_model_returns_the_template_with_the_reason():
    case = named_case()
    result = generate(case, None)
    assert_is_the_template(result, case)
    assert result.fallback_reason == "no language model is configured"


def test_prompt_carries_case_and_passages_and_marks_them_as_data():
    model = FakeModel()
    generate(named_case(), model)
    prompt = model.prompts[0]
    assert "<case>" in prompt and "<passages>" in prompt and "<template>" in prompt
    assert NOTICE in prompt and "regarded as unaffected" in prompt


# --- retrieval over the committed index


def test_index_has_each_document_with_a_source():
    index = load_index()
    assert {p.doc for p in index} == {"RR", "M4", "T/R 25-08"}
    assert all(p.page_or_url and p.text for p in index)


def test_excerpts_stay_short():
    assert max(len(p.text) for p in load_index()) <= 2000


def test_sources_record_what_was_and_was_not_obtained():
    manifest = json.loads((Path(__file__).parents[1] / "data/refdocs/sources.json").read_text())
    assert {o["doc"] for o in manifest["obtained"]} == {"RR", "M4", "T/R 25-08"} and manifest["not_obtained"]
    assert all(o["url"].startswith("https://") and len(o["sha256"]) == 64 for o in manifest["obtained"])


def test_retrieval_is_by_exact_clause_and_band():
    passages, missing = retrieve("space", named_case().details)
    assert {"rr-9.14", "rr-9.52", "rr-9.52A", "rr-9.52C"} <= {p.id for p in passages} and missing == []
    passages, missing = retrieve("space", m4_case().details)
    assert {p.id for p in passages} == {"rr-9.3", "m4-13400-13750"} and missing == []
    assert "21.16" in wanted_provisions(calculated_case().details)
    assert "T/R 25-08" in {p.doc for p in retrieve("terrestrial", {"method": "p1546"})[0]}


def test_clause_text_matches_the_source():
    index = {p.id: p for p in load_index()}
    assert "within four months of the date of publication" in index["rr-9.52"].text
    assert "shall be regarded as unaffected" in index["rr-9.52C"].text
    assert "shall not exceed the limit given in Table 21-4" in index["rr-21.16"].text
    assert index["m4-1400-1427"].text.startswith("1400 - 1427 MHz RADIO ASTRONOMY")


def test_a_provision_outside_the_index_is_recorded_not_invented():
    case = make_case(provisions=["9.99"], sections=[])
    result = draft(case)
    assert result.passages_missing == ["RR No. 9.99"]
    assert any("9.99" in note for note in result.review_notes)
    assert not any(c.get("clause", "").endswith("9.99") for c in result.citations)


def test_a_missing_index_still_gives_a_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("IFIC_REFDOCS_DIR", str(tmp_path))
    result = draft(m4_case())
    assert result.passages_retrieved == [] and "RR No. 9.3" in result.passages_missing
    assert "13400-13750 MHz" in result.letter


# --- the Claude path, with no key and no network


def test_no_credentials_means_no_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert configured_model() is None


def _claude(handler) -> ClaudeModel:
    transport = httpx2.MockTransport(handler)
    client = anthropic.Anthropic(api_key=SECRET, http_client=anthropic.DefaultHttpxClient(transport=transport), max_retries=0)
    return ClaudeModel(client=client)


def _message(text: str, stop_reason="end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def test_claude_model_sends_the_expected_request_through_the_sdk():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(200, json=_message('{"explanation": "E", "letter_body": "L"}'))

    output = _claude(handler).rewrite("prompt")
    assert output == ModelOutput("E", "L")
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert seen["beta"] == "server-side-fallback-2026-07-01"
    assert body["output_config"]["format"]["type"] == "json_schema" and body["output_config"]["effort"] == "medium"
    assert "thinking" not in body and "temperature" not in body


@pytest.mark.parametrize(
    "response, reason",
    [
        (httpx2.Response(200, json=_message("", "refusal")), "refusal"),
        (httpx2.Response(200, json=_message('{"explanation": "E"', "max_tokens")), "max_tokens"),
        (httpx2.Response(200, json=_message("not json")), "JSON"),
        (httpx2.Response(200, json=_message('{"explanation": "E"}')), "JSON"),
        (httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}), "rate limit"),
        (httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "x"}}), "credentials"),
        (httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}}), "500"),
    ],
)
def test_claude_failures_become_a_model_error_without_secrets(response, reason):
    with pytest.raises(ModelError) as caught:
        _claude(lambda request: response).rewrite("prompt")
    assert reason in str(caught.value) and SECRET not in str(caught.value)


def test_a_connection_failure_gives_the_template():
    def handler(request):
        raise httpx2.ConnectError("no network")

    case = named_case()
    result = generate(case, _claude(handler))
    assert_is_the_template(result, case)
    assert result.fallback_reason == "could not reach the API"


def test_claude_output_goes_through_the_guardrail():
    invented = json.dumps({"explanation": "E", "letter_body": "A margin of 57.3 units."})
    case = named_case()
    result = generate(case, _claude(lambda request: httpx2.Response(200, json=_message(invented))))
    assert_is_the_template(result, case)
    assert result.model == "claude-opus-5-5" and result.violations[0].value == "57.3"


def test_response_block_handling_skips_non_text_blocks():
    client = SimpleNamespace(
        beta=SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text='{"explanation": "E", "letter_body": "L"}')],
                )
            )
        )
    )
    assert ClaudeModel(client=client).rewrite("p") == ModelOutput("E", "L")


# --- the endpoint and the boundaries around it


def _store_case() -> int:
    with session_scope() as s:
        case = m4_case()
        case.id = None
        s.add(case)
        s.flush()
        return case.id


def test_endpoint_returns_the_template_and_404s():
    client = TestClient(screening_app)
    case_id = _store_case()
    body = client.get(f"/cases/{case_id}/draft").json()
    assert body["source"] == "template" and body["requires_expert_review"] is True and body["sent"] is False
    assert body["fallback_reason"] is None and "m4-13400-13750" in body["passages_retrieved"]
    assert client.get("/cases/9999/draft").status_code == 404


def test_endpoint_asks_for_the_key_before_it_may_call_a_model(monkeypatch):
    monkeypatch.setattr("ific_guard.screening.app.configured_model", lambda: pytest.fail("no model without the key"))
    client = TestClient(screening_app)
    assert client.get(f"/cases/{_store_case()}/draft", params={"use_llm": True}).status_code == 401


def test_endpoint_uses_the_model_with_the_key_and_falls_back_on_invented_text(monkeypatch):
    client = TestClient(screening_app)
    case_id = _store_case()
    monkeypatch.setattr("ific_guard.screening.app.configured_model", lambda: FakeModel(letter_body="See Article 99."))
    body = client.get(f"/cases/{case_id}/draft", params={"use_llm": True}, headers=KEY).json()
    assert body["source"] == "template" and "guardrail" in body["fallback_reason"]
    assert body["violations"][0]["value"] == "99"


def test_endpoint_without_a_configured_model_falls_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    client = TestClient(screening_app)
    body = client.get(f"/cases/{_store_case()}/draft", params={"use_llm": True}, headers=KEY).json()
    assert body["source"] == "template" and body["fallback_reason"] == "no language model is configured"


def test_a_draft_stores_nothing_and_queues_no_email(monkeypatch):
    client = TestClient(screening_app)
    case_id = _store_case()
    monkeypatch.setattr("ific_guard.screening.app.configured_model", lambda: FakeModel())
    client.get(f"/cases/{case_id}/draft")
    client.get(f"/cases/{case_id}/draft", params={"use_llm": True}, headers=KEY)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Notification)) == 0
        assert s.scalar(select(func.count()).select_from(Event)) == 0
        stored = s.get(Case, case_id)
        assert stored.review_status == "pending" and "llm" not in json.dumps(stored.details)


def test_notifier_and_digest_do_not_use_the_llm_layer():
    root = Path(__file__).parents[1] / "ific_guard"
    for path in (root / "notifier").glob("*.py"):
        text = path.read_text()
        assert "llm" not in text and "anthropic" not in text, path


def test_llm_layer_has_no_mail_code_or_recipient_addresses():
    for path in (Path(__file__).parents[1] / "ific_guard" / "llm").glob("*.py"):
        text = path.read_text()
        assert "smtplib" not in text and "mailer" not in text and "@example" not in text, path


def pfd_case(result="envelope_exceeds", basis="envelope", margin=2.8) -> Case:
    return make_case(
        rule_id="pfd-ngso-art21",
        method="pfd_ngso",
        result=result,
        margin_basis=basis,
        value_db=-151.2,
        limit_db=-154.0,
        margin_db=margin,
        mask_id="art21-test",
        citations=[{"doc": "RR", "clause": "Table 21-4", "printed_page": 165, "footnotes": ["NOTE 2"]}],
    )


def test_draft_accepts_the_citations_the_pfd_rule_writes():
    result = draft(CaseView.of(pfd_case()))
    assert any(c.get("printed_page") == 165 for c in result.citations)


def test_upper_bound_above_a_limit_is_never_drafted_as_an_exceedance():
    view = CaseView.of(pfd_case())
    result = draft(view)
    assert result.possible_concern_only is True
    assert "exceeds the limit" not in result.letter
    assert "establishes neither an exceedance nor compliance" in result.letter
    assert "does not show that a limit is exceeded" in result.explanation
    assert margin_of(view.details) is None


def test_guardrail_stops_a_model_claiming_a_bound_passed_the_limit():
    view = CaseView.of(pfd_case())
    violations = check_text("The filing exceeds the limit.", evidence_for(view, []), True, margin_of(view.details))
    assert [v for v in violations if v.kind == "claim"]


def test_propagated_exceedance_is_still_drafted_as_one():
    result = draft(CaseView.of(pfd_case("exceeds", "propagated_sample")))
    assert result.possible_concern_only is False
    assert "the calculated value exceeds the limit" in result.letter


def test_letters_quote_rounded_levels_and_a_model_may_repeat_them():
    case = calculated_case(margin_db=36.664971472751915, value_db=-86.33502852724808, limit_db=-123.0)
    assert "a margin of 36.66 dB" in draft(CaseView.of(case)).letter
    assert "36.664971" not in draft(CaseView.of(case)).letter
    text = "The calculated value of -86.34 dB exceeds the limit of -123.0 dB by a margin of 36.66 dB."
    assert generate(case, FakeModel(letter_body=text, explanation="Margin 36.66 dB.")).source == "llm"

