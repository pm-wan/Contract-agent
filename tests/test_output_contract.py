import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from contract_app.workflow.output_contract import CandidateOutput, candidate_output_schema


def test_candidate_output_accepts_bounded_structured_result() -> None:
    document_id = uuid4()
    candidate = CandidateOutput.model_validate_json(
        json.dumps(
            {
                "observations": [
                    {
                        "document_version_id": str(document_id),
                        "page": 1,
                        "quote": "Payment is due after acceptance.",
                    }
                ],
                "fact_candidates": [],
                "slot_candidates": [
                    {
                        "slot_id": "payment_schedule",
                        "fact_type": "contract.payment_schedule",
                        "state": "filled",
                        "value": [
                            {"trigger": "验收", "amount_type": "percentage", "percentage": "100"}
                        ],
                        "evidence": [
                            {
                                "document_version_id": str(document_id),
                                "page": 1,
                                "quote": "Payment is due after acceptance.",
                            }
                        ],
                    }
                ],
                "limitations": [],
            }
        )
    )
    assert candidate.observations[0].document_version_id == document_id
    assert candidate.observations[0].page == 1
    assert candidate.slot_candidates[0].value[0]["percentage"] == "100"
    assert candidate.slot_candidates[0].fact_type == "contract.payment_schedule"
    assert candidate_output_schema()["additionalProperties"] is False


def test_fact_type_is_required_and_uses_stable_id_syntax() -> None:
    schema = candidate_output_schema()["$defs"]["SlotCandidate"]
    assert "fact_type" in schema["required"]
    with pytest.raises(ValidationError):
        CandidateOutput.model_validate(
            {
                "observations": [],
                "fact_candidates": [],
                "slot_candidates": [
                    {
                        "slot_id": "payment_schedule",
                        "fact_type": "Payment Schedule",
                        "state": "undetermined",
                        "value": None,
                        "evidence": [],
                    }
                ],
                "limitations": [],
            }
        )


def test_fact_candidates_require_exact_evidence_for_candidate_state() -> None:
    doc_id = str(uuid4())
    base = {
        "observations": [],
        "fact_candidates": [
            {
                "predicate": "contract.total_amount",
                "state": "candidate",
                "value": "1000 CNY",
                "evidence": [
                    {"document_version_id": doc_id, "page": 1, "quote": "合同总价为1000元"}
                ],
            }
        ],
        "slot_candidates": [],
        "limitations": [],
    }
    parsed = CandidateOutput.model_validate(base)
    assert parsed.fact_candidates[0].predicate == "contract.total_amount"
    invalid = {**base, "fact_candidates": [{**base["fact_candidates"][0], "evidence": []}]}
    with pytest.raises(ValidationError):
        CandidateOutput.model_validate(invalid)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "observations": [],
            "slot_candidates": [],
            "limitations": [],
            "judgment": "passed",
        },
        {
            "observations": [
                {"document_version_id": str(uuid4()), "page": 1, "quote": "x", "risk": "low"}
            ],
            "slot_candidates": [],
            "limitations": [],
        },
        {
            "observations": [{"document_version_id": str(uuid4()), "page": True, "quote": "x"}],
            "slot_candidates": [],
            "limitations": [],
        },
        {
            "observations": [{"document_version_id": str(uuid4()), "page": 1, "quote": "   "}],
            "slot_candidates": [],
            "limitations": [],
        },
        {"observations": [], "slot_candidates": [], "limitations": [" "]},
        {
            "observations": [{"document_version_id": str(uuid4()), "page": 1, "quote": "x"}] * 21,
            "slot_candidates": [],
            "limitations": [],
        },
        {
            "observations": [],
            "slot_candidates": [
                {
                    "slot_id": "payment_schedule",
                    "fact_type": "contract.payment_schedule",
                    "state": "filled",
                    "value": [],
                    "evidence": [],
                }
            ],
            "limitations": [],
        },
        {
            "observations": [],
            "slot_candidates": [
                {
                    "slot_id": "payment_schedule",
                    "state": "undetermined",
                    "value": [],
                    "evidence": [],
                }
            ],
            "limitations": [],
        },
        {
            "observations": [],
            "slot_candidates": [
                {
                    "slot_id": "payment_schedule",
                    "state": "undetermined",
                    "value": None,
                    "evidence": [],
                },
                {
                    "slot_id": "payment_schedule",
                    "state": "undetermined",
                    "value": None,
                    "evidence": [],
                },
            ],
            "limitations": [],
        },
    ],
)
def test_candidate_output_rejects_malformed_or_out_of_contract_result(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CandidateOutput.model_validate_json(json.dumps(payload))
