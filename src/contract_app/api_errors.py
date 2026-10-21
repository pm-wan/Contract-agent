"""Shared OpenAPI contract for the application's safe Problem responses."""

from typing import Any, Literal

from pydantic import BaseModel


class ProblemDetailsResponse(BaseModel):
    type: Literal["about:blank"]
    title: str
    status: int
    code: str
    trace_id: str


PROBLEM_SCHEMA_REF = "#/components/schemas/ProblemDetailsResponse"


def problem_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build response entries using the exact media type returned by the API."""
    return {
        status_code: {
            "description": "Safe application error; response bodies omit request data.",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": PROBLEM_SCHEMA_REF},
                }
            },
        }
        for status_code in status_codes
    }


COMMON_PROBLEM_RESPONSES = problem_responses(401, 403, 422, 503)
