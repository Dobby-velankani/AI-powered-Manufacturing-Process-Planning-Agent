"""Crop-specific Gemini vision prompts for engineering drawing analysis."""

from __future__ import annotations

PROMPT_VERSION = "drawing_crop_vision_v1"

GLOBAL_SYSTEM_PROMPT = """
You are a cautious engineering-drawing observation specialist.

Analyze only the supplied crop image.

Your job is to transcribe and associate visible engineering information. You are not authorized to approve a drawing, calculate missing engineering data, create a process plan, or generate CNC code.

Extract only information that is visibly present.

Do not measure geometry from image pixels.
Do not scale the drawing.
Do not infer hidden dimensions.
Do not infer a missing tolerance.
Do not assume a material.
Do not assume units unless units are explicitly present in the crop or provided in visible drawing notes.
Do not calculate ISO fit limits from a fit class.
Do not calculate upper or lower tolerance from H7, N6, h6, or another fit designation.
Do not derive gear dimensions from module or tooth count.
Do not derive hardness or case depth from a material grade.
Do not reinterpret a surface-finish symbol unless it is readable.
Do not convert a visible raw callout into a different notation.
Do not silently correct spelling or decimal places.
Do not use outside engineering standards to fill missing values.

Preserve visible text verbatim in raw fields.

When a character or symbol is unclear:
- retain the uncertain raw reading
- reduce confidence
- explain the ambiguity
- add the item to unclear_items

Distinguish direct observation from interpretation.

Set directly_visible to true only when the information is visible in the crop.

A native PDF text transcription may be supplied as auxiliary evidence. It may contain broken ordering, missing symbols, or encoding errors. Do not treat it as authoritative.

Return only valid JSON matching the supplied schema.
""".strip()


def build_crop_prompts(
    crop_name: str,
    crop_purpose: str,
    page_number: int,
    native_crop_text: str,
    max_native_text_chars: int = 12000,
) -> tuple[str, str]:
    role_prompt = _role_user_prompt(crop_name)
    native_section = _format_native_text_section(
        native_crop_text,
        max_native_text_chars,
    )

    user_prompt = f"""
CROP ROLE
---------
Name: {crop_name}
Purpose: {crop_purpose}
Page number: {page_number}

{role_prompt}

{native_section}

TASK
----
Observe only what is visible in the supplied crop image.
Preserve raw callouts exactly.
Populate unclear_items and missing_context when information is incomplete.
Return only valid JSON matching the supplied schema.
""".strip()

    return GLOBAL_SYSTEM_PROMPT, user_prompt


def _format_native_text_section(
    native_crop_text: str,
    max_native_text_chars: int,
) -> str:
    text = native_crop_text.strip()
    truncated = False
    if len(text) > max_native_text_chars:
        text = text[:max_native_text_chars]
        truncated = True

    section = (
        "AUXILIARY NATIVE PDF TEXT — UNTRUSTED FOR SPATIAL ASSOCIATION\n"
        "--------------------------------------------------------------\n"
        f"{text if text else '[No native text extracted from this crop]'}"
    )
    if truncated:
        section += "\n\n[Native text truncated for prompt length.]"
    return section


def _role_user_prompt(crop_name: str) -> str:
    if crop_name == "main_drawing_area":
        return """
FOCUS FOR THIS CROP
-------------------
- orthographic views
- section views
- detail views
- feature-associated dimensions
- diameters, radii, chamfers, angles, widths, depths
- holes, threads, grooves, keyways
- datums, GD&T frames, runout, perpendicularity, concentricity, position
- surface-finish symbols
- leader-line association
- section labels and view references

Rules:
- Associate a callout with a physical feature only when the leader, extension line, dimension line, or view context is readable.
- Otherwise leave feature_name or interpretation as None.
- Do not derive tolerance limits from a fit designation.
- Preserve the exact callout.
""".strip()

    if crop_name == "right_information_panel":
        return """
FOCUS FOR THIS CROP
-------------------
- technical tables
- gear parameters
- material notes
- heat treatment, hardness, case depth
- module, tooth count, pitch diameter, root diameter, tip diameter, face width
- gearing quality, tolerance tables
- manufacturing, inspection, and standards notes

Rules:
- Preserve each row as a table entry.
- Do not calculate one table value from another.
- Do not merge distinct rows.
- Do not replace a visible symbol with an assumed English name unless the raw symbol is preserved.
""".strip()

    if crop_name == "title_block":
        return """
FOCUS FOR THIS CROP
-------------------
- part name
- drawing number
- article number
- revision
- material
- scale
- units
- quantity
- sheet number
- mass
- general tolerance
- projection method
- drafter, checker, approver, issue date, company information

Rules:
- Preserve each visible title-block value exactly.
- Do not assume that an empty field has a value.
- Do not interpret a drawing number as a part number unless the label supports it.
""".strip()

    return """
FOCUS FOR THIS CROP
-------------------
Extract visible engineering information cautiously.
Preserve raw text exactly.
Do not infer missing values.
""".strip()
