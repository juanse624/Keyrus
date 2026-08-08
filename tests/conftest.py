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


@pytest.fixture
def write_markdown(tmp_path):
    """Writes a markdown file under a documents/ subdir of tmp_path.

    Mirrors write_csv's tmp_path-only isolation — tests for tools.documents
    never touch the real data/documents/*.md content.
    """

    def _write(filename: str, text: str) -> Path:
        documents_dir = tmp_path / "documents"
        documents_dir.mkdir(exist_ok=True)
        path = documents_dir / filename
        path.write_text(text, encoding="utf-8")
        return documents_dir

    return _write
