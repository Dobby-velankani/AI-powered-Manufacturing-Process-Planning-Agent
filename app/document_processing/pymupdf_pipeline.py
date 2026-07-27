"""
Phase 1 local engineering PDF extraction pipeline using PyMuPDF.

This module does not call Gemini, OpenAI, OCR, or any external API.
It only reads the PDF locally, extracts native text geometry, and
renders preview / crop images for later vision stages.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

import pymupdf

from app.models.drawing_extraction import (
    BoundingBox,
    ExtractedBlock,
    ExtractedTable,
    ExtractedWord,
    NormalizedRegion,
    PageExtraction,
    PDFExtractionResult,
    RenderedCrop,
)


class PDFPipelineError(RuntimeError):
    """Raised when a PDF cannot be processed safely."""


class EngineeringPDFPipeline:
    """
    Local PyMuPDF pipeline for engineering drawing PDFs.

    Default crop regions are generic overlapping templates only.
    They will later be replaced by template-based regions,
    anchor-based detection, user-selected regions, or
    vision-assisted region detection.
    """

    def __init__(
        self,
        output_root: str | Path = "outputs/pdf_pipeline",
        preview_dpi: int = 144,
        crop_dpi: int = 350,
        maximum_render_dimension_pixels: int = 7000,
        maximum_file_size_mb: int = 100,
        maximum_pages: int = 100,
        meaningful_text_character_threshold: int = 30,
        extract_tables: bool = True,
        extract_vector_drawing_count: bool = True,
    ) -> None:
        if preview_dpi < 72:
            raise ValueError("preview_dpi must be at least 72.")
        if crop_dpi < 72:
            raise ValueError("crop_dpi must be at least 72.")
        if maximum_render_dimension_pixels < 1000:
            raise ValueError(
                "maximum_render_dimension_pixels must be "
                "at least 1000."
            )

        self.output_root = Path(output_root)
        self.preview_dpi = preview_dpi
        self.crop_dpi = crop_dpi
        self.maximum_render_dimension_pixels = (
            maximum_render_dimension_pixels
        )
        self.maximum_file_size_bytes = (
            maximum_file_size_mb * 1024 * 1024
        )
        self.maximum_pages = maximum_pages
        self.meaningful_text_character_threshold = (
            meaningful_text_character_threshold
        )
        self.extract_tables = extract_tables
        self.extract_vector_drawing_count = (
            extract_vector_drawing_count
        )

        self.output_root.mkdir(parents=True, exist_ok=True)

    def process(
        self,
        pdf_path: str | Path,
        regions: list[NormalizedRegion] | None = None,
        use_default_engineering_regions: bool = True,
        password: str | None = None,
    ) -> PDFExtractionResult:
        """
        Extract native text, geometry, tables, and rendered crops
        from an engineering PDF.
        """

        source_path = Path(pdf_path).expanduser().resolve()
        self._validate_path(source_path)

        file_size = source_path.stat().st_size
        if file_size > self.maximum_file_size_bytes:
            raise PDFPipelineError(
                "PDF is larger than the configured maximum size of "
                f"{self.maximum_file_size_bytes // (1024 * 1024)} MB."
            )

        file_hash = self.calculate_sha256(source_path)
        safe_stem = self.safe_file_name(source_path.stem)
        output_directory = (
            self.output_root
            / f"{safe_stem}_{file_hash[:12]}"
        )
        output_directory.mkdir(parents=True, exist_ok=True)

        document: pymupdf.Document | None = None
        document_warnings: list[str] = []

        try:
            try:
                document = pymupdf.open(source_path)
            except Exception as exc:
                raise PDFPipelineError(
                    f"Unable to open PDF: {exc}"
                ) from exc

            if document.needs_pass:
                if not password:
                    raise PDFPipelineError(
                        "The PDF is password protected. "
                        "Supply a valid password."
                    )
                authenticated = document.authenticate(password)
                if not authenticated:
                    raise PDFPipelineError(
                        "The supplied PDF password was rejected."
                    )

            if document.page_count == 0:
                raise PDFPipelineError(
                    "The PDF contains no pages."
                )

            if document.page_count > self.maximum_pages:
                raise PDFPipelineError(
                    "The PDF contains more than the configured "
                    f"maximum of {self.maximum_pages} pages."
                )

            selected_regions = regions
            if selected_regions is None:
                if use_default_engineering_regions:
                    selected_regions = (
                        self.default_engineering_regions(
                            document.page_count
                        )
                    )
                else:
                    selected_regions = []

            pages: list[PageExtraction] = []

            for page_index in range(document.page_count):
                page_number = page_index + 1
                page_regions = [
                    region
                    for region in selected_regions
                    if region.page_number == page_number
                ]

                page_extraction = self._process_page(
                    document=document,
                    page_index=page_index,
                    output_directory=output_directory,
                    regions=page_regions,
                )
                pages.append(page_extraction)
                document_warnings.extend(
                    [
                        f"Page {page_number}: {warning}"
                        for warning in page_extraction.warnings
                    ]
                )

            total_native_characters = sum(
                page.native_character_count for page in pages
            )
            total_native_words = sum(
                page.native_word_count for page in pages
            )
            meaningful_pages = sum(
                1
                for page in pages
                if page.has_meaningful_native_text
            )
            contains_meaningful_native_text = (
                meaningful_pages > 0
            )
            likely_scanned_document = (
                meaningful_pages
                < math.ceil(len(pages) / 2)
            )

            metadata = document.metadata or {}
            cleaned_metadata = self._clean_all_metadata(
                metadata
            )

            result = PDFExtractionResult(
                source_path=source_path,
                file_name=source_path.name,
                file_size_bytes=file_size,
                sha256=file_hash,
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
                creator=self._clean_metadata(
                    metadata.get("creator")
                ),
                producer=self._clean_metadata(
                    metadata.get("producer")
                ),
                total_native_characters=total_native_characters,
                total_native_words=total_native_words,
                contains_meaningful_native_text=(
                    contains_meaningful_native_text
                ),
                likely_scanned_document=likely_scanned_document,
                output_directory=output_directory,
                manifest_path=output_directory / "manifest.json",
                pages=pages,
                warnings=document_warnings,
                additional_metadata=cleaned_metadata,
            )

            manifest_path = output_directory / "manifest.json"
            manifest_path.write_text(
                result.model_dump_json(indent=2),
                encoding="utf-8",
            )

            combined_text_path = (
                output_directory / "combined_native_text.txt"
            )
            combined_text_path.write_text(
                self._format_combined_text(pages),
                encoding="utf-8",
            )

            return result

        finally:
            if document is not None:
                document.close()

    def _process_page(
        self,
        document: pymupdf.Document,
        page_index: int,
        output_directory: Path,
        regions: list[NormalizedRegion],
    ) -> PageExtraction:
        page = document.load_page(page_index)
        page_number = page_index + 1
        page_folder = (
            output_directory / f"page_{page_number:03d}"
        )
        crops_folder = page_folder / "crops"
        page_folder.mkdir(parents=True, exist_ok=True)
        crops_folder.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []

        native_text = page.get_text("text", sort=True) or ""
        words = self._extract_words(page, warnings)
        blocks = self._extract_blocks(page, warnings)

        native_character_count = self._count_native_characters(
            native_text
        )
        native_word_count = len(words)
        has_meaningful_native_text = (
            native_character_count
            >= self.meaningful_text_character_threshold
        )
        likely_scanned_page = not has_meaningful_native_text

        tables: list[ExtractedTable] = []
        if self.extract_tables:
            tables = self._extract_tables(page, warnings)

        vector_drawing_count = 0
        if self.extract_vector_drawing_count:
            vector_drawing_count = (
                self._count_vector_drawings(page, warnings)
            )

        preview_image_path: Path | None = None
        try:
            preview_image_path = self._render_page_preview(
                page=page,
                page_number=page_number,
                page_folder=page_folder,
            )
        except Exception as exc:
            warnings.append(
                f"Preview rendering failed: {exc}"
            )

        crops: list[RenderedCrop] = []
        for region in regions:
            try:
                crop = self._render_crop(
                    page=page,
                    region=region,
                    crops_folder=crops_folder,
                )
                crops.append(crop)
            except Exception as exc:
                warnings.append(
                    f"Crop '{region.name}' failed: {exc}"
                )

        native_text_path = (
            page_folder
            / f"page_{page_number:03d}_native_text.txt"
        )
        native_text_path.write_text(
            native_text,
            encoding="utf-8",
        )

        return PageExtraction(
            page_number=page_number,
            width_points=float(page.rect.width),
            height_points=float(page.rect.height),
            rotation_degrees=int(page.rotation),
            native_text=native_text,
            native_character_count=native_character_count,
            native_word_count=native_word_count,
            has_meaningful_native_text=has_meaningful_native_text,
            likely_scanned_page=likely_scanned_page,
            words=words,
            blocks=blocks,
            tables=tables,
            vector_drawing_count=vector_drawing_count,
            preview_image_path=preview_image_path,
            crops=crops,
            warnings=warnings,
        )

    def _extract_words(
        self,
        page: pymupdf.Page,
        warnings: list[str],
    ) -> list[ExtractedWord]:
        words: list[ExtractedWord] = []

        try:
            raw_words = page.get_text("words", sort=True) or []
        except Exception as exc:
            warnings.append(f"Word extraction failed: {exc}")
            return words

        for raw_word in raw_words:
            try:
                if not isinstance(raw_word, (list, tuple)):
                    continue
                if len(raw_word) < 8:
                    continue

                text = str(raw_word[4]).strip()
                if not text:
                    continue

                x0 = float(raw_word[0])
                y0 = float(raw_word[1])
                x1 = float(raw_word[2])
                y1 = float(raw_word[3])
                if x1 <= x0 or y1 <= y0:
                    continue

                words.append(
                    ExtractedWord(
                        text=text,
                        bbox=BoundingBox(
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        ),
                        block_number=int(raw_word[5]),
                        line_number=int(raw_word[6]),
                        word_number=int(raw_word[7]),
                    )
                )
            except Exception:
                continue

        return words

    def _extract_blocks(
        self,
        page: pymupdf.Page,
        warnings: list[str],
    ) -> list[ExtractedBlock]:
        blocks: list[ExtractedBlock] = []

        try:
            raw_blocks = page.get_text("blocks", sort=True) or []
        except Exception as exc:
            warnings.append(f"Block extraction failed: {exc}")
            return blocks

        for index, raw_block in enumerate(raw_blocks):
            try:
                if not isinstance(raw_block, (list, tuple)):
                    continue
                if len(raw_block) < 7:
                    continue

                # Text blocks only (type 0). Image blocks are type 1.
                block_type = int(raw_block[6])
                if block_type != 0:
                    continue

                text = str(raw_block[4]).strip()
                if not text:
                    continue

                x0 = float(raw_block[0])
                y0 = float(raw_block[1])
                x1 = float(raw_block[2])
                y1 = float(raw_block[3])
                if x1 <= x0 or y1 <= y0:
                    continue

                block_number = (
                    int(raw_block[5])
                    if len(raw_block) > 5
                    else index
                )

                blocks.append(
                    ExtractedBlock(
                        text=text,
                        bbox=BoundingBox(
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        ),
                        block_number=block_number,
                        block_type=block_type,
                    )
                )
            except Exception:
                continue

        return blocks

    def _extract_tables(
        self,
        page: pymupdf.Page,
        warnings: list[str],
    ) -> list[ExtractedTable]:
        tables: list[ExtractedTable] = []

        try:
            finder = page.find_tables()
        except Exception as exc:
            warnings.append(
                f"Table extraction unavailable or failed: {exc}"
            )
            return tables

        try:
            detected = list(getattr(finder, "tables", finder) or [])
        except Exception as exc:
            warnings.append(
                f"Unable to enumerate detected tables: {exc}"
            )
            return tables

        for table_index, table in enumerate(detected, start=1):
            try:
                bbox_values = getattr(table, "bbox", None)
                if bbox_values is None:
                    continue

                x0, y0, x1, y1 = (
                    float(bbox_values[0]),
                    float(bbox_values[1]),
                    float(bbox_values[2]),
                    float(bbox_values[3]),
                )
                if x1 <= x0 or y1 <= y0:
                    continue

                extracted_rows = table.extract()
                cleaned_rows: list[list[str | None]] = []

                for row in extracted_rows or []:
                    cleaned_row: list[str | None] = []
                    for cell in row:
                        if cell is None:
                            cleaned_row.append(None)
                        else:
                            text = str(cell).strip()
                            cleaned_row.append(
                                text if text else None
                            )
                    cleaned_rows.append(cleaned_row)

                row_count = len(cleaned_rows)
                column_count = max(
                    (len(row) for row in cleaned_rows),
                    default=0,
                )

                markdown: str | None = None
                try:
                    markdown = table.to_markdown()
                except Exception:
                    markdown = None

                tables.append(
                    ExtractedTable(
                        table_number=table_index,
                        bbox=BoundingBox(
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        ),
                        row_count=row_count,
                        column_count=column_count,
                        rows=cleaned_rows,
                        markdown=markdown,
                    )
                )
            except Exception as exc:
                warnings.append(
                    f"Table {table_index} processing failed: {exc}"
                )
                continue

        return tables

    def _count_vector_drawings(
        self,
        page: pymupdf.Page,
        warnings: list[str],
    ) -> int:
        try:
            return len(page.get_drawings())
        except Exception as exc:
            warnings.append(
                f"Vector drawing count failed: {exc}"
            )
            return 0

    def _render_page_preview(
        self,
        page: pymupdf.Page,
        page_number: int,
        page_folder: Path,
    ) -> Path:
        actual_dpi = self.safe_dpi(
            longest_points=max(
                float(page.rect.width),
                float(page.rect.height),
            ),
            requested_dpi=self.preview_dpi,
            maximum_pixels=self.maximum_render_dimension_pixels,
        )

        pixmap = page.get_pixmap(
            dpi=actual_dpi,
            alpha=False,
        )
        image_path = (
            page_folder
            / f"page_{page_number:03d}_preview.png"
        )
        pixmap.save(image_path)
        return image_path

    def _render_crop(
        self,
        page: pymupdf.Page,
        region: NormalizedRegion,
        crops_folder: Path,
    ) -> RenderedCrop:
        absolute = region.to_absolute(
            page_width=float(page.rect.width),
            page_height=float(page.rect.height),
        )

        clip = pymupdf.Rect(absolute.as_tuple())
        clip = clip & page.rect
        if clip.is_empty or clip.width <= 0 or clip.height <= 0:
            raise PDFPipelineError(
                f"Crop '{region.name}' has an empty "
                "intersection with the page."
            )

        actual_dpi = self.safe_dpi(
            longest_points=max(float(clip.width), float(clip.height)),
            requested_dpi=self.crop_dpi,
            maximum_pixels=self.maximum_render_dimension_pixels,
        )

        pixmap = page.get_pixmap(
            dpi=actual_dpi,
            clip=clip,
            alpha=False,
        )

        safe_name = self.safe_file_name(region.name)
        image_path = crops_folder / f"{safe_name}.png"
        pixmap.save(image_path)

        extracted_text = (
            page.get_text(
                "text",
                clip=clip,
                sort=True,
            )
            or ""
        )

        raw_words = (
            page.get_text(
                "words",
                clip=clip,
                sort=True,
            )
            or []
        )
        native_word_count = 0
        for raw_word in raw_words:
            try:
                if (
                    isinstance(raw_word, (list, tuple))
                    and len(raw_word) >= 5
                    and str(raw_word[4]).strip()
                ):
                    native_word_count += 1
            except Exception:
                continue

        return RenderedCrop(
            name=region.name,
            purpose=region.purpose,
            page_number=region.page_number,
            bbox=BoundingBox(
                x0=float(clip.x0),
                y0=float(clip.y0),
                x1=float(clip.x1),
                y1=float(clip.y1),
            ),
            requested_dpi=self.crop_dpi,
            actual_dpi=actual_dpi,
            image_path=image_path,
            extracted_text=extracted_text,
            native_word_count=native_word_count,
        )

    def _validate_path(self, source_path: Path) -> None:
        if not source_path.exists():
            raise PDFPipelineError(
                f"PDF does not exist: {source_path}"
            )
        if not source_path.is_file():
            raise PDFPipelineError(
                f"Path is not a file: {source_path}"
            )
        if source_path.suffix.lower() != ".pdf":
            raise PDFPipelineError(
                "Only PDF files are supported."
            )

    @staticmethod
    def calculate_sha256(file_path: Path) -> str:
        """Calculate SHA-256 using streamed 1 MB blocks."""

        digest = hashlib.sha256()
        with file_path.open("rb") as file_handle:
            for block in iter(
                lambda: file_handle.read(1024 * 1024),
                b"",
            ):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def safe_file_name(value: str) -> str:
        """
        Create a filesystem-safe name.

        Allows letters, numbers, period, underscore, and hyphen.
        """

        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
        cleaned = cleaned.strip("._")
        return cleaned or "unnamed"

    @staticmethod
    def safe_dpi(
        longest_points: float,
        requested_dpi: int,
        maximum_pixels: int,
    ) -> int:
        """
        Reduce DPI so the longest rendered dimension stays within
        maximum_pixels. Never reduce below 96.
        """

        if longest_points <= 0:
            return max(96, requested_dpi)

        estimated = longest_points * requested_dpi / 72.0
        if estimated <= maximum_pixels:
            return requested_dpi

        reduced = int(
            math.floor(
                maximum_pixels * 72.0 / longest_points
            )
        )
        return max(96, reduced)

    @staticmethod
    def default_engineering_regions(
        page_count: int,
    ) -> list[NormalizedRegion]:
        """
        Generic overlapping crop templates for every page.

        These are temporary starting regions only. Future phases
        will replace them with template-based regions, anchor-based
        detection, user-selected regions, and vision-assisted
        region detection.
        """

        regions: list[NormalizedRegion] = []

        for page_number in range(1, page_count + 1):
            regions.extend(
                [
                    NormalizedRegion(
                        name="main_drawing_area",
                        page_number=page_number,
                        x0=0.00,
                        y0=0.00,
                        x1=0.72,
                        y1=0.88,
                        purpose=(
                            "Main orthographic, sectional and "
                            "detail drawing views."
                        ),
                    ),
                    NormalizedRegion(
                        name="right_information_panel",
                        page_number=page_number,
                        x0=0.65,
                        y0=0.00,
                        x1=1.00,
                        y1=0.82,
                        purpose=(
                            "Right-side notes, gear data, "
                            "tolerance data or technical tables."
                        ),
                    ),
                    NormalizedRegion(
                        name="title_block",
                        page_number=page_number,
                        x0=0.60,
                        y0=0.72,
                        x1=1.00,
                        y1=1.00,
                        purpose=(
                            "Drawing number, revision, material, "
                            "part name, scale and approval data."
                        ),
                    ),
                ]
            )

        return regions

    @staticmethod
    def _count_native_characters(text: str) -> int:
        compact = re.sub(r"[\r\n]+", "", text).strip()
        compact = re.sub(r"\s+", "", compact)
        return len(compact)

    @staticmethod
    def _clean_metadata(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    @classmethod
    def _clean_all_metadata(
        cls,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in metadata.items():
            cleaned_value = cls._clean_metadata(value)
            if cleaned_value is not None:
                cleaned[str(key)] = cleaned_value
        return cleaned

    @staticmethod
    def _format_combined_text(
        pages: list[PageExtraction],
    ) -> str:
        sections: list[str] = []
        for page in pages:
            sections.append(
                f"--- PAGE {page.page_number} ---\n"
                f"{page.native_text.strip()}"
            )
        return "\n\n".join(sections).strip() + "\n"
