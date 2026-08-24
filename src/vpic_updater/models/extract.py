from pathlib import Path
from pydantic import BaseModel

class TransformError(Exception):
    """Raised when the archive is corrupt, empty, or doesn't contain the
    expected .backup dump file."""


class ExtractedDump(BaseModel):
    dump_path: Path
    size_bytes: int

    model_config = {"frozen": True}