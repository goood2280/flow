import concurrent.futures
import json
import multiprocessing
from pathlib import Path
import time

from core.file_transaction import file_transaction
from core.utils import save_json


def _increment_file(path_text: str, count: int, delay: float = 0.0) -> None:
    path = Path(path_text)
    for _ in range(count):
        with file_transaction(path):
            try:
                payload = json.loads(path.read_text("utf-8"))
            except FileNotFoundError:
                payload = {"count": 0}
            if delay:
                time.sleep(delay)
            save_json(path, {"count": payload["count"] + 1})


def test_file_transaction_is_reentrant_and_serializes_threads(tmp_path):
    path = tmp_path / "state.json"
    with file_transaction(path):
        with file_transaction(path):
            save_json(path, {"count": 0})

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: _increment_file(str(path), 3, 0.005), range(6)))

    assert json.loads(path.read_text("utf-8")) == {"count": 18}


def test_file_transaction_serializes_processes(tmp_path):
    path = tmp_path / "shared.json"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_increment_file, args=(str(path), 4, 0.01))
        for _ in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0

    assert json.loads(path.read_text("utf-8")) == {"count": 12}
