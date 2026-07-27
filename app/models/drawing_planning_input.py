"""
Structured drawing input models for process planning.

These models provide full traceability from drawing extraction to
manufacturing plan generation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class PlanningRequirement(BaseModel):
    """
    A single traceable requirement extracted from a drawing.

    Preserves the raw callout, interpretation, evidence status,
    source location, and confidence.
    """

    category: str
    raw_callout: str
    interpreted_requirement: str | None = None
    feature_name: str | None = None
    page_number: int | None = None
    view_reference: str | None = None
    crop_name: str | None = None
    evidence_status: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class DrawingPlanningInput(BaseModel):
    """
    Structured drawing data ready for process planning.

    Contains confirmed requirements, uncertain observations,
    critical conflicts, and convenience lists.

    Safety flags are locally enforced and cannot be overridden.
    """

    # Identification fields
    drawing_number: str | None = None
    part_name: str | None = None
    material: str | None = None
    revision: str | None = None
    scale: str | None = None
    general_tolerance: str | None = None
    source_file_name: str
    document_sha256: str

    # Structured traceable requirements
    requirements: list[PlanningRequirement] = Field(
        default_factory=list,
        description="Confirmed observations (corroborated, normalized_support)",
    )
    uncertain_requirements: list[PlanningRequirement] = Field(
        default_factory=list,
        description="Partial/vision-only/uncertain/conflict observations",
    )
    critical_conflicts: list[str] = Field(
        default_factory=list,
        description="Human-readable critical conflict descriptions",
    )

    # Status and safety flags
    input_status: str = "review_required"
    requires_user_review: bool = True
    safe_to_release_for_production: bool = False

    # Convenience string lists (backward compatibility)
    dimensions: list[str] = Field(default_factory=list)
    holes: list[str] = Field(default_factory=list)
    surface_finish_requirements: list[str] = Field(default_factory=list)
    geometric_tolerances: list[str] = Field(default_factory=list)
    heat_treatment_requirements: list[str] = Field(default_factory=list)
    general_notes: list[str] = Field(default_factory=list)
    unclear_items: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_safety_flags(self) -> DrawingPlanningInput:
        """
        Locally enforce safety flags.

        These cannot be disabled by external input.
        """
        self.requires_user_review = True
        self.safe_to_release_for_production = False
        return self
