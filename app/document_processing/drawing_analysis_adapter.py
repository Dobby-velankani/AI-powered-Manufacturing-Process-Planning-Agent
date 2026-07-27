"""
Adapter: maps DrawingVisionAnalysis → DrawingPlanningInput.

No LLM calls. No I/O. Pure deterministic mapping.

Status routing (no silent discard):
  corroborated, normalized_support  → requirements (confirmed)
  partial_support, vision_only, uncertain → uncertain_requirements
  potential_conflict → uncertain_requirements + critical_conflicts
"""

from __future__ import annotations

from app.models.drawing_planning_input import (
    DrawingPlanningInput,
    PlanningRequirement,
)
from app.models.drawing_vision import (
    DrawingVisionAnalysis,
    MergedCalloutObservation,
    ObservedTitleBlockField,
)

# Evidence statuses that are considered confirmed.
_CONFIRMED_STATUSES = {"corroborated", "normalized_support"}

# High-confidence vision-only observations can also be confirmed
_HIGH_CONFIDENCE_THRESHOLD = 0.75

# Evidence statuses routed to uncertain_requirements only.
_UNCERTAIN_STATUSES = {"partial_support", "vision_only", "uncertain"}

# Category keywords that, when involved in a potential_conflict,
# block automatic proceeding and populate critical_conflicts.
_CRITICAL_CONFLICT_CATEGORIES = {
    "material",
    "dimension",
    "diameter",
    "radius",
    "tolerance",
    "fit",
    "thread",
    "gdt",
    "datum",
    "surface_finish",
    "heat_treatment",
    "hardness",
    "case_depth",
    "gear_parameter",
}

# Category keywords for convenience list routing.
_DIMENSION_KEYWORDS = {
    "dimension",
    "diameter",
    "radius",
    "length",
    "width",
    "depth",
    "chamfer",
    "angle",
}
_SURFACE_FINISH_KEYWORDS = {"surface_finish", "ra", "rz"}
_GDT_KEYWORDS = {
    "gdt",
    "runout",
    "perpendicularity",
    "concentricity",
    "position",
    "datum",
}
_HEAT_TREAT_KEYWORDS = {"heat", "hardness", "hrc", "hv", "case_depth"}


def _matches_any(value: str, keywords: set[str]) -> bool:
    """Return True if any keyword appears as a substring in value.lower()."""
    lower = value.lower()
    return any(kw in lower for kw in keywords)


def _is_critical_conflict_category(category: str) -> bool:
    return _matches_any(category, _CRITICAL_CONFLICT_CATEGORIES)


class DrawingAnalysisAdapter:
    """
    Maps a DrawingVisionAnalysis to a DrawingPlanningInput.

    All observation statuses are preserved — nothing is silently discarded.
    """

    def adapt(self, analysis: DrawingVisionAnalysis) -> DrawingPlanningInput:
        """
        Convert a DrawingVisionAnalysis into a DrawingPlanningInput.

        Args:
            analysis: The merged vision analysis from Phase 2.

        Returns:
            A DrawingPlanningInput with full traceability.
        """
        # --- Title block ---
        drawing_number = self._extract_title_field(
            analysis.merged_title_block_fields,
            {"drawing number", "drawing_number", "dwg", "drawing no", "drg"},
        )
        part_name = self._extract_title_field(
            analysis.merged_title_block_fields,
            {"part name", "part_name", "component", "description", "title", "name"},
        )
        material = self._extract_title_field(
            analysis.merged_title_block_fields,
            {"material", "mat", "material grade", "grade"},
        )
        revision = self._extract_title_field(
            analysis.merged_title_block_fields,
            {"revision", "rev", "revision level"},
        )
        scale = self._extract_title_field(
            analysis.merged_title_block_fields,
            {"scale"},
        )
        general_tolerance = self._extract_title_field(
            analysis.merged_title_block_fields,
            {
                "general tolerance",
                "general_tolerance",
                "gen tol",
                "tolerance",
            },
        )

        # --- Observation routing ---
        requirements: list[PlanningRequirement] = []
        uncertain_requirements: list[PlanningRequirement] = []
        critical_conflicts: list[str] = []

        for merged in analysis.merged_callouts:
            req = self._callout_to_requirement(merged)
            status = merged.status

            # Check if this is a high-confidence vision_only observation
            is_high_conf_vision = (
                status == "vision_only" 
                and merged.primary_observation.confidence is not None
                and merged.primary_observation.confidence >= _HIGH_CONFIDENCE_THRESHOLD
            )

            if status in _CONFIRMED_STATUSES or is_high_conf_vision:
                requirements.append(req)
            elif status in _UNCERTAIN_STATUSES:
                uncertain_requirements.append(req)
            elif status == "potential_conflict":
                uncertain_requirements.append(req)
                # Only flag as critical conflict if it's truly blocking for manufacturing
                if _is_critical_conflict_category(merged.primary_observation.category):
                    # Filter out false positives - only flag conflicts that indicate:
                    # 1. Contradictory values for the SAME feature
                    # 2. Material mismatches 
                    # 3. Heat treatment contradictions
                    meaningful_warnings = [
                        w for w in merged.warnings 
                        if w and any(indicator in w.lower() for indicator in [
                            "contradictory", "mismatch", "incompatible", 
                            "material conflict", "heat treatment conflict"
                        ])
                    ]
                    if meaningful_warnings:
                        conflict_reason = f"[{merged.primary_observation.category}] {merged.primary_observation.raw_callout} — {'; '.join(meaningful_warnings)}"
                        critical_conflicts.append(conflict_reason)
            else:
                # Unknown status — treat as uncertain, never discard.
                uncertain_requirements.append(req)

        # --- Convenience list population ---
        dimensions: list[str] = []
        surface_finish: list[str] = []
        geometric_tolerances: list[str] = []
        heat_treatment: list[str] = []

        for req in requirements:
            callout_text = req.raw_callout
            cat = req.category.lower()

            if _matches_any(cat, _DIMENSION_KEYWORDS):
                dimensions.append(callout_text)
            if _matches_any(cat, _SURFACE_FINISH_KEYWORDS):
                surface_finish.append(callout_text)
            if _matches_any(cat, _GDT_KEYWORDS):
                geometric_tolerances.append(callout_text)

        # Table entries for surface finish and heat treatment
        for entry in analysis.merged_table_entries:
            param = entry.parameter_name.lower()
            value_text = (
                f"{entry.parameter_name}: {entry.raw_value}"
                + (f" {entry.unit}" if entry.unit else "")
            )
            if _matches_any(param, _SURFACE_FINISH_KEYWORDS):
                surface_finish.append(value_text)
            if _matches_any(param, _HEAT_TREAT_KEYWORDS):
                heat_treatment.append(value_text)

        # General notes
        general_notes: list[str] = [
            note.raw_text for note in analysis.merged_notes
        ]

        # Unclear items = analysis-level unclear + processing warnings
        unclear_items: list[str] = list(analysis.unclear_items)
        unclear_items.extend(analysis.processing_warnings)

        return DrawingPlanningInput(
            drawing_number=drawing_number,
            part_name=part_name,
            material=material,
            revision=revision,
            scale=scale,
            general_tolerance=general_tolerance,
            source_file_name=analysis.source_file_name,
            document_sha256=analysis.document_sha256,
            requirements=requirements,
            uncertain_requirements=uncertain_requirements,
            critical_conflicts=critical_conflicts,
            dimensions=dimensions,
            surface_finish_requirements=surface_finish,
            geometric_tolerances=geometric_tolerances,
            heat_treatment_requirements=heat_treatment,
            general_notes=general_notes,
            unclear_items=unclear_items,
        )

    @staticmethod
    def _extract_title_field(
        fields: list[ObservedTitleBlockField],
        target_names: set[str],
    ) -> str | None:
        """
        Find the best-confidence title block field matching any target name.

        Matching is case-insensitive substring search.
        Returns raw_value of the highest-confidence match, or None.
        """
        candidates: list[ObservedTitleBlockField] = []
        for field in fields:
            field_lower = field.field_name.lower()
            if any(t in field_lower for t in target_names):
                candidates.append(field)

        if not candidates:
            return None

        best = max(candidates, key=lambda f: f.confidence)
        value = best.raw_value.strip()
        return value if value else None

    @staticmethod
    def _callout_to_requirement(
        merged: MergedCalloutObservation,
    ) -> PlanningRequirement:
        """Convert a MergedCalloutObservation into a PlanningRequirement."""
        obs = merged.primary_observation
        return PlanningRequirement(
            category=obs.category,
            raw_callout=obs.raw_callout,
            interpreted_requirement=obs.interpretation,
            feature_name=obs.feature_name,
            page_number=merged.native_evidence.page_number,
            view_reference=obs.view_reference,
            crop_name=merged.native_evidence.crop_name,
            evidence_status=merged.status,
            confidence=obs.confidence,
            warnings=list(merged.warnings),
        )

    @staticmethod
    def _build_conflict_reason(merged: MergedCalloutObservation) -> str:
        """Build a human-readable conflict string for critical_conflicts."""
        obs = merged.primary_observation
        reason_parts = [w for w in merged.warnings if w and w.strip()]
        
        if reason_parts:
            reason = "; ".join(reason_parts)
            return f"[{obs.category}] conflict: '{obs.raw_callout}' — {reason}"
        else:
            # Return empty string if no meaningful warnings - will be filtered out
            return ""


def format_planning_description(
    planning_input: DrawingPlanningInput,
    title_block_fields: list[ObservedTitleBlockField] | None = None,
    table_entries_raw: list | None = None,
) -> str:
    """
    Convert a DrawingPlanningInput into a hybrid structured text description
    for ProcessPlannerAgent.generate_plan().

    Format:
        - Header (file, sha256)
        - Drawing identification
        - Confirmed requirements
        - Convenience sections (surface finish, GD&T, heat treatment, notes)
        - Uncertain / needs review
        - Critical conflicts (only if present)
        - Raw evidence (traceability only)
    """
    lines: list[str] = []

    # --- Header ---
    lines.append("INPUT TYPE: ENGINEERING DRAWING PDF")
    lines.append(f"FILE: {planning_input.source_file_name}")
    lines.append(f"SHA256: {planning_input.document_sha256}")

    # --- Drawing identification ---
    lines.append("")
    lines.append("=== DRAWING IDENTIFICATION ===")
    lines.append(
        f"Part Name: {planning_input.part_name or 'Not identified'}"
    )
    lines.append(
        f"Drawing Number: {planning_input.drawing_number or 'Not identified'}"
    )
    lines.append(
        f"Material: {planning_input.material or 'Not identified'}"
    )
    lines.append(
        f"Revision: {planning_input.revision or 'Not specified'}"
    )
    lines.append(
        f"Scale: {planning_input.scale or 'Not specified'}"
    )
    lines.append(
        f"General Tolerance: {planning_input.general_tolerance or 'Not specified'}"
    )

    # --- Confirmed requirements ---
    lines.append("")
    lines.append("=== CONFIRMED REQUIREMENTS ===")
    if planning_input.requirements:
        for req in planning_input.requirements:
            feature_part = (
                f" | Feature: {req.feature_name}"
                if req.feature_name
                else ""
            )
            page_part = (
                f" | Page: {req.page_number}" if req.page_number is not None else ""
            )
            conf_part = (
                f" | Confidence: {req.confidence:.2f}"
                if req.confidence is not None
                else ""
            )
            lines.append(
                f"  - [{req.category}] {req.raw_callout}"
                f"{feature_part}{page_part}{conf_part}"
            )
    else:
        lines.append("  (No confirmed requirements extracted)")

    # --- Convenience sections — omit headers when lists are empty ---
    if planning_input.surface_finish_requirements:
        lines.append("")
        lines.append("=== SURFACE FINISH ===")
        for item in planning_input.surface_finish_requirements:
            lines.append(f"  - {item}")

    if planning_input.geometric_tolerances:
        lines.append("")
        lines.append("=== GEOMETRIC TOLERANCES ===")
        for item in planning_input.geometric_tolerances:
            lines.append(f"  - {item}")

    if planning_input.heat_treatment_requirements:
        lines.append("")
        lines.append("=== HEAT TREATMENT ===")
        for item in planning_input.heat_treatment_requirements:
            lines.append(f"  - {item}")

    if planning_input.general_notes:
        lines.append("")
        lines.append("=== GENERAL NOTES ===")
        for item in planning_input.general_notes:
            lines.append(f"  - {item}")

    # --- Uncertain / needs engineer review ---
    lines.append("")
    lines.append("=== UNCERTAIN / NEEDS ENGINEER REVIEW ===")
    if planning_input.uncertain_requirements:
        for req in planning_input.uncertain_requirements:
            warnings_part = (
                f" | Warnings: {'; '.join(req.warnings)}"
                if req.warnings
                else ""
            )
            lines.append(
                f"  - [{req.category}] {req.raw_callout}"
                f" | Status: {req.evidence_status}"
                f"{warnings_part}"
            )
    else:
        lines.append("  (No uncertain observations)")

    # --- Critical conflicts (only when present) ---
    if planning_input.critical_conflicts:
        lines.append("")
        lines.append(
            "=== CRITICAL CONFLICTS — ENGINEER MUST RESOLVE BEFORE APPROVING ==="
        )
        for conflict in planning_input.critical_conflicts:
            lines.append(f"  - {conflict}")

    # --- Raw evidence (traceability only) ---
    has_title_fields = bool(title_block_fields)
    has_table_entries = bool(table_entries_raw)

    if has_title_fields or has_table_entries:
        lines.append("")
        lines.append("=== RAW EVIDENCE (SUPPORTING TRACEABILITY ONLY) ===")

        if has_title_fields:
            lines.append("Title block raw values:")
            for field in title_block_fields:  # type: ignore[union-attr]
                lines.append(
                    f"  - {field.field_name}: {field.raw_value}"
                    f" (confidence: {field.confidence:.2f})"
                )

        if has_table_entries:
            lines.append("Key table entries:")
            for entry in table_entries_raw:  # type: ignore[union-attr]
                unit_part = f" [{entry.unit}]" if entry.unit else ""
                lines.append(
                    f"  - {entry.parameter_name}: {entry.raw_value}{unit_part}"
                )

    return "\n".join(lines)
