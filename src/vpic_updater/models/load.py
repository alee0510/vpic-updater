from pydantic import BaseModel


class LoadError(Exception):
    """
    Raised on any failure during Load stage operations:
    - Database creation
    - Dump restoration
    - Data validation
    - Permission grants
    - Database promotion
    """


class LoadResult(BaseModel):
    """
    Represents the outcome of the Load stage.

    Attributes:
        db_name: The name of the database that was created and loaded.
        version: The version string (year and month) of the data.
        row_count: The number of rows in the main VIN table.

    Config:
        frozen: Ensures the result object is immutable.
    """
    db_name: str
    version: str
    row_count: int

    model_config = {"frozen": True}