import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROCESSED = ROOT / "data" / "processed"


@pytest.fixture(scope="session")
def processed_dir():
    """Skip tests that need pipeline output if it has not been generated yet."""
    if not (PROCESSED / "measure_fact.csv").exists():
        pytest.skip("data/processed not built - run `python -m src.data_ingestion` and `python -m src.data_cleaning`")
    return PROCESSED
