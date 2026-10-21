#!/usr/bin/env python3
"""Record Orca/GNOME process health and relevant kernel graphics/power events."""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import select
import signal
import subprocess
import sys
import threading
import time


INTERVAL = 5.0
WATCHED = {"orca-ide", "gnome-shell", "Xwayland", "Xorg"}
KERNEL_RE = re.compile(
    r"nvidia|drm|gpu|amdgpu|i915|nouveau|suspend|resume|freeze|hung|timeout|oom|out of memory",
    re.IGNORECASE,
)
STOP = threading.Event()


def timestamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def proc_samples(previous: dict[int, tuple[int, float]]) -> tuple[list[str], dict[int, tuple[int, float]]]:
    rows: list[str] = []
    current: dict[int, tuple[int, float]] = {}
    ticks = os.sysconf("SC_CLK_TCK")
    page_size = os.sysconf("SC_PAGE_SIZE")
    now = time.monotonic()
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = (entry / "stat").read_text()
            # comm is parenthesized and can contain spaces or parentheses; use its final ')'.
            close = raw.rfind(")")
            comm = raw[raw.find("(") + 1 : close]
            fields = raw[close + 2 :].split()  # begins at proc field 3 (state)
            if comm not in WATCHED:
                continue
            state = fields[0]
            ppid = int(fields[1])
            cpu_ticks = int(fields[11]) + int(fields[12])  # utime + stime
            threads = int(fields[17])
            rss_mb = int(fields[21]) * page_size / (1024 * 1024)
            current[pid] = (cpu_ticks, now)
            cpu_pct = 0.0
            if pid in previous:
                old_ticks, old_time = previous[pid]
                elapsed = now - old_time
                if elapsed > 0:
                    cpu_pct = max(0, cpu_ticks - old_ticks) * 100 / ticks / elapsed
            rows.append(
                f"pid={pid} ppid={ppid} name={comm} state={state} "
                f"cpu_pct={cpu_pct:.1f} rss_mb={rss_mb:.1f} threads={threads}"
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, IndexError):
            continue
    return rows, current


def watch_kernel(path: pathlib.Path) -> None:
    try:
        proc = subprocess.Popen(
            ["journalctl", "-k", "-f", "-n", "0", "-o", "short-iso-precise", "--no-pager"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        with path.open("a", encoding="utf-8") as out:
            out.write(f"{timestamp()} journalctl_start_error={type(exc).__name__}\n")
        return

    with path.open("a", encoding="utf-8") as out:
        out.write(f"{timestamp()} kernel_log_follow_started\n")
        assert proc.stdout is not None
        while not STOP.is_set():
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                break
            if KERNEL_RE.search(line):
                out.write(line.rstrip() + "\n")
                out.flush()
        if proc.poll() not in (None, 0) and proc.stderr:
            # Keep the error class/message, not any environment or command-line data.
            detail = proc.stderr.read(400).strip().replace("\n", " ")
            out.write(f"{timestamp()} journalctl_exit={proc.returncode} detail={detail}\n")
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    if not pathlib.Path("/proc").is_dir():
        print("This monitor requires Linux /proc.", file=sys.stderr)
        return 2
    if len(sys.argv) > 2:
        print(f"Usage: {pathlib.Path(sys.argv[0]).name} [interval-seconds]", file=sys.stderr)
        return 2
    interval = INTERVAL
    if len(sys.argv) == 2:
        try:
            interval = float(sys.argv[1])
            if interval < 1:
                raise ValueError
        except ValueError:
            print("Interval must be a number >= 1 second.", file=sys.stderr)
            return 2

    log_dir = pathlib.Path.home() / "orca-desktop-watch"
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S%z")
    samples_path = log_dir / f"samples-{stamp}.log"
    kernel_path = log_dir / f"kernel-{stamp}.log"
    for path in (samples_path, kernel_path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)

    def stop(_signum: int, _frame: object) -> None:
        STOP.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    kernel_thread = threading.Thread(target=watch_kernel, args=(kernel_path,), daemon=True)
    kernel_thread.start()
    previous: dict[int, tuple[int, float]] = {}
    print(f"Recording every {interval:g}s. Logs: {log_dir}")
    print("Stop with Ctrl+C. The script does not change power or Orca settings.")

    try:
        with samples_path.open("a", encoding="utf-8") as out:
            out.write("# Fields: timestamp, Orca/GNOME/X server process CPU%, RSS, state and threads.\n")
            while not STOP.is_set():
                rows, previous = proc_samples(previous)
                out.write(f"\n[{timestamp()}]\n")
                out.write("\n".join(rows) if rows else "No watched processes found.")
                out.write("\n")
                out.flush()
                STOP.wait(interval)
    except PermissionError as exc:
        print(f"Cannot write log: {exc}", file=sys.stderr)
        STOP.set()
        return 1
    finally:
        STOP.set()
        kernel_thread.join(timeout=3)
    print(f"Stopped. Logs: {samples_path} and {kernel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
