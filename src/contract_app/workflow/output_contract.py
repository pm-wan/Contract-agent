"""Strict model-response contract for factual slot candidates and citations."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

BoundedText = Annotated[
    str,
    Field(
        min_length=1,
        max_length=500,
        pattern=r"^[\s\S]*\S[\s\S]*$",
    ),
]


class CandidateObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: UUID
    page: StrictInt = Field(ge=1)
    quote: BoundedText


class SlotEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: UUID
    page: StrictInt = Field(ge=1)
    quote: BoundedText


class FactEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: UUID
    page: StrictInt = Field(ge=1)
    quote: BoundedText


class FactCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: Literal[
        "contract.contract_type",
        "contract.party_role",
        "contract.jurisdiction",
        "contract.total_amount",
    ]
    state: Literal["candidate", "undetermined"]
    value: BoundedText | None
    evidence: list[FactEvidenceCandidate] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_state_payload(self) -> "FactCandidate":
        if self.state == "candidate" and (self.value is None or not self.evidence):
            raise ValueError("fact_candidate_requires_value_and_evidence")
        if self.state == "undetermined" and (self.value is not None or self.evidence):
            raise ValueError("undetermined_fact_candidate_must_be_empty")
        return self


class SlotCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    state: Literal["filled", "needs_material", "conflicted", "undetermined", "human_review"]
    value: Any
    fact_type: str | None = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    evidence: list[SlotEvidenceCandidate] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_state_payload(self) -> "SlotCandidate":
        if self.state != "filled" and self.value is not None:
            raise ValueError("nonfilled_slot_must_not_claim_value")
        if self.state == "filled" and not self.evidence:
            raise ValueError("filled_slot_requires_evidence")
        return self


class CandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[CandidateObservation] = Field(max_length=20)
    fact_candidates: list[FactCandidate] = Field(max_length=4)
    slot_candidates: list[SlotCandidate] = Field(max_length=20)
    limitations: list[BoundedText] = Field(max_length=20)

    @model_validator(mode="after")
    def reject_duplicate_candidates(self) -> "CandidateOutput":
        ids = [candidate.slot_id for candidate in self.slot_candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_slot_candidate")
        predicates = [candidate.predicate for candidate in self.fact_candidates]
        if len(predicates) != len(set(predicates)):
            raise ValueError("duplicate_fact_candidate")
        return self


def candidate_output_schema() -> dict[str, object]:
    """Return a detached JSON Schema for the structured model response."""
    return CandidateOutput.model_json_schema()
