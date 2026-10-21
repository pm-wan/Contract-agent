"""Bounded local OCR adapter; never sends document images to an external service."""

import csv
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

MAX_OCR_IMAGE_BYTES = 20 * 1024 * 1024
MAX_OCR_TSV_BYTES = 10 * 1024 * 1024
MAX_OCR_METADATA_BYTES = 64 * 1024
MAX_OCR_IDENTITY_FILE_BYTES = 256 * 1024 * 1024
MAX_TESSDATA_TOTAL_BYTES = 512 * 1024 * 1024
_IO_CHUNK_BYTES = 64 * 1024

_FileSignature = tuple[int, int, int, int, int]


class OcrError(RuntimeError):
    """OCR could not produce a trustworthy, parseable local response."""


@dataclass(frozen=True, slots=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float
    line_id: tuple[int, int, int]


class OcrEngine(Protocol):
    version: str

    def recognize_png(self, data: bytes) -> tuple[OcrWord, ...]: ...


@dataclass(frozen=True, slots=True)
class _TesseractRuntimeIdentity:
    version: str
    executable: Path
    files: tuple[tuple[Path, _FileSignature], ...]


def _run_bounded_process(
    argv: list[str],
    *,
    input_data: bytes | None,
    timeout_seconds: float,
    maximum_output_bytes: int,
) -> bytes:
    """Run a local OCR command without buffering unbounded stdin/stdout in memory."""
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        raise OcrError("ocr_unavailable") from exc

    input_stream = process.stdin
    output_stream = process.stdout
    if output_stream is None or (input_data is not None and input_stream is None):
        with suppress(OSError):
            process.kill()
        process.wait()
        raise OcrError("ocr_failed")

    output = bytearray()
    input_errors: list[OSError] = []
    output_errors: list[OSError] = []
    output_too_large = Event()

    def read_bounded_output() -> None:
        try:
            while len(output) <= maximum_output_bytes:
                remaining = maximum_output_bytes + 1 - len(output)
                chunk = output_stream.read(min(_IO_CHUNK_BYTES, remaining))
                if not chunk:
                    return
                output.extend(chunk)
                if len(output) > maximum_output_bytes:
                    output_too_large.set()
                    with suppress(OSError):
                        process.kill()
                    return
        except OSError as exc:
            output_errors.append(exc)

    def write_input() -> None:
        assert input_stream is not None and input_data is not None
        try:
            data_view = memoryview(input_data)
            while data_view:
                written = input_stream.write(data_view[:_IO_CHUNK_BYTES])
                if written is None or written <= 0:
                    raise OSError("OCR process stopped accepting input")
                data_view = data_view[written:]
        except BrokenPipeError:
            pass
        except OSError as exc:
            input_errors.append(exc)
        finally:
            try:
                input_stream.close()
            except BrokenPipeError:
                pass
            except OSError as exc:
                input_errors.append(exc)

    reader = Thread(target=read_bounded_output, name="tesseract-output-reader", daemon=True)
    reader.start()
    writer = (
        Thread(target=write_input, name="tesseract-input-writer", daemon=True)
        if input_data is not None
        else None
    )
    if writer is not None:
        writer.start()

    timed_out = False
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        with suppress(OSError):
            process.kill()
        returncode = process.wait()
    except BaseException:
        with suppress(OSError):
            process.kill()
        process.wait()
        if writer is not None:
            writer.join(timeout=1)
        reader.join(timeout=1)
        raise

    if writer is not None:
        writer.join(timeout=1)
    reader.join(timeout=1)
    if (writer is not None and writer.is_alive()) or reader.is_alive():
        with suppress(OSError):
            process.kill()
        for stream in (input_stream, output_stream):
            if stream is not None:
                with suppress(OSError):
                    stream.close()
        if writer is not None:
            writer.join(timeout=1)
        reader.join(timeout=1)
        raise OcrError("ocr_timeout")
    output_stream.close()

    if timed_out:
        raise OcrError("ocr_timeout")
    if output_too_large.is_set():
        raise OcrError("ocr_output_too_large")
    if input_errors or output_errors:
        raise OcrError("ocr_failed") from (input_errors + output_errors)[0]
    if returncode != 0:
        raise OcrError("ocr_failed")
    return bytes(output)


def _file_signature(path: Path, maximum_bytes: int) -> _FileSignature:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OcrError("ocr_failed") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
        ):
            raise OcrError("ocr_failed")
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
    finally:
        os.close(descriptor)


def _hash_regular_file(path: Path, maximum_bytes: int) -> tuple[str, _FileSignature]:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OcrError("ocr_failed") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
        ):
            raise OcrError("ocr_failed")
        signature = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        digest = sha256()
        bytes_read = 0
        while chunk := os.read(descriptor, _IO_CHUNK_BYTES):
            bytes_read += len(chunk)
            if bytes_read > maximum_bytes:
                raise OcrError("ocr_failed")
            digest.update(chunk)
        final_metadata = os.fstat(descriptor)
        final_signature = (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_size,
            final_metadata.st_mtime_ns,
            final_metadata.st_ctime_ns,
        )
        if bytes_read != metadata.st_size or final_signature != signature:
            raise OcrError("ocr_failed")
        return digest.hexdigest(), signature
    finally:
        os.close(descriptor)


class TesseractCliOcr:
    """Read TSV from stdin/stdout; no persistent plaintext or image temp files."""

    def __init__(
        self,
        *,
        language: str = "chi_sim+eng",
        executable: str = "tesseract",
        timeout_seconds: int = 45,
    ) -> None:
        if (
            re.fullmatch(r"[a-z]{3}(?:_[a-z]{3})?(?:\+[a-z]{3}(?:_[a-z]{3})?)*", language) is None
            or len(language) > 128
            or not executable
            or timeout_seconds <= 0
        ):
            raise ValueError("invalid_ocr_configuration")
        self.language = language
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._runtime_identity: _TesseractRuntimeIdentity | None = None

    @property
    def version(self) -> str:
        if self._runtime_identity is None:
            raise OcrError("ocr_failed")
        return self._runtime_identity.version

    def _resolve_runtime_identity(self) -> _TesseractRuntimeIdentity:
        if self._runtime_identity is not None:
            self._assert_runtime_identity_unchanged(self._runtime_identity)
            return self._runtime_identity

        executable = shutil.which(self.executable)
        if executable is None:
            raise OcrError("ocr_unavailable")
        executable_path = Path(executable).resolve()
        executable_sha256, executable_signature = _hash_regular_file(
            executable_path, MAX_OCR_IDENTITY_FILE_BYTES
        )

        version_output = _run_bounded_process(
            [str(executable_path), "--version"],
            input_data=None,
            timeout_seconds=self.timeout_seconds,
            maximum_output_bytes=MAX_OCR_METADATA_BYTES,
        )
        first_version_line = version_output.decode("utf-8", errors="replace").splitlines()[0:1]
        if not first_version_line:
            raise OcrError("ocr_failed")
        version_match = re.match(r"^tesseract\s+(\S+)", first_version_line[0], re.IGNORECASE)
        if version_match is None:
            raise OcrError("ocr_failed")
        engine_version = re.sub(r"[^A-Za-z0-9._+-]", "-", version_match.group(1))[:20]

        languages_output = _run_bounded_process(
            [str(executable_path), "--list-langs"],
            input_data=None,
            timeout_seconds=self.timeout_seconds,
            maximum_output_bytes=MAX_OCR_METADATA_BYTES,
        )
        languages_text = languages_output.decode(
            sys.getfilesystemencoding(), errors="surrogateescape"
        )
        lines = languages_text.splitlines()
        if not lines:
            raise OcrError("ocr_failed")
        header_match = re.fullmatch(r'List of available languages in "(.+)" \((\d+)\):', lines[0])
        if header_match is None:
            raise OcrError("ocr_failed")
        tessdata_path = Path(header_match.group(1))
        available_languages = lines[1:]
        if len(available_languages) != int(header_match.group(2)) or len(
            set(available_languages)
        ) != len(available_languages):
            raise OcrError("ocr_failed")
        requested_languages = self.language.split("+")
        if any(language not in available_languages for language in requested_languages):
            raise OcrError("ocr_failed")

        traineddata_hashes: dict[str, str] = {}
        identity_files: list[tuple[Path, _FileSignature]] = [
            (executable_path, executable_signature)
        ]
        traineddata_bytes = 0
        for language in requested_languages:
            traineddata_path = tessdata_path / f"{language}.traineddata"
            traineddata_sha256, signature = _hash_regular_file(
                traineddata_path, MAX_OCR_IDENTITY_FILE_BYTES
            )
            traineddata_bytes += signature[2]
            if traineddata_bytes > MAX_TESSDATA_TOTAL_BYTES:
                raise OcrError("ocr_failed")
            traineddata_hashes[language] = traineddata_sha256
            identity_files.append((traineddata_path, signature))

        identity_document = {
            "protocol": "tesseract-cli-tsv-v2",
            "language": self.language,
            "executable_sha256": executable_sha256,
            "version_output_sha256": sha256(version_output).hexdigest(),
            "traineddata_sha256": traineddata_hashes,
        }
        identity_digest = sha256(
            json.dumps(identity_document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        identity = _TesseractRuntimeIdentity(
            version=f"tesseract-cli-tsv-v2:{engine_version}:{identity_digest}",
            executable=executable_path,
            files=tuple(identity_files),
        )
        self._assert_runtime_identity_unchanged(identity)
        self._runtime_identity = identity
        return identity

    @staticmethod
    def _assert_runtime_identity_unchanged(identity: _TesseractRuntimeIdentity) -> None:
        for path, expected_signature in identity.files:
            try:
                actual_signature = _file_signature(path, MAX_OCR_IDENTITY_FILE_BYTES)
            except OcrError as exc:
                raise OcrError("ocr_failed") from exc
            if actual_signature != expected_signature:
                raise OcrError("ocr_failed")

    def recognize_png(self, data: bytes) -> tuple[OcrWord, ...]:
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_OCR_IMAGE_BYTES:
            raise OcrError("ocr_image_invalid")
        identity = self._resolve_runtime_identity()
        output = _run_bounded_process(
            [
                str(identity.executable),
                "stdin",
                "stdout",
                "-l",
                self.language,
                "tsv",
            ],
            input_data=data,
            timeout_seconds=self.timeout_seconds,
            maximum_output_bytes=MAX_OCR_TSV_BYTES,
        )
        assert self._runtime_identity is not None
        self._assert_runtime_identity_unchanged(self._runtime_identity)
        return _parse_tsv(output)


def _parse_tsv(data: bytes) -> tuple[OcrWord, ...]:
    try:
        stream = io.StringIO(data.decode("utf-8-sig"))
        reader = csv.DictReader(stream, delimiter="\t")
        required = {
            "level",
            "block_num",
            "par_num",
            "line_num",
            "left",
            "top",
            "width",
            "height",
            "conf",
            "text",
        }
        if not required.issubset(reader.fieldnames or []):
            raise OcrError("ocr_output_invalid")
        words: list[OcrWord] = []
        for row in reader:
            if row["level"] != "5" or not row["text"].strip():
                continue
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
            confidence = float(row["conf"])
            if left < 0 or top < 0 or width <= 0 or height <= 0 or not 0 <= confidence <= 100:
                raise OcrError("ocr_output_invalid")
            words.append(
                OcrWord(
                    row["text"].strip(),
                    left,
                    top,
                    width,
                    height,
                    confidence,
                    (int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
                )
            )
        return tuple(words)
    except (AttributeError, TypeError, ValueError, UnicodeError, csv.Error, KeyError) as exc:
        raise OcrError("ocr_output_invalid") from exc
