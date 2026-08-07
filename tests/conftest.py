from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def write_csv(tmp_path):
    """Writes a CSV under tmp_path from a header list + row dicts.

    Ensures tests that need concrete, known values never touch the real
    data/ directory — every synthetic dataset lives entirely in tmp_path.
    """

    def _write(filename: str, header: list[str], rows: list[dict]) -> Path:
        df = pd.DataFrame(rows, columns=header)
        path = tmp_path / filename
        df.to_csv(path, index=False)
        return path

    return _write
