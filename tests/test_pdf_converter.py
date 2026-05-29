from pathlib import Path

import pytest

from services.pdf_converter import PDFConversionError, convert_docx_to_pdf


def test_convert_docx_to_pdf_requires_libreoffice(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("services.pdf_converter.shutil.which", lambda name: None)

    with pytest.raises(PDFConversionError):
        convert_docx_to_pdf(tmp_path / "file.docx", tmp_path)
