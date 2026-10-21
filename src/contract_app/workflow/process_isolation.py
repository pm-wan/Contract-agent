"""Run a task handler in a child process that can be terminated on cancellation."""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import signal
from collections.abc import Callable
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from time import monotonic
from typing import Any

from contract_app.workflow.cancellation import CancellationSignal

MAX_ISOLATED_RESULT_BYTES = 4 * 1024 * 1024
_POLL_INTERVAL_SECONDS = 0.025
_COOPERATIVE_STOP_SECONDS = 0.25
_TERMINATE_WAIT_SECONDS = 2.0
_SAFE_ERROR_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_PROCESS_GROUP_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)

ProcessTarget = Callable[..., dict[str, Any]]


class IsolatedHandlerCancelled(Exception):
    """The parent task lost its lease or received an explicit cancellation."""


class IsolatedHandlerTimeout(TimeoutError):
    """An isolated handler exceeded its independent process deadline."""


class IsolatedHandlerError(RuntimeError):
    """The child failed; only its exception type, never its message, is retained."""

    def __init__(self, error_type: str = "IsolatedHandlerError") -> None:
        self.safe_error_code = (
            error_type if _SAFE_ERROR_TYPE.fullmatch(error_type) else "IsolatedHandlerError"
        )
        super().__init__(self.safe_error_code)


def _safe_error_type(error: Exception) -> str:
    name = type(error).__name__
    return name if _SAFE_ERROR_TYPE.fullmatch(name) else "IsolatedHandlerError"


def _child_entry(
    connection: Connection,
    target: ProcessTarget,
    args: tuple[Any, ...],
    cancellation: CancellationSignal,
) -> None:
    if os.name == "posix":
        os.setsid()
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    finally:
        if devnull > 2:
            os.close(devnull)
    try:
        try:
            result = target(*args, cancellation=cancellation)
            if not isinstance(result, dict):
                raise TypeError("isolated_handler_result_must_be_dict")
            response: dict[str, Any] = {"ok": True, "result": result}
            encoded = json.dumps(
                response, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            if len(encoded) > MAX_ISOLATED_RESULT_BYTES:
                encoded = b'{"ok":false,"error_type":"IsolatedResultTooLarge"}'
        except Exception as exc:
            encoded = json.dumps(
                {"ok": False, "error_type": _safe_error_type(exc)},
                separators=(",", ":"),
            ).encode("ascii")
        connection.send_bytes(encoded)
    finally:
        connection.close()


def _terminate_process_tree(process: BaseProcess) -> None:
    pid = process.pid
    if pid is None:
        return
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            if process.is_alive():
                process.terminate()
        process.join(_COOPERATIVE_STOP_SECONDS)
        # Kill the process group even if its Python leader exited: a blocking
        # provider SDK may have left a helper subprocess behind.
        try:
            os.killpg(pid, _PROCESS_GROUP_SIGNAL)
        except ProcessLookupError:
            if process.is_alive():
                process.kill()
    elif process.is_alive():
        process.terminate()
    process.join(_TERMINATE_WAIT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_TERMINATE_WAIT_SECONDS)


def run_cancellable_process(
    target: ProcessTarget,
    args: tuple[Any, ...],
    *,
    cancellation: CancellationSignal,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run an importable handler in a spawned process with bounded result IPC.

    The child gets a multiprocessing-compatible cancellation signal for normal
    async shutdown. If it cannot cooperate (for example, a synchronous SDK call
    blocks its event loop), the parent terminates the isolated process group.
    """
    if timeout_seconds <= 0:
        raise ValueError("invalid_isolated_handler_timeout")
    if os.name != "posix":
        raise IsolatedHandlerError("ProcessGroupIsolationUnavailable")
    if cancellation.is_set():
        raise IsolatedHandlerCancelled()

    context = multiprocessing.get_context("spawn")
    child_cancellation = context.Event()
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_child_entry,
        args=(send_connection, target, args, child_cancellation),
        name="contract-task-handler",
        daemon=False,
    )
    started = False
    try:
        process.start()
        started = True
        send_connection.close()
        deadline = monotonic() + timeout_seconds
        response_bytes: bytes | None = None
        while response_bytes is None:
            if cancellation.is_set():
                child_cancellation.set()
                process.join(_COOPERATIVE_STOP_SECONDS)
                _terminate_process_tree(process)
                raise IsolatedHandlerCancelled()
            if monotonic() >= deadline:
                child_cancellation.set()
                process.join(_COOPERATIVE_STOP_SECONDS)
                _terminate_process_tree(process)
                raise IsolatedHandlerTimeout()
            if receive_connection.poll(_POLL_INTERVAL_SECONDS):
                response_bytes = receive_connection.recv_bytes(MAX_ISOLATED_RESULT_BYTES)
                break
            if not process.is_alive():
                if receive_connection.poll(_POLL_INTERVAL_SECONDS):
                    response_bytes = receive_connection.recv_bytes(MAX_ISOLATED_RESULT_BYTES)
                    break
                raise IsolatedHandlerError("IsolatedHandlerCrashed")

        process.join(_COOPERATIVE_STOP_SECONDS)
        terminated_after_response = process.is_alive()
        if terminated_after_response:
            # The target has returned and the complete response is already in
            # the pipe. SDK-owned non-daemon threads may still keep the child
            # alive during interpreter shutdown; stop that isolated process
            # without discarding the successful response.
            _terminate_process_tree(process)
        if process.exitcode != 0 and not terminated_after_response:
            raise IsolatedHandlerError("IsolatedHandlerCrashed")

        try:
            response = json.loads(response_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise IsolatedHandlerError("IsolatedHandlerResponseInvalid") from exc
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise IsolatedHandlerError("IsolatedHandlerResponseInvalid")
        if response["ok"] is False:
            error_type = response.get("error_type")
            if not isinstance(error_type, str):
                error_type = "IsolatedHandlerError"
            raise IsolatedHandlerError(error_type)
        result = response.get("result")
        if not isinstance(result, dict):
            raise IsolatedHandlerError("IsolatedHandlerResponseInvalid")
        return result
    except (IsolatedHandlerCancelled, IsolatedHandlerTimeout, IsolatedHandlerError):
        raise
    except (EOFError, OSError, ValueError, RuntimeError) as exc:
        raise IsolatedHandlerError("IsolatedHandlerUnavailable") from exc
    finally:
        if started and process.is_alive():
            _terminate_process_tree(process)
        if started and process.exitcode is not None:
            process.close()
        receive_connection.close()
        send_connection.close()
