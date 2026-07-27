"""
Gemini multimodal vision provider for Phase 2 crop analysis.

This module is separate from app.llm.gemini_provider and does not modify it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generic, TypeVar

from dotenv import load_dotenv

load_dotenv()


def _configure_certificates() -> None:
  custom_ca_bundle = os.getenv("GEMINI_CA_BUNDLE")
  if custom_ca_bundle:
    certificate_path = Path(custom_ca_bundle).expanduser()
    if not certificate_path.is_file():
      raise RuntimeError(
        "GEMINI_CA_BUNDLE points to a file that does not exist:\n"
        f"{certificate_path}"
      )
    os.environ["SSL_CERT_FILE"] = str(certificate_path.resolve())
    return
  import truststore
  truststore.inject_into_ssl()


_configure_certificates()

import httpx
from google import genai
from google.genai import errors
from pydantic import BaseModel, ValidationError

SchemaType = TypeVar("SchemaType", bound=BaseModel)
ResultType = TypeVar("ResultType")


class GeminiVisionProviderError(RuntimeError):
  """Raised when Gemini vision extraction fails."""


class RetryableVisionError(GeminiVisionProviderError):
  """Temporary error that may permit model fallback."""


@dataclass
class VisionGenerationResult(Generic[SchemaType]):
  parsed: SchemaType
  raw_output_text: str
  model_name: str
  interaction_id: str | None
  uploaded_file_name: str | None = None
  uploaded_file_uri: str | None = None
  uploaded_file_deleted: bool | None = None
  warnings: list[str] = field(default_factory=list)


class GeminiVisionProvider:
  RETRYABLE_SERVER_CODES = {500, 502, 503, 504}
  SUPPORTED_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
  }

  def __init__(
    self,
    maximum_image_size_mb: int = 20,
    delete_uploaded_files: bool = True,
    max_retries_per_model: int = 2,
  ) -> None:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
      raise GeminiVisionProviderError(
        "Gemini API key was not found. Add GEMINI_API_KEY to the .env file."
      )

    primary_model = os.getenv("GEMINI_VISION_MODEL") or os.getenv("GEMINI_MODEL")
    if not primary_model or not primary_model.strip():
      raise GeminiVisionProviderError(
        "No Gemini vision model configured. Set GEMINI_VISION_MODEL or GEMINI_MODEL."
      )

    fallback_setting = os.getenv("GEMINI_VISION_FALLBACK_MODELS", "")
    fallback_models = [
      model.strip()
      for model in fallback_setting.split(",")
      if model.strip()
    ]

    self.models = self._remove_duplicates([primary_model.strip(), *fallback_models])
    self.model = self.models[0]
    self.maximum_image_size_bytes = maximum_image_size_mb * 1024 * 1024
    self.delete_uploaded_files = delete_uploaded_files
    self.max_retries_per_model = max_retries_per_model

    self.client = genai.Client(api_key=api_key)
    if not hasattr(self.client, "interactions"):
      raise GeminiVisionProviderError(
        "The installed google-genai version does not support the Interactions API."
      )

  @staticmethod
  def _remove_duplicates(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
      if value and value not in unique:
        unique.append(value)
    return unique

  def generate_structured_from_image(
    self,
    image_path: str | Path,
    system_prompt: str,
    user_prompt: str,
    response_model: type[SchemaType],
  ) -> VisionGenerationResult[SchemaType]:
    image_path = Path(image_path).expanduser().resolve()
    image_bytes, mime_type, image_hash = self._validate_image(image_path)

    try:
      json_schema = response_model.model_json_schema()
    except RecursionError as error:
      raise GeminiVisionProviderError(
        "Pydantic could not create the JSON Schema for vision output."
      ) from error

    def request(model: str) -> VisionGenerationResult[SchemaType]:
      request_started = datetime.now(timezone.utc).isoformat()
      image_b64 = base64.b64encode(image_bytes).decode("ascii")

      interaction = self.client.interactions.create(
        model=model,
        input=[
          {
            "type": "image",
            "mime_type": mime_type,
            "data": image_b64,
          },
          {
            "type": "text",
            "text": user_prompt,
          },
        ],
        system_instruction=system_prompt,
        store=False,
        response_format={
          "type": "text",
          "mime_type": "application/json",
          "schema": json_schema,
        },
      )

      response_finished = datetime.now(timezone.utc).isoformat()
      raw_output_text = getattr(interaction, "output_text", None) or ""
      if not raw_output_text.strip():
        raise RetryableVisionError(
          f"Gemini model {model} returned no structured JSON."
        )

      cleaned_json = self._clean_json_text(raw_output_text)
      try:
        parsed = response_model.model_validate_json(cleaned_json)
      except ValidationError as error:
        raise GeminiVisionProviderError(
          "Gemini returned JSON that did not match the expected schema.\n"
          f"Validation errors: {error.errors(include_url=False)[:10]}"
        ) from error

      interaction_id = getattr(interaction, "id", None)
      return VisionGenerationResult(
        parsed=parsed,
        raw_output_text=raw_output_text,
        model_name=model,
        interaction_id=interaction_id,
        uploaded_file_name=None,
        uploaded_file_uri=None,
        uploaded_file_deleted=None,
        warnings=[],
      )

    result = self._execute_with_fallback(
      request=request,
      request_name="structured vision generation",
    )
    self.model = result.model_name
    return result

  def _validate_image(
    self,
    image_path: Path,
  ) -> tuple[bytes, str, str]:
    if not image_path.exists():
      raise GeminiVisionProviderError(f"Image does not exist: {image_path}")
    if not image_path.is_file():
      raise GeminiVisionProviderError(f"Path is not a file: {image_path}")

    mime_type = self.SUPPORTED_SUFFIXES.get(image_path.suffix.lower())
    if mime_type is None:
      raise GeminiVisionProviderError(
        "Only PNG, JPG, JPEG, and WEBP crop images are supported."
      )

    file_size = image_path.stat().st_size
    if file_size <= 0:
      raise GeminiVisionProviderError("Image file is empty.")
    if file_size > self.maximum_image_size_bytes:
      raise GeminiVisionProviderError(
        f"Image exceeds maximum size of "
        f"{self.maximum_image_size_bytes // (1024 * 1024)} MB."
      )

    image_bytes = image_path.read_bytes()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    return image_bytes, mime_type, image_hash

  def _execute_with_fallback(
    self,
    request: Callable[[str], VisionGenerationResult[SchemaType]],
    request_name: str,
  ) -> VisionGenerationResult[SchemaType]:
    failures: list[str] = []

    for index, model in enumerate(self.models, start=1):
      for attempt in range(1, self.max_retries_per_model + 1):
        try:
          return request(model)
        except RecursionError as error:
          raise GeminiVisionProviderError(
            "A local Python recursion error occurred during vision generation."
          ) from error
        except RetryableVisionError as error:
          failures.append(f"{model} attempt {attempt}: {error}")
          if attempt < self.max_retries_per_model:
            time.sleep(2)
            continue
          break
        except errors.ServerError as error:
          message = self._error_message(error)
          failures.append(f"{model}: server error {error.code} - {message}")
          if error.code in self.RETRYABLE_SERVER_CODES and attempt < self.max_retries_per_model:
            time.sleep(2)
            continue
          if error.code not in self.RETRYABLE_SERVER_CODES:
            raise GeminiVisionProviderError(
              f"Gemini {request_name} failed.\nModel: {model}\nDetails: {message}"
            ) from error
          break
        except errors.ClientError as error:
          message = self._error_message(error)
          if error.code == 404:
            failures.append(f"{model}: unavailable - {message}")
            break
          if error.code == 429:
            failures.append(f"{model}: quota/rate limit - {message}")
            if attempt < self.max_retries_per_model:
              time.sleep(2)
              continue
            break
          raise GeminiVisionProviderError(
            f"Gemini rejected the vision request.\nModel: {model}\nDetails: {message}"
          ) from error
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as error:
          failures.append(f"{model}: network error - {error}")
          if attempt < self.max_retries_per_model:
            time.sleep(2)
            continue
          break
        except GeminiVisionProviderError:
          raise
        except Exception as error:
          raise GeminiVisionProviderError(
            f"Gemini {request_name} failed.\nModel: {model}\n"
            f"Error type: {type(error).__name__}\nDetails: {error}"
          ) from error

      if index < len(self.models):
        continue

    failure_report = "\n".join(f"- {failure}" for failure in failures)
    raise GeminiVisionProviderError(
      "All configured Gemini vision models failed.\n\n"
      f"{failure_report}"
    )

  @staticmethod
  def _clean_json_text(response_text: str) -> str:
    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
      cleaned = cleaned[len("```json"):]
    elif cleaned.startswith("```"):
      cleaned = cleaned[len("```"):]
    if cleaned.endswith("```"):
      cleaned = cleaned[:-3]
    return cleaned.strip()

  @staticmethod
  def _error_message(error: Exception) -> str:
    message = getattr(error, "message", None)
    if message:
      return str(message)
    status = getattr(error, "status", None)
    if status:
      return str(status)
    return str(error)

  def close(self) -> None:
    try:
      self.client.close()
    except Exception:
      pass
