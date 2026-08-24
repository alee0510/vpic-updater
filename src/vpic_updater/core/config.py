# urls
VPIC_DOWNLOADS_URL = "https://vpic.nhtsa.dot.gov/downloads"
VPIC_BASE_URL = "https://vpic.nhtsa.dot.gov"

# extract stage
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MB
DEFAULT_TIMEOUT_SECONDS = 120
MIN_EXPECTED_ZIP_BYTES = 10_000_000  # ~10MB floor; real files are 60-200MB

# transform stage
MIN_EXPECTED_BACKUP_BYTES = 10_000_000  # matches the zip floor from Extract

# Arbitrary constant unique to this job -- must never collide with another
# app's advisory lock key on the same control-db instance.
ADVISORY_LOCK_KEY = 78123456

DEFAULT_MIN_ROW_COUNT = 100_000
DEFAULT_APP_ROLE = "vpic_user"
DEFAULT_SCHEMA = "vpic"