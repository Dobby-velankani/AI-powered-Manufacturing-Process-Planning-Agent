from app.agents.process_planner import (
    ProcessPlannerAgent,
)
from app.document_processing import (
    DrawingAnalysisAdapter,
    EngineeringPDFPipeline,
    GeminiCropAnalyzer,
    GeminiCropAnalyzerError,
    PDFPipelineError,
    format_planning_description,
)
from app.llm.gemini_provider import (
    GeminiProvider,
)
from app.llm.gemini_vision_provider import (
    GeminiVisionProvider,
    GeminiVisionProviderError,
)
from app.models.drawing_planning_input import DrawingPlanningInput
from app.models.process_plan import ProcessPlan
from app.services.knowledge_base import (
    ManufacturingKnowledgeBase,
)
from pathlib import Path


def display_database(
    knowledge_base: ManufacturingKnowledgeBase,
) -> None:
    print("=" * 75)
    print("AI MANUFACTURING PROCESS PLANNER")
    print("=" * 75)

    print(
        f"Loaded reference cases: "
        f"{len(knowledge_base.cases)}"
    )

    print()

    for index, case in enumerate(
        knowledge_base.cases,
        start=1,
    ):
        print(
            f"{index:>2}. "
            f"{case.part_name} "
            f"| Material: {case.material} "
            f"| Case: {case.case_id}"
        )


def display_plan(
    plan: ProcessPlan,
) -> None:
    print()
    print("=" * 75)
    print(plan.plan_title.upper())
    print("=" * 75)

    print("\nPART INTERPRETATION")
    print(plan.part_interpretation)

    print("\nRECOMMENDED RAW MATERIAL")
    print(plan.recommended_raw_material)

    print("\nMANUFACTURING STRATEGY")
    print(plan.manufacturing_strategy)

    print("\nOPERATION SEQUENCE")

    for operation in plan.operations:
        print()
        print(
            f"{operation.sequence}. "
            f"{operation.operation_name}"
        )
        print(
            f"   Setup       : "
            f"{operation.setup_id}"
        )
        print(
            f"   Process     : "
            f"{operation.process_type}"
        )
        print(
            f"   Machine     : "
            f"{operation.recommended_machine}"
        )
        print(
            f"   Workholding : "
            f"{operation.workholding}"
        )
        print(
            f"   Datum       : "
            f"{operation.locating_datum}"
        )
        print(
            f"   Input       : "
            f"{operation.input_condition}"
        )

        print("   Steps:")

        for step in operation.operation_steps:
            print(f"      - {step}")

        if operation.in_process_checks:
            print("   Checks:")

            for check in operation.in_process_checks:
                print(f"      - {check}")

        print(
            f"   Reason: "
            f"{operation.reason_for_selection}"
        )

        if operation.reference_case_ids:
            print(
                "   References: "
                + ", ".join(
                    operation.reference_case_ids
                )
            )

        print(
            "   Engineer review: "
            + (
                "REQUIRED"
                if operation.engineer_review_required
                else "Normal review"
            )
        )

    print("\nINSPECTION PLAN")

    for item in plan.inspection_plan:
        print(f"  - {item}")

    print("\nTOOLING AND FIXTURES")

    for item in plan.tooling_and_fixture_requirements:
        print(f"  - {item}")

    print("\nASSUMPTIONS")

    if plan.assumptions:
        for item in plan.assumptions:
            print(f"  - {item}")
    else:
        print("  None stated.")

    print("\nMISSING INFORMATION")

    if plan.missing_information:
        for item in plan.missing_information:
            print(f"  - {item}")
    else:
        print("  None stated.")

    print("\nMANUFACTURING RISKS")

    if plan.manufacturing_risks:
        for item in plan.manufacturing_risks:
            print(f"  - {item}")
    else:
        print("  None stated.")

    print()
    print(
        "CNC code status: "
        f"{plan.cnc_code_status}"
    )
    print(
        "Human approval required: "
        f"{plan.human_approval_required}"
    )


def _prompt_input_mode() -> str:
    print()
    print("Select input type:")
    print("  1) Text description")
    print("  2) Engineering drawing PDF")
    print()

    choice = input("Enter 1 or 2 (or type exit): ").strip()
    return choice


def _get_text_description() -> str:
    return input(
        "Describe the new part or process "
        "(or type exit): "
    ).strip()


def _can_auto_proceed(planning_input: DrawingPlanningInput) -> bool:
    """
    Return True when auto-proceed conditions are met:
    - No critical conflicts
    - Part name identified
    - Material identified  
    - At least one confirmed requirement OR high-confidence uncertain requirements
    """
    # Basic identification required
    has_part_name = planning_input.part_name is not None
    has_material = planning_input.material is not None
    no_critical_conflicts = not planning_input.critical_conflicts
    
    # Check for confirmed requirements
    has_confirmed = len(planning_input.requirements) > 0
    
    # Check for high-confidence uncertain requirements (fallback)
    high_confidence_uncertain = False
    if not has_confirmed and planning_input.uncertain_requirements:
        high_conf_count = sum(
            1 for req in planning_input.uncertain_requirements
            if req.confidence is not None and req.confidence >= 0.7
        )
        # Allow if we have at least 2 high-confidence uncertain requirements
        high_confidence_uncertain = high_conf_count >= 2
    
    return (
        no_critical_conflicts
        and has_part_name
        and has_material  
        and (has_confirmed or high_confidence_uncertain)
    )


def _get_pdf_planning_description() -> str | None:
    """
    Run the full Phase 1 + Phase 2 + adapter pipeline for a PDF drawing.

    Returns:
        A formatted description string for process planning, or None if
        the user cancelled, a failure occurred, or the PDF path was invalid.

    GeminiVisionProvider is initialised lazily — only after the user
    selects a PDF path AND approves the upload consent prompt.
    """
    default_pdf = (
        Path("mech_drw")
        / "VI-RAH-25-73-09-03-05-R2-1.pdf"
    )

    print()
    print("Enter the PDF path.")
    if default_pdf.exists():
        print(f"Press Enter to use: {default_pdf}")

    pdf_path_text = input("PDF path: ").strip().strip('"')
    if not pdf_path_text and default_pdf.exists():
        pdf_path_text = str(default_pdf)

    if not pdf_path_text:
        print("No PDF path provided.")
        return None

    # --- Phase 1: Local extraction (no API calls) ---
    pipeline = EngineeringPDFPipeline(
        output_root=Path("outputs") / "pdf_pipeline",
        preview_dpi=144,
        crop_dpi=350,
        maximum_file_size_mb=100,
        maximum_pages=100,
    )

    try:
        extraction_result = pipeline.process(
            pdf_path_text,
            use_default_engineering_regions=True,
        )
    except PDFPipelineError as error:
        print()
        print("PDF LOADING FAILED")
        print(error)
        return None

    print()
    print("PDF LOADED")
    print(f"File    : {extraction_result.file_name}")
    print(f"Pages   : {extraction_result.page_count}")
    print(f"SHA-256 : {extraction_result.sha256}")
    print(f"Output  : {extraction_result.output_directory}")

    if extraction_result.likely_scanned_document:
        print()
        print(
            "NOTE: This PDF appears to be a scanned drawing "
            "(minimal embedded text). Vision analysis is the "
            "primary extraction method."
        )

    print()
    print("Detected crop images:")
    crop_count = 0
    for page in extraction_result.pages:
        for crop in page.crops:
            print(
                f"  - Page {page.page_number} / "
                f"{crop.name} → {crop.image_path}"
            )
            crop_count += 1

    if crop_count == 0:
        print("  (No crop images were produced.)")

    # --- Upload consent ---
    print()
    print("=" * 60)
    print("UPLOAD CONSENT REQUIRED")
    print("=" * 60)
    print("This operation will upload selected crop images from the")
    print("engineering drawing to the configured Gemini API.")
    print("The original PDF will not be uploaded.")
    print("The crop images may contain confidential engineering")
    print("information.")
    print()
    consent = input("Continue? [y/N]: ").strip().lower()
    if consent not in {"y", "yes"}:
        print("Upload cancelled. No data was sent to Gemini.")
        return None

    # --- Lazy GeminiVisionProvider initialisation (after consent only) ---
    try:
        vision_provider = GeminiVisionProvider()
    except GeminiVisionProviderError as error:
        print()
        print("VISION PROVIDER INITIALISATION FAILED")
        print(error)
        return None
    except Exception as error:
        print()
        print("VISION PROVIDER INITIALISATION FAILED")
        print(error)
        return None

    # --- Phase 2: Gemini Vision crop analysis ---
    analyzer = GeminiCropAnalyzer(
        provider=vision_provider,
        fail_fast=True,   # any crop failure aborts (requirement 3=a)
        use_cache=True,
    )

    manifest_path = extraction_result.manifest_path

    try:
        analysis = analyzer.analyze_manifest(manifest_path)
    except GeminiCropAnalyzerError as error:
        print()
        print("DRAWING VISION ANALYSIS FAILED")
        print(error)
        return None
    except Exception as error:
        print()
        print("DRAWING VISION ANALYSIS FAILED")
        print(error)
        return None

    print()
    print(
        f"Vision analysis complete. "
        f"Crops analyzed: {analysis.analyzed_crop_count}. "
        f"Failed: {analysis.failed_crop_count}."
    )

    # --- Adapter: DrawingVisionAnalysis → DrawingPlanningInput ---
    try:
        adapter = DrawingAnalysisAdapter()
        planning_input = adapter.adapt(analysis)
    except Exception as error:
        print()
        print("DRAWING ANALYSIS ADAPTER FAILED")
        print(error)
        return None

    # --- Format hybrid description ---
    description = format_planning_description(
        planning_input,
        title_block_fields=analysis.merged_title_block_fields or None,
        table_entries_raw=analysis.merged_table_entries or None,
    )

    # --- Show summary ---
    print()
    print("=" * 60)
    print("DRAWING ANALYSIS SUMMARY")
    print("=" * 60)
    print(description)

    # --- Determine and present options ---
    if _can_auto_proceed(planning_input):
        print()
        print("Options:")
        print("  y = proceed automatically with extracted data")
        print("  e = review and add corrections before proceeding")
        print("  n = cancel")
        choice = input("Choice [y/e/n]: ").strip().lower()
    else:
        print()
        print("Automatic proceeding is unavailable.")
        if planning_input.critical_conflicts:
            print("Critical drawing conflicts were detected:")
            for conflict in planning_input.critical_conflicts:
                print(f"  - {conflict}")
        
        # Check if we have enough data to potentially proceed with conflicts
        has_basic_info = (
            planning_input.part_name is not None 
            and planning_input.material is not None
            and (len(planning_input.requirements) > 0 or len(planning_input.uncertain_requirements) > 0)
        )
        
        if has_basic_info and planning_input.critical_conflicts:
            print("Engineer review is required before proceeding.")
            print()
            print("Options:")
            print("  e = review and add corrections before proceeding")
            print("  f = proceed anyway (experienced users - conflicts acknowledged)")
            print("  n = cancel")
            choice = input("Choice [e/f/n]: ").strip().lower()
        else:
            print("Engineer review is required before proceeding.")
            print()
            print("Options:")
            print("  e = review and add corrections before proceeding")
            print("  n = cancel")
            choice = input("Choice [e/n]: ").strip().lower()

    # --- Handle choice ---
    if choice in {"n", ""}:
        print("Planning cancelled.")
        return None

    if choice == "f":
        print()
        print("PROCEEDING WITH CONFLICTS ACKNOWLEDGED")
        print("All identified conflicts will be noted in the process plan.")
        print()
        # Add conflict acknowledgment to description
        if planning_input.critical_conflicts:
            conflict_note = (
                "\n\n=== ENGINEER ACKNOWLEDGMENT ===\n"
                "The following critical conflicts were identified and acknowledged:\n"
            )
            for conflict in planning_input.critical_conflicts:
                conflict_note += f"  - {conflict}\n"
            conflict_note += "\nThese conflicts require resolution before manufacturing."
            description = description + conflict_note

    elif choice == "e":
        print()
        print("Enter corrections or additional information.")
        print("The extracted summary will be preserved.")
        print("Type END on a new line when finished.")
        correction_lines: list[str] = []
        while True:
            line = input()
            if line.strip().upper() == "END":
                break
            correction_lines.append(line)
        corrections = "\n".join(correction_lines).strip()
        if corrections:
            description = (
                description
                + "\n\n=== USER CORRECTIONS / ADDITIONAL INFORMATION ===\n"
                + corrections
            )

    # y, f, or proceeding after edit: return description
    return description


def main() -> None:
    try:
        knowledge_base = (
            ManufacturingKnowledgeBase(
                "database"
            )
        )

        knowledge_base.load()

        # GeminiProvider for text-based planning (always initialised).
        # GeminiVisionProvider is NOT initialised here — it is lazy.
        provider = GeminiProvider()

        agent = ProcessPlannerAgent(
            provider=provider,
            knowledge_base=knowledge_base,
            output_directory="outputs",
        )

    except Exception as error:
        print("STARTUP FAILED")
        print(error)
        return

    display_database(knowledge_base)

    print()
    print(f"Gemini model: {provider.model}")

    while True:
        print()
        choice = _prompt_input_mode()

        if choice.lower() in {"exit", "quit", "q"}:
            print("Program closed.")
            break

        if choice == "1":
            description = _get_text_description()

            if description.lower() in {"exit", "quit", "q"}:
                print("Program closed.")
                break

            if not description:
                print("Please enter a part description.")
                continue

        elif choice == "2":
            result = _get_pdf_planning_description()
            if result is None:
                # User cancelled or pipeline failed — return to menu.
                continue
            description = result

        else:
            print("Please enter 1 or 2.")
            continue

        print()
        print(
            "Searching database and generating "
            "the process plan..."
        )

        try:
            (
                plan,
                references,
                validation_issues,
                output_path,
            ) = agent.generate_plan(
                part_description=description,
                top_k=3,
            )

            print("\nRETRIEVED CASES")

            if references:
                for score, case in references:
                    print(
                        f"  - {case.case_id} "
                        f"({score:.2f})"
                    )
            else:
                print(
                    "  No matching reference case."
                )

            display_plan(plan)

            print("\nVALIDATION")

            if validation_issues:
                for issue in validation_issues:
                    print(f"  - {issue}")
            else:
                print(
                    "  No deterministic validation "
                    "issues detected."
                )

            print()
            print(
                f"Result saved to: {output_path}"
            )

        except Exception as error:
            print()
            print("PROCESS PLANNING FAILED")
            print(error)


if __name__ == "__main__":
    main()
