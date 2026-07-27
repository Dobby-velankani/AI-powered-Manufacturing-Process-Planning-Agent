"""
Deprecated legacy drawing data model.

DrawingPlanningInput (app/models/drawing_planning_input.py) is the
current structured drawing input model.
Keep this module temporarily for rollback and compatibility.
Do not use it for new drawing-analysis workflows.
"""

from pydantic import BaseModel, Field


class DrawingDimension(BaseModel):
    nominal_value: str
    tolerance: str | None = None
    unit: str = "mm"
    feature: str
    page_number: int
    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )


class DrawingHole(BaseModel):
    quantity: int | None = None
    diameter: str | None = None
    thread: str | None = None
    depth: str | None = None
    tolerance: str | None = None
    position_description: str | None = None
    page_number: int


class DrawingData(BaseModel):
    document_name: str
    drawing_number: str | None = None
    revision: str | None = None
    part_name: str | None = None
    material: str | None = None
    quantity: int | None = None

    dimensions: list[DrawingDimension] = Field(
        default_factory=list,
    )
    holes: list[DrawingHole] = Field(
        default_factory=list,
    )

    surface_finish_requirements: list[str] = Field(
        default_factory=list,
    )
    geometric_tolerances: list[str] = Field(
        default_factory=list,
    )
    heat_treatment_requirements: list[str] = Field(
        default_factory=list,
    )
    coating_requirements: list[str] = Field(
        default_factory=list,
    )
    welding_requirements: list[str] = Field(
        default_factory=list,
    )
    general_notes: list[str] = Field(
        default_factory=list,
    )

    unclear_items: list[str] = Field(
        default_factory=list,
    )
    contradictions: list[str] = Field(
        default_factory=list,
    )
    missing_critical_information: list[str] = Field(
        default_factory=list,
    )

    requires_human_drawing_review: bool = True
