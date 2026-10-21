import os
from pathlib import Path

import pytest

from contract_app.bounded_files import read_bounded_file


def test_read_bounded_file_reads_at_most_limit_plus_one(tmp_path: Path) -> None:
    path = tmp_path / "source.txt"
    path.write_bytes(b"abcdef")

    assert read_bounded_file(path, 4) == b"abcde"


def test_read_bounded_file_rejects_non_regular_file(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        read_bounded_file(tmp_path, 10)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are unavailable")
def test_read_bounded_file_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    path = tmp_path / "source.pipe"
    os.mkfifo(path)

    with pytest.raises(OSError):
        read_bounded_file(path, 10)
