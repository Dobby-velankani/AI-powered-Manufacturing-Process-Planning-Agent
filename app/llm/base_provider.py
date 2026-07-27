from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel


SchemaType = TypeVar(
    "SchemaType",
    bound=BaseModel,
)


class LLMProvider(ABC):
    """
    Common interface for all language-model providers.
    """

    @abstractmethod
    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[SchemaType],
    ) -> SchemaType:
        raise NotImplementedError