"""
Deprecated legacy PDF reader.

The main application now uses EngineeringPDFPipeline.
Keep this module temporarily for rollback and compatibility.
Do not use it for new drawing-analysis workflows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf


class PDFReaderError(RuntimeError):
    """Raised when a PDF cannot be safely opened or processed."""


@dataclass(frozen=True)
class PDFPageData:
    page_number: int
    width_points: float
    height_points: float
    rotation: int
    extracted_text: str
    image_path: Path


@dataclass(frozen=True)
class PDFDocumentData:
    source_path: Path
    file_name: str
    file_size_bytes: int
    file_sha256: str
    page_count: int
    title: str | None
    author: str | None
    subject: str | None
    pages: list[PDFPageData]

    @property
    def combined_text(self) -> str:
        sections: list[str] = []

        for page in self.pages:
            sections.append(
                f"--- PAGE {page.page_number} ---\n"
                f"{page.extracted_text.strip()}"
            )

        return "\n\n".join(sections).strip()


class PDFReader:
    def __init__(
        self,
        output_directory: str | Path = "extracted_pages",
        render_dpi: int = 200,
        maximum_file_size_mb: int = 50,
        maximum_pages: int = 100,
    ) -> None:
        self.output_directory = Path(output_directory)
        self.render_dpi = render_dpi
        self.maximum_file_size_bytes = (
            maximum_file_size_mb * 1024 * 1024
        )
        self.maximum_pages = maximum_pages

        self.output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    def read(
        self,
        pdf_path: str | Path,
    ) -> PDFDocumentData:
        source_path = Path(pdf_path).expanduser().resolve()

        self._validate_path(source_path)

        file_size = source_path.stat().st_size

        if file_size > self.maximum_file_size_bytes:
            raise PDFReaderError(
                "PDF is larger than the configured maximum size of "
                f"{self.maximum_file_size_bytes // (1024 * 1024)} MB."
            )

        file_hash = self._calculate_sha256(source_path)

        document_output_directory = (
            self.output_directory
            / f"{source_path.stem}_{file_hash[:12]}"
        )

        document_output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            document = pymupdf.open(source_path)
        except Exception as exc:
            raise PDFReaderError(
                f"Unable to open PDF: {exc}"
            ) from exc

        try:
            if document.needs_pass:
                raise PDFReaderError(
                    "The PDF is password protected."
                )

            if document.page_count == 0:
                raise PDFReaderError(
                    "The PDF contains no pages."
                )

            if document.page_count > self.maximum_pages:
                raise PDFReaderError(
                    "The PDF contains more than the configured maximum "
                    f"of {self.maximum_pages} pages."
                )

            pages: list[PDFPageData] = []

            for page_index in range(document.page_count):
                page = document.load_page(page_index)

                extracted_text = page.get_text(
                    "text",
                    sort=True,
                )

                image_path = (
                    document_output_directory
                    / f"page_{page_index + 1:03d}.png"
                )

                pixmap = page.get_pixmap(
                    dpi=self.render_dpi,
                    alpha=False,
                )

                pixmap.save(image_path)

                pages.append(
                    PDFPageData(
                        page_number=page_index + 1,
                        width_points=float(page.rect.width),
                        height_points=float(page.rect.height),
                        rotation=int(page.rotation),
                        extracted_text=extracted_text,
                        image_path=image_path,
                    )
                )

            metadata = document.metadata or {}

            return PDFDocumentData(
                source_path=source_path,
                file_name=source_path.name,
                file_size_bytes=file_size,
                file_sha256=file_hash,
                page_count=document.page_count,
                title=self._clean_metadata(
                    metadata.get("title")
                ),
                author=self._clean_metadata(
                    metadata.get("author")
                ),
                subject=self._clean_metadata(
                    metadata.get("subject")
                ),
                pages=pages,
            )

        finally:
            document.close()

    def _validate_path(
        self,
        source_path: Path,
    ) -> None:
        if not source_path.exists():
            raise PDFReaderError(
                f"PDF does not exist: {source_path}"
            )

        if not source_path.is_file():
            raise PDFReaderError(
                f"Path is not a file: {source_path}"
            )

        if source_path.suffix.lower() != ".pdf":
            raise PDFReaderError(
                "Only PDF files are supported."
            )

    @staticmethod
    def _calculate_sha256(
        file_path: Path,
    ) -> str:
        digest = hashlib.sha256()

        with file_path.open("rb") as file_handle:
            for block in iter(
                lambda: file_handle.read(1024 * 1024),
                b"",
            ):
                digest.update(block)

        return digest.hexdigest()

    @staticmethod
    def _clean_metadata(
        value: object,
    ) -> str | None:
        if not isinstance(value, str):
            return None

        cleaned = value.strip()

        return cleaned or None
