import os
import time
from pathlib import Path
from typing import Callable, TypeVar

from dotenv import load_dotenv

# Load configuration before importing networking libraries.
load_dotenv()


def _configure_certificates() -> None:
    """
    Configure SSL certificates before importing Google GenAI/httpx.

    Option 1:
        GEMINI_CA_BUNDLE points to a PEM certificate file.

    Option 2:
        Use the Windows certificate store through truststore.
    """

    custom_ca_bundle = os.getenv("GEMINI_CA_BUNDLE")

    if custom_ca_bundle:
        certificate_path = Path(
            custom_ca_bundle
        ).expanduser()

        if not certificate_path.is_file():
            raise RuntimeError(
                "GEMINI_CA_BUNDLE points to a file "
                "that does not exist:\n"
                f"{certificate_path}"
            )

        # Google GenAI/httpx reads this environment setting.
        os.environ["SSL_CERT_FILE"] = str(
            certificate_path.resolve()
        )
        return

    # This must happen before importing google.genai or httpx.
    import truststore

    truststore.inject_into_ssl()


_configure_certificates()


# Import networking and SDK packages only after SSL setup.
import httpx
from google import genai
from google.genai import errors
from pydantic import ValidationError

from app.llm.base_provider import (
    LLMProvider,
    SchemaType,
)


ResultType = TypeVar("ResultType")


class RetryableGeminiError(RuntimeError):
    """
    Temporary/model-specific error that permits fallback.
    """


class GeminiProvider(LLMProvider):
    """
    Gemini provider for the manufacturing agent.

    Design:
    - Uses Gemini Interactions API.
    - Does not put an SSLContext inside HttpOptions.
    - Does not pass Pydantic classes directly to the SDK.
    - Converts Pydantic models into plain JSON Schema.
    - Validates Gemini JSON locally with Pydantic.
    - Supports primary and fallback models.
    """

    RETRYABLE_SERVER_CODES = {
        500,
        502,
        503,
        504,
    }

    DEFAULT_PRIMARY_MODEL = "gemini-3.5-flash"

    DEFAULT_FALLBACK_MODELS = (
        "gemini-3.1-flash-lite",
    )

    def __init__(self) -> None:
        api_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )

        if not api_key:
            raise ValueError(
                "Gemini API key was not found.\n"
                "Add GEMINI_API_KEY to the .env file."
            )

        primary_model = os.getenv(
            "GEMINI_MODEL",
            self.DEFAULT_PRIMARY_MODEL,
        ).strip()

        fallback_setting = os.getenv(
            "GEMINI_FALLBACK_MODELS",
            ",".join(
                self.DEFAULT_FALLBACK_MODELS
            ),
        )

        fallback_models = [
            model.strip()
            for model in fallback_setting.split(",")
            if model.strip()
        ]

        if not primary_model:
            raise ValueError(
                "GEMINI_MODEL cannot be empty."
            )

        self.models = self._remove_duplicates(
            [
                primary_model,
                *fallback_models,
            ]
        )

        self.primary_model = self.models[0]

        # Updated when a request succeeds.
        self.model = self.primary_model
        self.last_used_model = self.primary_model

        # No custom SSLContext is passed here.
        # SSL is already configured globally.
        self.client = genai.Client(
            api_key=api_key,
        )

        if not hasattr(
            self.client,
            "interactions",
        ):
            raise RuntimeError(
                "The installed google-genai version does "
                "not support the Interactions API.\n"
                "Upgrade it with:\n"
                'python -m pip install --upgrade '
                '"google-genai>=2.3.0"'
            )

    @staticmethod
    def _remove_duplicates(
        values: list[str],
    ) -> list[str]:
        unique_values: list[str] = []

        for value in values:
            cleaned = value.strip()

            if (
                cleaned
                and cleaned not in unique_values
            ):
                unique_values.append(cleaned)

        return unique_values

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """
        Generate an ordinary text response.
        """

        def request(model: str) -> str:
            interaction = (
                self.client.interactions.create(
                    model=model,
                    input=user_prompt,
                    system_instruction=system_prompt,
                    store=False,
                )
            )

            output_text = getattr(
                interaction,
                "output_text",
                None,
            )

            if not output_text:
                raise RetryableGeminiError(
                    f"Gemini model {model} returned "
                    "an empty text response."
                )

            return output_text.strip()

        return self._execute_with_fallback(
            request=request,
            request_name="text generation",
        )

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[SchemaType],
    ) -> SchemaType:
        """
        Generate structured JSON.

        Pydantic creates a plain JSON Schema dictionary.
        The dictionary is sent to Gemini.
        Returned JSON is validated locally with Pydantic.
        """

        try:
            json_schema = (
                response_model.model_json_schema()
            )

        except RecursionError as error:
            raise RuntimeError(
                "Pydantic could not create the JSON "
                "Schema.\n"
                "Check app/models/process_plan.py for "
                "a model that refers to itself."
            ) from error

        def request(model: str) -> SchemaType:
            interaction = (
                self.client.interactions.create(
                    model=model,
                    input=user_prompt,
                    system_instruction=system_prompt,
                    store=False,
                    response_format={
                        "type": "text",
                        "mime_type": (
                            "application/json"
                        ),
                        "schema": json_schema,
                    },
                )
            )

            output_text = getattr(
                interaction,
                "output_text",
                None,
            )

            if not output_text:
                raise RetryableGeminiError(
                    f"Gemini model {model} returned "
                    "no structured JSON."
                )

            cleaned_json = self._clean_json_text(
                output_text
            )

            try:
                return (
                    response_model.model_validate_json(
                        cleaned_json
                    )
                )

            except ValidationError as error:
                validation_details = error.errors(
                    include_url=False
                )

                raise RetryableGeminiError(
                    f"Gemini model {model} returned "
                    "JSON that did not match the "
                    "ProcessPlan structure.\n"
                    f"First validation errors: "
                    f"{validation_details[:10]}"
                ) from error

        return self._execute_with_fallback(
            request=request,
            request_name="structured generation",
        )

    @staticmethod
    def _clean_json_text(
        response_text: str,
    ) -> str:
        """
        Remove accidental Markdown code fences.
        """

        cleaned = response_text.strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned[len("```json"):]

        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        return cleaned.strip()

    def _execute_with_fallback(
        self,
        request: Callable[[str], ResultType],
        request_name: str,
    ) -> ResultType:
        """
        Try the primary model, then fallback models.
        """

        failures: list[str] = []

        for index, model in enumerate(
            self.models,
            start=1,
        ):
            try:
                if index > 1:
                    print(
                        "Trying fallback Gemini model: "
                        f"{model}"
                    )

                result = request(model)

                self.model = model
                self.last_used_model = model

                return result

            except RecursionError as error:
                # Recursion is a local programming/SDK
                # problem. Another model cannot fix it.
                raise RuntimeError(
                    "A local Python recursion error "
                    "occurred before Gemini completed "
                    "the request.\n"
                    "Fallback was not attempted because "
                    "this is not a model-capacity error."
                ) from error

            except RetryableGeminiError as error:
                failures.append(
                    f"{model}: {error}"
                )

                if index < len(self.models):
                    time.sleep(2)
                    continue

                break

            except errors.ServerError as error:
                message = self._error_message(
                    error
                )

                failures.append(
                    f"{model}: server error "
                    f"{error.code} - {message}"
                )

                if (
                    error.code
                    not in self.RETRYABLE_SERVER_CODES
                ):
                    raise RuntimeError(
                        f"Gemini {request_name} failed.\n"
                        f"Model: {model}\n"
                        f"Status: {error.code}\n"
                        f"Details: {message}"
                    ) from error

                if index < len(self.models):
                    time.sleep(2)
                    continue

                break

            except errors.ClientError as error:
                message = self._error_message(
                    error
                )

                if error.code == 404:
                    failures.append(
                        f"{model}: unavailable - "
                        f"{message}"
                    )

                    if index < len(self.models):
                        continue

                    break

                if error.code == 429:
                    failures.append(
                        f"{model}: quota/rate limit - "
                        f"{message}"
                    )

                    if index < len(self.models):
                        time.sleep(2)
                        continue

                    break

                raise RuntimeError(
                    "Gemini rejected the request.\n"
                    f"Model: {model}\n"
                    f"Status: {error.code} "
                    f"{getattr(error, 'status', '')}\n"
                    f"Details: {message}"
                ) from error

            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
            ) as error:
                failures.append(
                    f"{model}: network connection "
                    f"error - {error}"
                )

                if index < len(self.models):
                    time.sleep(2)
                    continue

                break

            except httpx.TimeoutException as error:
                failures.append(
                    f"{model}: request timed out - "
                    f"{error}"
                )

                if index < len(self.models):
                    time.sleep(2)
                    continue

                break

            except RuntimeError:
                raise

            except Exception as error:
                raise RuntimeError(
                    f"Gemini {request_name} failed.\n"
                    f"Model: {model}\n"
                    f"Error type: "
                    f"{type(error).__name__}\n"
                    f"Details: {error}"
                ) from error

        failure_report = "\n".join(
            f"- {failure}"
            for failure in failures
        )

        raise RuntimeError(
            "All configured Gemini models failed.\n\n"
            f"{failure_report}\n\n"
            "No process plan was generated."
        )

    @staticmethod
    def _error_message(
        error: Exception,
    ) -> str:
        message = getattr(
            error,
            "message",
            None,
        )

        if message:
            return str(message)

        status = getattr(
            error,
            "status",
            None,
        )

        if status:
            return str(status)

        return str(error)

    def close(self) -> None:
        """
        Close the Gemini client.
        """

        try:
            self.client.close()
        except Exception:
            pass