from pathlib import Path

# src/metropolis/paths.py -> repository root
_BASE_DIR = Path(__file__).resolve().parents[2]

# Data
DATA_DIR = _BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

# Models
MODELS_DIR = _BASE_DIR / "models"
BINARIES_DIR = MODELS_DIR / "binaries"
CHECKPOINTS_DIR = MODELS_DIR / "checkpoints"

# Runtime artifacts
ARTIFACTS_DIR = _BASE_DIR / "artifacts"
LOGS_DIR = ARTIFACTS_DIR / "logs"
CACHE_DIR = ARTIFACTS_DIR / "cache"
OUTPUTS_DIR = ARTIFACTS_DIR / "outputs"


def ensure_directories() -> None:
    """Create directories the application writes to."""
    for path in (
        RAW_DATA_DIR,
        PROCESSED_DATA_DIR,
        BINARIES_DIR,
        CHECKPOINTS_DIR,
        LOGS_DIR,
        CACHE_DIR,
        OUTPUTS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
