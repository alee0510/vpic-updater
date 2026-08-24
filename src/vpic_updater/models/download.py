from pathlib import Path
from pydantic import BaseModel


class DownloadResult(BaseModel):
    file_path: Path
    url: str
    size_bytes: int

    model_config = {"frozen": True}