# urls
VPIC_DOWNLOADS_URL = "https://vpic.nhtsa.dot.gov/downloads"
VPIC_BASE_URL = "https://vpic.nhtsa.dot.gov"

# extract stage
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MB
DEFAULT_TIMEOUT_SECONDS = 120
MIN_EXPECTED_ZIP_BYTES = 10_000_000  # ~10MB floor; real files are 60-200MB