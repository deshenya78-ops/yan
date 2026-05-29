from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class PDFConversionError(RuntimeError):
    """Raised when DOCX to PDF conversion fails."""


def convert_docx_to_pdf(docx_path: Path, output_dir: Path) -> Path:
    """Convert DOCX to PDF with LibreOffice in headless mode."""
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise PDFConversionError(
            "LibreOffice не установлен. Установите LibreOffice на сервере, чтобы бот отправлял PDF."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            executable,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(docx_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    pdf_path = output_dir / f"{docx_path.stem}.pdf"
    if completed.returncode != 0 or not pdf_path.exists():
        details = (completed.stderr or completed.stdout or "неизвестная ошибка").strip()
        raise PDFConversionError(f"Не удалось сконвертировать DOCX в PDF: {details}")
    return pdf_path
