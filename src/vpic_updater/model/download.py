from pathlib import Path
from pydantic import BaseModel


class ExtractError(Exception):
    """Raised when download fails, is truncated, or is not a valid zip."""


class DownloadResult(BaseModel):
    file_path: Path
    url: str
    size_bytes: int

    model_config = {"frozen": True}