from pydantic import BaseModel, Field


class ManufacturingOperation(BaseModel):
    sequence: int = Field(
        ge=1,
        description="Operation sequence number.",
    )

    setup_id: str = Field(
        description="Setup identifier, such as S1 or S2.",
    )

    operation_name: str = Field(
        description="Short operation name.",
    )

    process_type: str = Field(
        description=(
            "Manufacturing process such as sawing, "
            "turning, milling, drilling or grinding."
        ),
    )

    recommended_machine: str = Field(
        description=(
            "Recommended machine or process equipment."
        ),
    )

    workholding: str = Field(
        description=(
            "Recommended holding or fixture method."
        ),
    )

    locating_datum: str = Field(
        description=(
            "Datum or reference used in this operation."
        ),
    )

    input_condition: str = Field(
        description=(
            "Condition of the component before this operation."
        ),
    )

    operation_steps: list[str] = Field(
        description=(
            "Detailed shop-floor steps for the operation."
        ),
    )

    target_features: list[str] = Field(
        description=(
            "Features produced or controlled by this operation."
        ),
    )

    indicative_parameters: list[str] = Field(
        description=(
            "Tentative parameters requiring verification "
            "against the actual machine and tooling."
        ),
    )

    in_process_checks: list[str] = Field(
        description=(
            "Measurements and inspections required during "
            "or immediately after the operation."
        ),
    )

    reason_for_selection: str = Field(
        description=(
            "Engineering reason for choosing the process."
        ),
    )

    reference_case_ids: list[str] = Field(
        description=(
            "Database reference cases supporting this operation."
        ),
    )

    engineer_review_required: bool = Field(
        description=(
            "Whether this operation needs special engineering review."
        ),
    )


class ProcessPlan(BaseModel):
    plan_title: str

    part_interpretation: str

    recommended_raw_material: str

    manufacturing_strategy: str

    operations: list[ManufacturingOperation]

    inspection_plan: list[str]

    tooling_and_fixture_requirements: list[str]

    assumptions: list[str]

    missing_information: list[str]

    manufacturing_risks: list[str]

    alternative_routes: list[str]

    reference_cases_used: list[str]

    cnc_code_status: str = "not_generated"

    human_approval_required: bool = True
