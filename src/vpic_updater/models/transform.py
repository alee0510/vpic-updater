class TransformError(Exception):
    """Raised when the archive is corrupt, empty, or doesn't contain the
    expected .backup dump file."""