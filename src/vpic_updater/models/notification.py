from pydantic import BaseModel


class NotificationResult(BaseModel):
    sent: bool
    status_code: int | None = None
    error: str | None = None

    model_config = {"frozen": True}