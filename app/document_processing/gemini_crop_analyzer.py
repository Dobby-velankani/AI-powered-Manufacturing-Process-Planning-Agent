"""
Phase 2 Gemini crop analyzer for engineering drawing manifests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.document_processing.drawing_result_merger import DrawingResultMerger
from app.document_processing.vision_prompts import (
    PROMPT_VERSION,
    build_crop_prompts,
)
from app.llm.gemini_vision_provider import (
    GeminiVisionProvider,
    GeminiVisionProviderError,
)
from app.models.drawing_extraction import BoundingBox, PDFExtractionResult
from app.models.drawing_vision import (
    DrawingVisionAnalysis,
    GeminiCropExtraction,
    VisionCropResult,
    VisionProviderMetadata,
)


class GeminiCropAnalyzerError(RuntimeError):
    """Raised when crop vision analysis cannot proceed."""


RESPONSE_SCHEMA_VERSION = "phase_2_gemini_crop_vision_v1"


@dataclass(frozen=True)
class SelectedCrop:
    page_number: int
    crop_name: str
    crop_purpose: str
    crop_image_path: Path
    crop_bbox: BoundingBox
    native_crop_text: str
    native_word_count: int


class GeminiCropAnalyzer:
    DEFAULT_CROP_NAMES = (
        "main_drawing_area",
        "right_information_panel",
        "title_block",
    )

    def __init__(
        self,
        provider: GeminiVisionProvider,
        output_subdirectory_name: str = "vision",
        default_crop_names: tuple[str, ...] = DEFAULT_CROP_NAMES,
        max_native_text_chars: int = 12000,
        fail_fast: bool = False,
        use_cache: bool = True,
        force_reanalysis: bool = False,
    ) -> None:
        self.provider = provider
        self.output_subdirectory_name = output_subdirectory_name
        self.default_crop_names = default_crop_names
        self.max_native_text_chars = max_native_text_chars
        self.fail_fast = fail_fast
        self.use_cache = use_cache
        self.force_reanalysis = force_reanalysis
        self.merger = DrawingResultMerger()

    def analyze_manifest(
        self,
        manifest_path: str | Path,
        crop_names: list[str] | None = None,
    ) -> DrawingVisionAnalysis:
        manifest_path = Path(manifest_path).expanduser().resolve()
        if not manifest_path.is_file():
            raise GeminiCropAnalyzerError(
                f"Manifest does not exist: {manifest_path}"
            )

        manifest = PDFExtractionResult.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        document_output_directory = manifest.output_directory.resolve()
        vision_root = document_output_directory / self.output_subdirectory_name
        crop_results_dir = vision_root / "crop_results"
        raw_responses_dir = vision_root / "raw_responses"
        request_metadata_dir = vision_root / "request_metadata"
        cache_dir = vision_root / "cache"

        for folder in (
            crop_results_dir,
            raw_responses_dir,
            request_metadata_dir,
            cache_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

        selected_names = crop_names or list(self.default_crop_names)
        selected_crops = self._select_crops(manifest, selected_names)

        crop_results: list[VisionCropResult] = []
        processing_warnings: list[str] = []
        failed_crop_count = 0

        for selected in selected_crops:
            try:
                crop_result = self._analyze_single_crop(
                    manifest=manifest,
                    selected=selected,
                    manifest_path=manifest_path,
                    cache_dir=cache_dir,
                    crop_results_dir=crop_results_dir,
                    raw_responses_dir=raw_responses_dir,
                    request_metadata_dir=request_metadata_dir,
                )
                crop_results.append(crop_result)
            except Exception as error:
                failed_crop_count += 1
                warning = (
                    f"Crop page {selected.page_number} "
                    f"{selected.crop_name} failed: {error}"
                )
                processing_warnings.append(warning)
                if self.fail_fast:
                    raise GeminiCropAnalyzerError(warning) from error

        analysis = self.merger.merge(
            manifest=manifest,
            crop_results=crop_results,
            manifest_path=manifest_path,
            failed_crop_count=failed_crop_count,
            processing_warnings=processing_warnings,
        )

        vision_analysis_path = (
            document_output_directory / "vision_analysis.json"
        )
        merged_analysis_path = (
            document_output_directory / "merged_drawing_analysis.json"
        )
        vision_analysis_path.write_text(
            analysis.model_dump_json(indent=2),
            encoding="utf-8",
        )
        merged_analysis_path.write_text(
            analysis.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return analysis

    def _analyze_single_crop(
        self,
        manifest: PDFExtractionResult,
        selected: SelectedCrop,
        manifest_path: Path,
        cache_dir: Path,
        crop_results_dir: Path,
        raw_responses_dir: Path,
        request_metadata_dir: Path,
    ) -> VisionCropResult:
        image_path = selected.crop_image_path.resolve()
        if not image_path.is_file():
            raise GeminiCropAnalyzerError(
                f"Crop image does not exist: {image_path}"
            )

        image_bytes = image_path.read_bytes()
        image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        cache_key = self._cache_key(
            image_sha256=image_sha256,
            crop_name=selected.crop_name,
            model_name=self.provider.model,
        )
        cache_path = cache_dir / f"{cache_key}.json"
        raw_path = raw_responses_dir / self._artifact_name(
            selected, suffix=".txt"
        )
        crop_result_path = crop_results_dir / self._artifact_name(
            selected, suffix=".json"
        )
        metadata_path = request_metadata_dir / self._artifact_name(
            selected, suffix=".json"
        )

        system_prompt, user_prompt = build_crop_prompts(
            crop_name=selected.crop_name,
            crop_purpose=selected.crop_purpose,
            page_number=selected.page_number,
            native_crop_text=selected.native_crop_text,
            max_native_text_chars=self.max_native_text_chars,
        )

        warnings: list[str] = []
        request_started = datetime.now(timezone.utc).isoformat()

        if self.use_cache and not self.force_reanalysis and cache_path.is_file():
            cached = VisionCropResult.model_validate_json(
                cache_path.read_text(encoding="utf-8")
            )
            cached.provider_metadata.cache_hit = True
            crop_result_path.write_text(
                cached.model_dump_json(indent=2),
                encoding="utf-8",
            )
            return cached

        try:
            generation = self.provider.generate_structured_from_image(
                image_path=image_path,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=GeminiCropExtraction,
            )
        except GeminiVisionProviderError as error:
            if raw_path.exists():
                warnings.append("Raw response preserved from a prior attempt.")
            raise

        response_finished = datetime.now(timezone.utc).isoformat()
        raw_path.write_text(generation.raw_output_text, encoding="utf-8")

        provider_metadata = VisionProviderMetadata(
            model_name=generation.model_name,
            interaction_id=generation.interaction_id,
            request_timestamp_utc=request_started,
            response_timestamp_utc=response_finished,
            prompt_version=PROMPT_VERSION,
            image_sha256=image_sha256,
            image_size_bytes=len(image_bytes),
            image_mime_type=self._mime_type_for_path(image_path),
            uploaded_file_name=generation.uploaded_file_name,
            uploaded_file_uri=generation.uploaded_file_uri,
            uploaded_file_deleted=generation.uploaded_file_deleted,
            cache_hit=False,
        )

        crop_result = VisionCropResult(
            document_sha256=manifest.sha256,
            source_file_name=manifest.file_name,
            page_number=selected.page_number,
            crop_name=selected.crop_name,
            crop_purpose=selected.crop_purpose,
            crop_image_path=image_path,
            crop_bbox=selected.crop_bbox,
            native_crop_text=selected.native_crop_text,
            native_word_count=selected.native_word_count,
            extraction=generation.parsed,
            provider_metadata=provider_metadata,
            warnings=warnings + generation.warnings,
        )

        crop_result_path.write_text(
            crop_result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        metadata_path.write_text(
            json.dumps(
                {
                    "manifest_path": str(manifest_path),
                    "crop_name": selected.crop_name,
                    "page_number": selected.page_number,
                    "prompt_version": PROMPT_VERSION,
                    "response_schema_version": RESPONSE_SCHEMA_VERSION,
                    "provider_metadata": provider_metadata.model_dump(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        cache_path.write_text(
            crop_result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return crop_result

    def _select_crops(
        self,
        manifest: PDFExtractionResult,
        crop_names: list[str],
    ) -> list[SelectedCrop]:
        selected: list[SelectedCrop] = []
        wanted = set(crop_names)

        for page in manifest.pages:
            for crop in page.crops:
                if crop.name not in wanted:
                    continue
                image_path = self._resolve_crop_image_path(
                    manifest.output_directory,
                    crop.image_path,
                )
                selected.append(
                    SelectedCrop(
                        page_number=page.page_number,
                        crop_name=crop.name,
                        crop_purpose=crop.purpose,
                        crop_image_path=image_path,
                        crop_bbox=crop.bbox,
                        native_crop_text=crop.extracted_text,
                        native_word_count=crop.native_word_count,
                    )
                )

        if not selected:
            raise GeminiCropAnalyzerError(
                "No matching crop images were found in the manifest."
            )
        return selected

    @staticmethod
    def _resolve_crop_image_path(
        output_directory: Path,
        image_path: Path,
    ) -> Path:
        if image_path.is_absolute() and image_path.exists():
            return image_path
        candidate = output_directory / image_path
        if candidate.exists():
            return candidate.resolve()
        return image_path.resolve()

    @staticmethod
    def _artifact_name(selected: SelectedCrop, suffix: str) -> str:
        return (
            f"page_{selected.page_number:03d}_"
            f"{selected.crop_name}{suffix}"
        )

    @staticmethod
    def _cache_key(
        image_sha256: str,
        crop_name: str,
        model_name: str,
    ) -> str:
        payload = "|".join(
            [
                image_sha256,
                model_name,
                PROMPT_VERSION,
                crop_name,
                RESPONSE_SCHEMA_VERSION,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _mime_type_for_path(image_path: Path) -> str:
        suffix = image_path.suffix.lower()
        mapping = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        return mapping.get(suffix, "application/octet-stream")
