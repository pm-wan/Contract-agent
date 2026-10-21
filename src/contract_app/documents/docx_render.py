"""Deterministic, local DOCX-to-PDF rendering for page-stable indexing."""

import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

MAX_RENDERED_PDF_BYTES = 120 * 1024 * 1024
RENDER_TIMEOUT_SECONDS = 120
MAX_ENVIRONMENT_ID_LENGTH = 40


class DocxRenderError(ValueError):
    """A DOCX could not be rendered into the canonical paginated representation."""


@dataclass(frozen=True, slots=True)
class RenderedDocx:
    pdf_bytes: bytes
    renderer_identity: str
    source_sha256: str


def render_docx_to_pdf(data: bytes) -> RenderedDocx:
    """Render an already ZIP-validated DOCX without using client paths or a shell."""
    executable = shutil.which("libreoffice")
    if executable is None:
        raise DocxRenderError("docx_renderer_unavailable")
    with tempfile.TemporaryDirectory(prefix="contract-docx-render-") as temporary:
        root = Path(temporary)
        profile = root / "profile"
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        source = input_dir / "source.docx"
        source.write_bytes(data)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(root),
            # OpenCL probing hangs headless LibreOffice on the current dev host.
            "SAL_DISABLE_OPENCL": "1",
        }
        try:
            version_result = subprocess.run(
                [executable, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                env=environment,
            )
            version_line = version_result.stdout.strip().splitlines()[0]
            if version_result.returncode != 0 or not version_line:
                raise DocxRenderError("docx_renderer_version_unavailable")
            expected_version = os.environ.get("DOCX_RENDERER_EXPECTED_VERSION")
            if expected_version and expected_version not in version_line:
                raise DocxRenderError("docx_renderer_version_unexpected")
            environment_id = os.environ.get("DOCX_RENDERER_ENVIRONMENT_ID", "unmanaged")
            if (
                not environment_id
                or len(environment_id) > MAX_ENVIRONMENT_ID_LENGTH
                or re.fullmatch(r"[A-Za-z0-9._+-]+", environment_id) is None
            ):
                raise DocxRenderError("docx_renderer_environment_invalid")
            process = subprocess.Popen(
                [
                    executable,
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    "--norestore",
                    "--convert-to",
                    "pdf:writer_pdf_Export",
                    "--outdir",
                    str(output_dir),
                    str(source),
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                _, _ = process.communicate(timeout=RENDER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise DocxRenderError("docx_renderer_timeout") from exc
        except (OSError, IndexError) as exc:
            raise DocxRenderError("docx_renderer_failed") from exc
        if process.returncode != 0:
            raise DocxRenderError("docx_renderer_failed")
        output = output_dir / "source.pdf"
        try:
            output_metadata = output.lstat()
            if (
                not stat.S_ISREG(output_metadata.st_mode)
                or output_metadata.st_size < 100
                or output_metadata.st_size > MAX_RENDERED_PDF_BYTES
            ):
                raise DocxRenderError("docx_renderer_output_invalid")
            pdf_bytes = output.read_bytes()
        except OSError as exc:
            raise DocxRenderError("docx_renderer_output_missing") from exc
    if (
        not pdf_bytes.startswith(b"%PDF-")
        or len(pdf_bytes) < 100
        or len(pdf_bytes) > MAX_RENDERED_PDF_BYTES
    ):
        raise DocxRenderError("docx_renderer_output_invalid")
    safe_version = re.sub(
        r"[^A-Za-z0-9._+-]", "?", version_line.removeprefix("LibreOffice ").split()[0]
    )[:40]
    return RenderedDocx(
        pdf_bytes=pdf_bytes,
        renderer_identity=(
            f"libreoffice:{safe_version}:writer_pdf_Export:opencl-disabled:{environment_id}"
        ),
        source_sha256=sha256(data).hexdigest(),
    )
