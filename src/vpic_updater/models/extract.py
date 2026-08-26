from pathlib import Path
from pydantic import BaseModel


class ExtractError(Exception):
    """Raised when download fails, is truncated, or is not a valid zip."""


class ExtractedDump(BaseModel):
    dump_path: Path
    size_bytes: int

    model_config = {"frozen": True}