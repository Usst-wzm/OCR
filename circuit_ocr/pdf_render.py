from __future__ import annotations

import re
import subprocess
from pathlib import Path


def get_pdf_page_count(pdf_path: Path, pdfinfo_bin: str = "pdfinfo") -> int:
    result = subprocess.run(
        [pdfinfo_bin, str(pdf_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("Could not determine PDF page count from pdfinfo output")
    return int(match.group(1))


def render_page(
    pdf_path: Path,
    page: int,
    output_dir: Path,
    *,
    dpi: int = 240,
    pdftoppm_bin: str = "pdftoppm",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / f"page_{page:03d}"
    expected = output_dir / f"page_{page:03d}.png"
    if expected.exists():
        return expected

    subprocess.run(
        [
            pdftoppm_bin,
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-png",
            "-r",
            str(dpi),
            str(pdf_path),
            str(prefix),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not expected.exists():
        raise RuntimeError(f"pdftoppm did not create {expected}")
    return expected
