"""Pydantic models for Phase 2 Gemini crop vision analysis."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.drawing_extraction import BoundingBox


class ObservedTitleBlockField(BaseModel):
    field_name: str
    raw_value: str
    interpreted_value: str | None = None
    location_description: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: list[str] = Field(default_factory=list)
    verification_required: bool = True


class ObservedCallout(BaseModel):
    category: str
    raw_callout: str
    feature_name: str | None = None
    interpretation: str | None = None
    nominal_value: str | None = None
    upper_tolerance: str | None = None
    lower_tolerance: str | None = None
    tolerance_class: str | None = None
    fit_class: str | None = None
    quantity: int | None = None
    unit: str | None = None
    gdt_symbol: str | None = None
    datum_references: list[str] = Field(default_factory=list)
    view_reference: str | None = None
    location_description: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: list[str] = Field(default_factory=list)
    directly_visible: bool = True
    verification_required: bool = True


class ObservedTableEntry(BaseModel):
    table_name: str | None = None
    parameter_name: str
    symbol: str | None = None
    raw_value: str
    unit: str | None = None
    raw_row_text: str | None = None
    location_description: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: list[str] = Field(default_factory=list)
    verification_required: bool = True


class ObservedDrawingNote(BaseModel):
    category: str
    raw_text: str
    interpretation: str | None = None
    location_description: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: list[str] = Field(default_factory=list)
    verification_required: bool = True


class GeminiCropExtraction(BaseModel):
    crop_summary: str
    visible_view_references: list[str] = Field(default_factory=list)
    title_block_fields: list[ObservedTitleBlockField] = Field(
        default_factory=list,
    )
    callouts: list[ObservedCallout] = Field(default_factory=list)
    table_entries: list[ObservedTableEntry] = Field(
        default_factory=list,
    )
    notes: list[ObservedDrawingNote] = Field(default_factory=list)
    unclear_items: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    possible_duplicate_items: list[str] = Field(
        default_factory=list,
    )
    requires_human_drawing_review: bool = True
    safe_to_release_for_production: bool = False

    @model_validator(mode="after")
    def enforce_safety_flags(self) -> GeminiCropExtraction:
        self.requires_human_drawing_review = True
        self.safe_to_release_for_production = False
        return self


class VisionProviderMetadata(BaseModel):
    model_name: str
    interaction_id: str | None = None
    request_timestamp_utc: str
    response_timestamp_utc: str
    prompt_version: str
    image_sha256: str
    image_size_bytes: int
    image_mime_type: str
    uploaded_file_name: str | None = None
    uploaded_file_uri: str | None = None
    uploaded_file_deleted: bool | None = None
    cache_hit: bool = False


class VisionCropResult(BaseModel):
    document_sha256: str
    source_file_name: str
    page_number: int
    crop_name: str
    crop_purpose: str
    crop_image_path: Path
    crop_bbox: BoundingBox
    native_crop_text: str
    native_word_count: int
    extraction: GeminiCropExtraction
    provider_metadata: VisionProviderMetadata
    warnings: list[str] = Field(default_factory=list)
    requires_human_drawing_review: bool = True
    safe_to_release_for_production: bool = False

    @model_validator(mode="after")
    def enforce_safety_flags(self) -> VisionCropResult:
        self.requires_human_drawing_review = True
        self.safe_to_release_for_production = False
        self.extraction.requires_human_drawing_review = True
        self.extraction.safe_to_release_for_production = False
        return self


class NativeEvidenceMatch(BaseModel):
    vision_raw_callout: str
    native_text_excerpt: str | None = None
    match_type: str
    match_score: float = Field(ge=0.0, le=1.0)
    normalized_vision_text: str
    normalized_native_text: str | None = None
    critical_tokens_vision: list[str] = Field(default_factory=list)
    critical_tokens_native: list[str] = Field(default_factory=list)
    conflict_reason: str | None = None
    page_number: int
    crop_name: str

    @field_validator("match_type")
    @classmethod
    def validate_match_type(cls, value: str) -> str:
        allowed = {
            "exact_match",
            "normalized_match",
            "partial_match",
            "not_found",
            "potential_conflict",
            "not_applicable",
        }
        if value not in allowed:
            raise ValueError(f"Unsupported match_type: {value}")
        return value


class MergedCalloutObservation(BaseModel):
    canonical_id: str
    primary_observation: ObservedCallout
    duplicate_observations: list[ObservedCallout] = Field(
        default_factory=list,
    )
    source_crops: list[str] = Field(default_factory=list)
    native_evidence: NativeEvidenceMatch
    status: str
    safe_to_use: bool = False
    engineer_verification_required: bool = True
    warnings: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = {
            "corroborated",
            "normalized_support",
            "partial_support",
            "vision_only",
            "uncertain",
            "potential_conflict",
        }
        if value not in allowed:
            raise ValueError(f"Unsupported status: {value}")
        return value

    @model_validator(mode="after")
    def enforce_safety_flags(self) -> MergedCalloutObservation:
        self.safe_to_use = False
        self.engineer_verification_required = True
        return self


class DrawingVisionAnalysis(BaseModel):
    document_sha256: str
    source_file_name: str
    manifest_path: Path
    model_names_used: list[str] = Field(default_factory=list)
    analyzed_crop_count: int
    failed_crop_count: int
    crop_results: list[VisionCropResult] = Field(default_factory=list)
    merged_callouts: list[MergedCalloutObservation] = Field(
        default_factory=list,
    )
    merged_title_block_fields: list[ObservedTitleBlockField] = Field(
        default_factory=list,
    )
    merged_table_entries: list[ObservedTableEntry] = Field(
        default_factory=list,
    )
    merged_notes: list[ObservedDrawingNote] = Field(
        default_factory=list,
    )
    unclear_items: list[str] = Field(default_factory=list)
    processing_warnings: list[str] = Field(default_factory=list)
    requires_human_drawing_review: bool = True
    safe_to_release_for_production: bool = False
    analysis_version: str = "phase_2_gemini_crop_vision_v1"

    @model_validator(mode="after")
    def enforce_safety_flags(self) -> DrawingVisionAnalysis:
        self.requires_human_drawing_review = True
        self.safe_to_release_for_production = False
        return self
