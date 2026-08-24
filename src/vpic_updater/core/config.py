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


"""
Table/row-count floors and the VIN decode smoke test used to validate a
freshly restored vPIC database before promotion.

Row-count floors are deliberately loose (an order of magnitude below what
the real dataset holds) -- their job is to catch a badly truncated or
empty restore, not to assert exact dataset size, which will naturally
grow release over release.
"""

# Core reference tables the decode pipeline depends on, with a minimum
# plausible row count for each. Floors are set well below observed
# production counts (see comments) to tolerate normal month-to-month
# growth/shrinkage, while still catching a truncated or empty restore.
#
# Source: pg_stat_user_tables against a real restored 4.08 dump, 2026-08-23.
CORE_TABLE_MIN_ROWS: dict[str, int] = {
    "wmiyearvalidchars": 5_000_000,   # actual: 8,809,214 -- largest table, strong signal
    "pattern": 1_000_000,             # actual: 1,674,161 -- was wrongly floored at 100K before
    "vehiclespecpattern": 100_000,    # actual: 222,169
    "wmi_vinschema": 25_000,          # actual: 41,708
    "model": 20_000,                  # actual: 31,869
    "vinschema": 15_000,              # actual: 25,149
    "manufacturer": 15_000,           # actual: 22,893
    "wmi": 8_000,                     # actual: 12,971
    "make": 8_000,                    # actual: 12,328
    "element": 50,                    # not in top 15 -- fixed small vocabulary, unconfirmed exact count
}

# Functions the pipeline's actual consumers depend on -- if these are
# missing, the restore is structurally incomplete even if row counts
# look fine on individual tables.
REQUIRED_FUNCTIONS = ("spvindecode", "spvindecode_core")

# A real, stable VIN used purely as a decode smoke test. Verified locally
# against vPIC 4.08 -- decodes clean (Error Text: "0 - VIN decoded clean"),
# returns 68 rows spanning General/Interior/Engine groups.
SMOKE_TEST_VIN = "1FTEW1E4XKFC98434"