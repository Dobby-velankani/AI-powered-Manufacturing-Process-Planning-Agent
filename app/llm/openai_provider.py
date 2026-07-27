import os

import certifi
import httpx
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.llm.base_provider import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self) -> None:
        load_dotenv()

        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL")

        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY was not found. "
                "Add it to the .env file."
            )

        if not model:
            raise ValueError(
                "OPENAI_MODEL was not found. "
                "Add it to the .env file."
            )

        self.model = model
        self.client = OpenAI(
            api_key=api_key,
            http_client=httpx.Client(verify=certifi.where()),
        )

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=system_prompt,
                input=user_prompt,
            )

            output_text = response.output_text.strip()

            if not output_text:
                raise RuntimeError(
                    "The model returned an empty response."
                )

            return output_text

        except AuthenticationError as error:
            raise RuntimeError(
                "The OpenAI API key was rejected. "
                "Check OPENAI_API_KEY in the .env file."
            ) from error

        except RateLimitError as error:
            raise RuntimeError(
                "The OpenAI request was rate-limited, "
                "or the API account has insufficient credits."
            ) from error

        except APIConnectionError as error:
            raise RuntimeError(
                "The application could not connect "
                "to the OpenAI API."
            ) from error

        except APIStatusError as error:
            raise RuntimeError(
                f"OpenAI returned API status "
                f"{error.status_code}: {error.message}"
            ) from error
