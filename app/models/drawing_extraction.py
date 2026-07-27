"""Pydantic models for Phase 1 local PyMuPDF drawing extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class BoundingBox(BaseModel):
    """Axis-aligned rectangle in PDF page coordinates (points)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_extent(self) -> BoundingBox:
        if self.x1 <= self.x0:
            raise ValueError("x1 must be greater than x0.")
        if self.y1 <= self.y0:
            raise ValueError("y1 must be greater than y0.")
        return self

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


class NormalizedRegion(BaseModel):
    """
    Crop region expressed as fractions of page width and height.

    Coordinates use the PDF origin at the top-left of the page for
    normalized planning convenience (0,0 top-left → 1,1 bottom-right).
    Absolute conversion maps into PDF point space (origin top-left of
    the unrotated page rectangle used by PyMuPDF page.rect).
    """

    name: str
    page_number: int = Field(ge=1)
    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    purpose: str = ""

    @model_validator(mode="after")
    def validate_extent(self) -> NormalizedRegion:
        if self.x1 <= self.x0:
            raise ValueError("x1 must be greater than x0.")
        if self.y1 <= self.y0:
            raise ValueError("y1 must be greater than y0.")
        return self

    def to_absolute(
        self,
        page_width: float,
        page_height: float,
    ) -> BoundingBox:
        """Convert normalized fractions into absolute page coordinates."""

        return BoundingBox(
            x0=self.x0 * page_width,
            y0=self.y0 * page_height,
            x1=self.x1 * page_width,
            y1=self.y1 * page_height,
        )


class ExtractedWord(BaseModel):
    """A single native text word with coordinates."""

    text: str
    bbox: BoundingBox
    block_number: int
    line_number: int
    word_number: int


class ExtractedBlock(BaseModel):
    """A native text block with coordinates."""

    text: str
    bbox: BoundingBox
    block_number: int
    block_type: int


class ExtractedTable(BaseModel):
    """A table candidate detected by PyMuPDF."""

    table_number: int
    bbox: BoundingBox
    row_count: int
    column_count: int
    rows: list[list[str | None]] = Field(default_factory=list)
    markdown: str | None = None


class RenderedCrop(BaseModel):
    """A rendered high-resolution crop of a page region."""

    name: str
    purpose: str
    page_number: int
    bbox: BoundingBox
    requested_dpi: int
    actual_dpi: int
    image_path: Path
    extracted_text: str
    native_word_count: int


class PageExtraction(BaseModel):
    """All Phase 1 extraction results for one PDF page."""

    page_number: int
    width_points: float
    height_points: float
    rotation_degrees: int
    native_text: str
    native_character_count: int
    native_word_count: int
    has_meaningful_native_text: bool
    likely_scanned_page: bool
    words: list[ExtractedWord] = Field(default_factory=list)
    blocks: list[ExtractedBlock] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    vector_drawing_count: int = 0
    preview_image_path: Path | None = None
    crops: list[RenderedCrop] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PDFExtractionResult(BaseModel):
    """Complete Phase 1 extraction result for one engineering PDF."""

    source_path: Path
    file_name: str
    file_size_bytes: int
    sha256: str
    page_count: int
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    producer: str | None = None
    total_native_characters: int
    total_native_words: int
    contains_meaningful_native_text: bool
    likely_scanned_document: bool
    output_directory: Path
    manifest_path: Path
    pages: list[PageExtraction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extraction_version: str = "phase_1_pymupdf_v1"
    additional_metadata: dict[str, Any] = Field(default_factory=dict)
