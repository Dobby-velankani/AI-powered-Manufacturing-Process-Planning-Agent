"""
Deterministic merger for Phase 2 Gemini crop vision results.

This module does not call Gemini.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict

from app.models.drawing_extraction import PDFExtractionResult
from app.models.drawing_vision import (
    DrawingVisionAnalysis,
    GeminiCropExtraction,
    MergedCalloutObservation,
    NativeEvidenceMatch,
    ObservedCallout,
    ObservedDrawingNote,
    ObservedTableEntry,
    ObservedTitleBlockField,
    VisionCropResult,
)


class DrawingResultMerger:
    """Merge crop vision results and compare against native PDF text."""

    def merge(
        self,
        manifest: PDFExtractionResult,
        crop_results: list[VisionCropResult],
        manifest_path,
        failed_crop_count: int = 0,
        processing_warnings: list[str] | None = None,
    ) -> DrawingVisionAnalysis:
        page_native_text = {
            page.page_number: page.native_text
            for page in manifest.pages
        }

        merged_callouts = self._merge_callouts(
            crop_results,
            page_native_text,
        )
        merged_title_block_fields = self._merge_title_block_fields(
            crop_results
        )
        merged_table_entries = self._merge_table_entries(crop_results)
        merged_notes = self._merge_notes(crop_results)

        unclear_items: list[str] = []
        for crop in crop_results:
            unclear_items.extend(crop.extraction.unclear_items)
        unclear_items = self._dedupe_strings(unclear_items)

        model_names = sorted(
            {
                crop.provider_metadata.model_name
                for crop in crop_results
                if crop.provider_metadata.model_name
            }
        )

        return DrawingVisionAnalysis(
            document_sha256=manifest.sha256,
            source_file_name=manifest.file_name,
            manifest_path=manifest_path,
            model_names_used=model_names,
            analyzed_crop_count=len(crop_results),
            failed_crop_count=failed_crop_count,
            crop_results=crop_results,
            merged_callouts=merged_callouts,
            merged_title_block_fields=merged_title_block_fields,
            merged_table_entries=merged_table_entries,
            merged_notes=merged_notes,
            unclear_items=unclear_items,
            processing_warnings=processing_warnings or [],
        )

    def _merge_callouts(
        self,
        crop_results: list[VisionCropResult],
        page_native_text: dict[int, str],
    ) -> list[MergedCalloutObservation]:
        grouped: dict[str, list[tuple[VisionCropResult, ObservedCallout]]] = (
            defaultdict(list)
        )

        for crop in crop_results:
            for callout in crop.extraction.callouts:
                canonical_id = self._canonical_callout_id(
                    callout,
                    crop.page_number,
                )
                grouped[canonical_id].append((crop, callout))

        merged: list[MergedCalloutObservation] = []
        for canonical_id, items in grouped.items():
            primary_crop, primary_callout = self._choose_primary_callout(items)
            duplicates = [
                callout
                for crop, callout in items
                if callout is not primary_callout
            ]
            source_crops = sorted({crop.crop_name for crop, _ in items})

            native_evidence = self._match_native_evidence(
                callout=primary_callout,
                crop=primary_crop,
                page_native_text=page_native_text.get(
                    primary_crop.page_number,
                    "",
                ),
            )
            status, warnings = self._determine_status(
                primary_callout=primary_callout,
                duplicates=duplicates,
                native_evidence=native_evidence,
            )

            merged.append(
                MergedCalloutObservation(
                    canonical_id=canonical_id,
                    primary_observation=primary_callout,
                    duplicate_observations=duplicates,
                    source_crops=source_crops,
                    native_evidence=native_evidence,
                    status=status,
                    warnings=warnings,
                )
            )

        merged.sort(key=lambda item: item.canonical_id)
        merged = self._flag_cross_crop_conflicts(merged)
        return merged

    def _merge_title_block_fields(
        self,
        crop_results: list[VisionCropResult],
    ) -> list[ObservedTitleBlockField]:
        grouped: dict[str, list[ObservedTitleBlockField]] = defaultdict(list)
        for crop in crop_results:
            for field in crop.extraction.title_block_fields:
                key = self.normalize_text(field.field_name)
                grouped[key].append(field)

        merged: list[ObservedTitleBlockField] = []
        for fields in grouped.values():
            primary = max(
                fields,
                key=lambda field: (
                    len(field.raw_value),
                    field.confidence,
                ),
            )
            conflicting = {
                self.normalize_text(field.raw_value)
                for field in fields
                if self.normalize_text(field.raw_value)
                != self.normalize_text(primary.raw_value)
            }
            if len(conflicting) > 1:
                primary.ambiguity_notes = list(
                    dict.fromkeys(
                        [
                            *primary.ambiguity_notes,
                            "Conflicting title-block values were observed "
                            "across crops.",
                        ]
                    )
                )
            merged.append(primary)
        return merged

    def _merge_table_entries(
        self,
        crop_results: list[VisionCropResult],
    ) -> list[ObservedTableEntry]:
        seen: set[str] = set()
        merged: list[ObservedTableEntry] = []
        for crop in crop_results:
            for entry in crop.extraction.table_entries:
                key = "|".join(
                    [
                        str(crop.page_number),
                        self.normalize_text(entry.parameter_name),
                        self.normalize_text(entry.raw_value),
                    ]
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(entry)
        return merged

    def _merge_notes(
        self,
        crop_results: list[VisionCropResult],
    ) -> list[ObservedDrawingNote]:
        seen: set[str] = set()
        merged: list[ObservedDrawingNote] = []
        for crop in crop_results:
            for note in crop.extraction.notes:
                key = self.normalize_text(note.raw_text)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(note)
        return merged

    def _choose_primary_callout(
        self,
        items: list[tuple[VisionCropResult, ObservedCallout]],
    ) -> tuple[VisionCropResult, ObservedCallout]:
        def score(item: tuple[VisionCropResult, ObservedCallout]) -> tuple:
            crop, callout = item
            native_support = self.normalize_text(callout.raw_callout) in (
                self.normalize_text(crop.native_crop_text)
            )
            return (
                callout.confidence,
                1 if native_support else 0,
                len(callout.raw_callout),
                -len(callout.ambiguity_notes),
            )

        return max(items, key=score)

    def _canonical_callout_id(
        self,
        callout: ObservedCallout,
        page_number: int,
    ) -> str:
        parts = [
            str(page_number),
            self.normalize_text(callout.category),
            self.normalize_text(callout.raw_callout),
            self.normalize_text(callout.feature_name or ""),
        ]
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        return digest[:16]

    def _determine_status(
        self,
        primary_callout: ObservedCallout,
        duplicates: list[ObservedCallout],
        native_evidence: NativeEvidenceMatch,
    ) -> tuple[str, list[str]]:
        warnings: list[str] = []
        status_map = {
            "exact_match": "corroborated",
            "normalized_match": "normalized_support",
            "partial_match": "partial_support",
            "not_found": "vision_only",
            "potential_conflict": "potential_conflict",
            "not_applicable": "vision_only",
        }
        status = status_map.get(native_evidence.match_type, "uncertain")

        if primary_callout.ambiguity_notes:
            status = "uncertain"
            warnings.extend(primary_callout.ambiguity_notes)

        for duplicate in duplicates:
            if self._critical_tokens(duplicate.raw_callout) != self._critical_tokens(
                primary_callout.raw_callout
            ):
                status = "potential_conflict"
                warnings.append(
                    "Overlapping crops reported different critical tokens "
                    f"for callout '{primary_callout.raw_callout}'."
                )

        if native_evidence.match_type == "potential_conflict":
            status = "potential_conflict"
            if native_evidence.conflict_reason:
                warnings.append(native_evidence.conflict_reason)

        return status, warnings

    def _match_native_evidence(
        self,
        callout: ObservedCallout,
        crop: VisionCropResult,
        page_native_text: str,
    ) -> NativeEvidenceMatch:
        if not callout.directly_visible and callout.category in {
            "datum",
            "gdt",
            "note",
        }:
            return NativeEvidenceMatch(
                vision_raw_callout=callout.raw_callout,
                match_type="not_applicable",
                match_score=0.0,
                normalized_vision_text=self.normalize_text(callout.raw_callout),
                normalized_native_text=None,
                critical_tokens_vision=self._critical_tokens(callout.raw_callout),
                critical_tokens_native=[],
                page_number=crop.page_number,
                crop_name=crop.crop_name,
            )

        candidates = [
            ("crop", crop.native_crop_text),
            ("page", page_native_text),
        ]

        best: NativeEvidenceMatch | None = None
        for source_name, native_text in candidates:
            match = self._compare_texts(
                vision_text=callout.raw_callout,
                native_text=native_text,
                page_number=crop.page_number,
                crop_name=crop.crop_name,
            )
            if best is None or match.match_score > best.match_score:
                best = match
            if match.match_type in {"exact_match", "normalized_match"}:
                break

        return best or NativeEvidenceMatch(
            vision_raw_callout=callout.raw_callout,
            match_type="not_found",
            match_score=0.0,
            normalized_vision_text=self.normalize_text(callout.raw_callout),
            normalized_native_text=None,
            critical_tokens_vision=self._critical_tokens(callout.raw_callout),
            critical_tokens_native=[],
            page_number=crop.page_number,
            crop_name=crop.crop_name,
        )

    def _compare_texts(
        self,
        vision_text: str,
        native_text: str,
        page_number: int,
        crop_name: str,
    ) -> NativeEvidenceMatch:
        normalized_vision = self.normalize_text(vision_text)
        normalized_native = self.normalize_text(native_text)
        vision_tokens = self._critical_tokens(vision_text)
        native_tokens = self._critical_tokens(native_text)

        if not normalized_vision:
            return NativeEvidenceMatch(
                vision_raw_callout=vision_text,
                match_type="not_found",
                match_score=0.0,
                normalized_vision_text=normalized_vision,
                normalized_native_text=normalized_native or None,
                critical_tokens_vision=vision_tokens,
                critical_tokens_native=native_tokens,
                page_number=page_number,
                crop_name=crop_name,
            )

        if vision_text in native_text:
            return NativeEvidenceMatch(
                vision_raw_callout=vision_text,
                native_text_excerpt=vision_text,
                match_type="exact_match",
                match_score=1.0,
                normalized_vision_text=normalized_vision,
                normalized_native_text=normalized_native,
                critical_tokens_vision=vision_tokens,
                critical_tokens_native=native_tokens,
                page_number=page_number,
                crop_name=crop_name,
            )

        if normalized_vision and normalized_vision in normalized_native:
            return NativeEvidenceMatch(
                vision_raw_callout=vision_text,
                native_text_excerpt=normalized_vision,
                match_type="normalized_match",
                match_score=0.9,
                normalized_vision_text=normalized_vision,
                normalized_native_text=normalized_native,
                critical_tokens_vision=vision_tokens,
                critical_tokens_native=native_tokens,
                page_number=page_number,
                crop_name=crop_name,
            )

        overlap = set(vision_tokens) & set(native_tokens)
        if overlap and len(overlap) >= max(1, len(vision_tokens) // 2):
            conflict = self._detect_potential_conflict(
                vision_tokens,
                native_tokens,
            )
            if conflict:
                return NativeEvidenceMatch(
                    vision_raw_callout=vision_text,
                    native_text_excerpt=self._find_excerpt(native_text, overlap),
                    match_type="potential_conflict",
                    match_score=0.4,
                    normalized_vision_text=normalized_vision,
                    normalized_native_text=normalized_native,
                    critical_tokens_vision=vision_tokens,
                    critical_tokens_native=native_tokens,
                    conflict_reason=conflict,
                    page_number=page_number,
                    crop_name=crop_name,
                )
            return NativeEvidenceMatch(
                vision_raw_callout=vision_text,
                native_text_excerpt=self._find_excerpt(native_text, overlap),
                match_type="partial_match",
                match_score=0.6,
                normalized_vision_text=normalized_vision,
                normalized_native_text=normalized_native,
                critical_tokens_vision=vision_tokens,
                critical_tokens_native=native_tokens,
                page_number=page_number,
                crop_name=crop_name,
            )

        conflict = self._detect_potential_conflict(vision_tokens, native_tokens)
        if not conflict:
            conflict = self._detect_decimal_token_conflict(vision_text, native_text)
        if conflict:
            return NativeEvidenceMatch(
                vision_raw_callout=vision_text,
                native_text_excerpt=self._find_excerpt(native_text, overlap),
                match_type="potential_conflict",
                match_score=0.3,
                normalized_vision_text=normalized_vision,
                normalized_native_text=normalized_native,
                critical_tokens_vision=vision_tokens,
                critical_tokens_native=native_tokens,
                conflict_reason=conflict,
                page_number=page_number,
                crop_name=crop_name,
            )

        return NativeEvidenceMatch(
            vision_raw_callout=vision_text,
            match_type="not_found",
            match_score=0.0,
            normalized_vision_text=normalized_vision,
            normalized_native_text=normalized_native or None,
            critical_tokens_vision=vision_tokens,
            critical_tokens_native=native_tokens,
            page_number=page_number,
            crop_name=crop_name,
        )

    @staticmethod
    def normalize_text(value: str) -> str:
        text = unicodedata.normalize("NFKC", value or "")
        text = text.replace("\u2212", "-").replace("\u00b1", "±")
        text = text.replace("Ø", "ø").replace("⌀", "ø")
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    @classmethod
    def _critical_tokens(cls, value: str) -> list[str]:
        normalized = cls.normalize_text(value)
        tokens: list[str] = []

        patterns = [
            r"\b\d+(?:[.,]\d+)?\b",
            r"[±+-]\d+(?:[.,]\d+)?",
            r"\b(?:ø|Ø|⌀)\s*\d+(?:[.,]\d+)?\b",
            r"\b(?:ra|rz)\s*\d+(?:[.,]\d+)?\b",
            r"\b(?:h|n|js|g|f|p)\d+\b",
            r"\bm\d+(?:x\d+(?:[.,]\d+)?)?\b",
            r"\bhv\d+\b",
            r"\bhrc\d+\b",
            r"\b[a-z]\b",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
                token = match.group(0).replace(" ", "")
                if token and token not in tokens:
                    tokens.append(token)

        return tokens

    @staticmethod
    def _detect_potential_conflict(
        vision_tokens: list[str],
        native_tokens: list[str],
    ) -> str | None:
        vision_numbers = {
            token for token in vision_tokens if re.search(r"\d", token)
        }
        native_numbers = {
            token for token in native_tokens if re.search(r"\d", token)
        }
        overlap = vision_numbers & native_numbers
        if overlap and vision_numbers != native_numbers:
            return (
                "Comparable numeric or symbolic tokens differ between "
                f"vision and native text: vision={sorted(vision_numbers)} "
                f"native={sorted(native_numbers)}"
            )
        return None

    @staticmethod
    def _detect_decimal_token_conflict(
        vision_text: str,
        native_text: str,
    ) -> str | None:
        vision_numbers = re.findall(r"\d+(?:[.,]\d+)?", vision_text)
        native_numbers = re.findall(r"\d+(?:[.,]\d+)?", native_text)
        if not vision_numbers or not native_numbers:
            return None

        if set(vision_numbers) & set(native_numbers):
            return None

        if len(vision_numbers) == 1 and len(native_numbers) == 1:
            vision_number = vision_numbers[0]
            native_number = native_numbers[0]
            if vision_number != native_number:
                vision_root = vision_number.split(".")[0]
                native_root = native_number.split(".")[0]
                if vision_root == native_root:
                    return (
                        "Comparable decimal tokens differ between vision and "
                        f"native text: vision={vision_number} "
                        f"native={native_number}"
                    )

        for vision_number in vision_numbers:
            for native_number in native_numbers:
                if vision_number == native_number:
                    continue
                if (
                    vision_number in native_number
                    or native_number in vision_number
                ):
                    return (
                        "Comparable decimal tokens differ between vision and "
                        f"native text: vision={vision_number} "
                        f"native={native_number}"
                    )
        return None

    def _flag_cross_crop_conflicts(
        self,
        merged: list[MergedCalloutObservation],
    ) -> list[MergedCalloutObservation]:
        for index, left in enumerate(merged):
            for right in merged[index + 1:]:
                if (
                    left.native_evidence.page_number
                    != right.native_evidence.page_number
                ):
                    continue
                if (
                    left.primary_observation.category
                    != right.primary_observation.category
                ):
                    continue
                left_tokens = set(
                    self._critical_tokens(left.primary_observation.raw_callout)
                )
                right_tokens = set(
                    self._critical_tokens(right.primary_observation.raw_callout)
                )
                if not left_tokens or not right_tokens or left_tokens == right_tokens:
                    continue
                shared = left_tokens & right_tokens
                if not shared:
                    continue
                message = (
                    "Cross-crop observations disagree on critical tokens: "
                    f"{sorted(left_tokens)} vs {sorted(right_tokens)}"
                )
                for item in (left, right):
                    if item.status == "corroborated":
                        item.status = "potential_conflict"
                    item.warnings.append(message)
        return merged

    @staticmethod
    def _find_excerpt(native_text: str, overlap: set[str]) -> str | None:
        for token in overlap:
            index = native_text.lower().find(token.lower())
            if index >= 0:
                start = max(0, index - 20)
                end = min(len(native_text), index + len(token) + 20)
                return native_text[start:end].strip()
        return None

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        return result
