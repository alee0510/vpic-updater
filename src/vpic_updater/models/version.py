import re
from datetime import date
from pydantic import BaseModel, field_validator

class VersionCheckError(Exception):
    """Raised when the downloads page cannot be fetched or parsed."""


class VpicVersion(BaseModel):
    version: str                # e.g. "4.08"
    released_on: date
    postgres_custom_url: str    # .custom.zip -- what we actually download
    postgres_plain_url: str     # .plain.zip -- captured for reference/audit only

    model_config = { "frozen": True }

    @field_validator("version")
    @classmethod
    def validate_version_format(cls, v: str) -> str:
        if not re.match(r"^\d+\.\d+$", v):
            raise ValueError(f"Invalid version format: {v}")
        return v

    @field_validator("postgres_custom_url", "postgres_plain_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith("http"):
            raise ValueError(f"Invalid URL: {v}")
        return v

    @property
    def year_month(self) -> str:
        """Derive 'YYYY_MM' from the download filename, matching the ops
        team's existing naming convention (vPICList_lite_2026_07.custom.zip)."""
        match = re.search(r"_(\d{4}_\d{2})\.custom\.zip$", self.postgres_custom_url)
        if not match:
            raise VersionCheckError(
                f"Could not derive year_month from URL: {self.postgres_custom_url}"
            )
        return match.group(1)