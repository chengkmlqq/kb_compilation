"""LibreOffice helpers for legacy or unusual Word uploads (.doc / .rtf / .odt).

Mirrors ``excel_convert`` / ``ppt_convert``: legacy Word binaries and
non-OOXML Word formats cannot be parsed (or previewed) by OOXML-only tools
(``python-docx`` / ``docx-preview``), so convert them to ``.docx`` first.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from docreader.parser.excel_convert import find_soffice

logger = logging.getLogger(__name__)

# Legacy binary Office (OLE Compound File) vs. OOXML (ZIP) magic numbers.
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"

# Word formats that OOXML-only readers cannot open directly.
_WORD_CONVERT_EXTS = ("doc", "rtf", "odt")


def is_ole_compound(content: bytes) -> bool:
    """True when content is a legacy binary Office file (OLE compound file)."""
    return len(content) >= len(_OLE_MAGIC) and content.startswith(_OLE_MAGIC)


def is_zip_openxml(content: bytes) -> bool:
    """True when content is an OOXML container (ZIP)."""
    return len(content) >= len(_ZIP_MAGIC) and content.startswith(_ZIP_MAGIC)


def needs_doc_to_docx_conversion(content: bytes, file_type: str | None) -> bool:
    """True when the content must be converted to .docx before OOXML readers.

    OOXML (``PK`` ZIP) content is already readable, regardless of the file
    extension (Word/WPS rename legacy files to ``.docx`` and vice versa), so it
    never needs conversion. Otherwise a legacy Word extension (``doc``/``rtf``/
    ``odt``) or an OLE payload does.
    """
    if is_zip_openxml(content):
        return False
    ext = (file_type or "").lstrip(".").lower()
    if ext in _WORD_CONVERT_EXTS:
        return True
    return is_ole_compound(content)


def convert_doc_to_docx_bytes(content: bytes, suffix: str = ".doc") -> bytes | None:
    """Convert legacy/non-OOXML Word bytes to DOCX using LibreOffice, if available.

    Returns the converted DOCX bytes, or ``None`` when LibreOffice is not
    installed or the conversion fails (callers fall back to download).
    """
    soffice = find_soffice()
    if not soffice:
        logger.warning("LibreOffice (soffice) not found; cannot convert %s", suffix)
        return None

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as profile_dir:
            src = os.path.join(temp_dir, f"input{suffix}")
            with open(src, "wb") as handle:
                handle.write(content)

            user_installation = Path(profile_dir).as_uri()
            cmd = [
                soffice,
                "--headless",
                f"-env:UserInstallation={user_installation}",
                "--convert-to",
                "docx",
                "--outdir",
                temp_dir,
                src,
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("LibreOffice DOC convert failed to start: %s", exc)
                return None

            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="ignore")
                logger.warning(
                    "LibreOffice DOC convert failed (attempt %s/%s): %s",
                    attempt,
                    max_attempts,
                    stderr,
                )
                if attempt < max_attempts:
                    time.sleep(0.5 * attempt)
                    continue
                return None

            for name in os.listdir(temp_dir):
                if name.endswith(".docx"):
                    with open(os.path.join(temp_dir, name), "rb") as handle:
                        converted = handle.read()
                    logger.info(
                        "Converted Word document via LibreOffice (%s -> docx, %d bytes)",
                        suffix,
                        len(converted),
                    )
                    return converted

            if attempt < max_attempts:
                time.sleep(0.5 * attempt)
    return None
