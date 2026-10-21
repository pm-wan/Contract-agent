import multiprocessing
import os
import queue
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

from contract_app.workflow.cancellation import CancellationSignal
from contract_app.workflow.process_isolation import (
    IsolatedHandlerCancelled,
    IsolatedHandlerError,
    run_cancellable_process,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process-group isolation requires POSIX")


def _spawn_blocking_descendant(
    started: Any, descendant_pid: Any, *, cancellation: CancellationSignal
) -> dict[str, Any]:
    del cancellation
    descendant = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )
    descendant_pid.value = descendant.pid
    started.set()
    while True:
        time.sleep(1)


def _raise_with_sensitive_message(*, cancellation: CancellationSignal) -> dict[str, Any]:
    del cancellation
    raise ValueError("sensitive contract text must not cross process boundary")


def _return_result(value: str, *, cancellation: CancellationSignal) -> dict[str, Any]:
    return {"value": value, "cancelled": cancellation.is_set()}


def _block_without_cooperating(started: Any, *, cancellation: CancellationSignal) -> dict[str, Any]:
    del cancellation
    started.set()
    while True:
        time.sleep(1)


def _process_is_running(pid: int) -> bool:
    proc_stat = f"/proc/{pid}/stat"
    if os.path.exists(proc_stat):
        try:
            state = Path(proc_stat).read_text(encoding="ascii").split()[2]
        except (FileNotFoundError, ProcessLookupError):
            return False
        return state != "Z"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_cancellation_kills_noncooperative_handler_and_its_descendants() -> None:
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    descendant_pid = context.Value("i", 0)
    cancellation = Event()
    outcomes: queue.Queue[BaseException] = queue.Queue()

    def invoke() -> None:
        try:
            run_cancellable_process(
                _spawn_blocking_descendant,
                (started, descendant_pid),
                cancellation=cancellation,
                timeout_seconds=30,
            )
        except BaseException as exc:
            outcomes.put(exc)

    thread = Thread(target=invoke, daemon=True)
    thread.start()
    try:
        assert started.wait(15), "isolated handler did not start"
        cancellation.set()
        thread.join(10)
        assert not thread.is_alive(), "cancellation did not return to the worker"
    finally:
        cancellation.set()
        if thread.is_alive():
            thread.join(10)

    assert isinstance(outcomes.get_nowait(), IsolatedHandlerCancelled)
    pid = descendant_pid.value
    assert pid > 0
    deadline = time.monotonic() + 5
    while _process_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_is_running(pid), "isolated SDK helper process survived cancellation"
    assert not any(
        child.name == "contract-task-handler" and child.is_alive()
        for child in multiprocessing.active_children()
    )


def test_hard_timeout_kills_noncooperative_handler() -> None:
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    cancellation = Event()
    outcomes: queue.Queue[BaseException] = queue.Queue()

    def invoke() -> None:
        try:
            run_cancellable_process(
                _block_without_cooperating,
                (started,),
                cancellation=cancellation,
                timeout_seconds=5,
            )
        except BaseException as exc:
            outcomes.put(exc)

    thread = Thread(target=invoke, daemon=True)
    thread.start()
    assert started.wait(10), "isolated handler did not start before deadline"
    thread.join(10)

    assert not thread.is_alive(), "hard timeout did not return to the worker"
    failure = outcomes.get_nowait()
    assert type(failure).__name__ == "IsolatedHandlerTimeout"
    assert not any(
        child.name == "contract-task-handler" and child.is_alive()
        for child in multiprocessing.active_children()
    )


def test_child_exception_exports_only_safe_type_not_message() -> None:
    with pytest.raises(IsolatedHandlerError) as caught:
        run_cancellable_process(
            _raise_with_sensitive_message,
            (),
            cancellation=Event(),
            timeout_seconds=10,
        )

    assert caught.value.safe_error_code == "ValueError"
    assert "sensitive contract text" not in str(caught.value)


def test_isolated_handler_returns_bounded_json_result() -> None:
    result = run_cancellable_process(
        _return_result,
        ("synthetic-safe-value",),
        cancellation=Event(),
        timeout_seconds=10,
    )

    assert result == {"value": "synthetic-safe-value", "cancelled": False}
