from app.models.process_plan import ProcessPlan


FORBIDDEN_CNC_MARKERS = (
    "G00",
    "G01",
    "G02",
    "G03",
    "M03",
    "M04",
    "M05",
    "M06",
    "M30",
)


def validate_process_plan(
    plan: ProcessPlan,
) -> list[str]:
    issues: list[str] = []

    if not plan.operations:
        issues.append(
            "ERROR: The plan contains no operations."
        )

    sequences = [
        operation.sequence
        for operation in plan.operations
    ]

    if len(sequences) != len(set(sequences)):
        issues.append(
            "ERROR: Duplicate operation sequence "
            "numbers were detected."
        )

    if sequences != sorted(sequences):
        issues.append(
            "ERROR: Operations are not arranged "
            "in sequence order."
        )

    serialized = plan.model_dump_json().upper()

    for marker in FORBIDDEN_CNC_MARKERS:
        if marker in serialized:
            issues.append(
                "ERROR: Machine-code-like content "
                f"was detected: {marker}"
            )

    if plan.cnc_code_status != "not_generated":
        issues.append(
            "ERROR: CNC code generation is not "
            "permitted at this project stage."
        )

    if not plan.human_approval_required:
        issues.append(
            "ERROR: Human engineering approval "
            "cannot be disabled."
        )

    if not plan.inspection_plan:
        issues.append(
            "WARNING: No inspection plan was generated."
        )

    return issues
