from pydantic import BaseModel


class ValidationResult(BaseModel):
    """
    Lightweight read-only DTO representing the outcome of Stage 3 (validate).

    No internal mutable state, no side effects. Used to communicate the
    validation snapshot from the validate stage to the audit log and
    the deploy stage.
    """
    db_name: str
    table_row_counts: dict[str, int]
    smoke_test_passed: bool

    model_config = {"frozen": True}