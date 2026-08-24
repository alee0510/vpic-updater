from pathlib import Path
from pydantic import BaseModel

class TransformError(Exception):
    """Raised when the archive is corrupt, empty, or doesn't contain the
    expected .backup dump file."""


class ExtractError(Exception):
    """Raised when download fails, is truncated, or is not a valid zip."""


class ExtractedDump(BaseModel):
    dump_path: Path
    size_bytes: int

    model_config = {"frozen": True}