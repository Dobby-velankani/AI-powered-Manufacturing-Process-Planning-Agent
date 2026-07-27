import json
import re
from pathlib import Path
from typing import Any

from app.models.reference_case import ReferenceCase


class ManufacturingKnowledgeBase:
    def __init__(
        self,
        database_directory: str | Path = "database",
    ) -> None:
        self.database_directory = Path(database_directory)
        self.cases: list[ReferenceCase] = []

    def load(self) -> list[ReferenceCase]:
        """
        Load every valid JSON manufacturing case from the database folder.
        """

        if not self.database_directory.exists():
            raise FileNotFoundError(
                f"Database folder not found: "
                f"{self.database_directory.resolve()}"
            )

        json_files = sorted(
            self.database_directory.glob("*.json")
        )

        if not json_files:
            raise FileNotFoundError(
                f"No JSON files found inside: "
                f"{self.database_directory.resolve()}"
            )

        loaded_cases: list[ReferenceCase] = []

        for file_path in json_files:
            try:
                with file_path.open(
                    "r",
                    encoding="utf-8-sig",
                ) as file:
                    data = json.load(file)

                if not isinstance(data, dict):
                    print(
                        f"Skipped {file_path.name}: "
                        "top-level JSON must be an object."
                    )
                    continue

                loaded_cases.append(
                    self._build_case(
                        file_path=file_path,
                        data=data,
                    )
                )

            except json.JSONDecodeError as error:
                print(
                    f"Invalid JSON in {file_path.name}: "
                    f"line {error.lineno}, "
                    f"column {error.colno}: "
                    f"{error.msg}"
                )

            except OSError as error:
                print(
                    f"Could not read {file_path.name}: "
                    f"{error}"
                )

        self.cases = loaded_cases
        return self.cases

    def _build_case(
        self,
        file_path: Path,
        data: dict[str, Any],
    ) -> ReferenceCase:
        """
        Convert one heterogeneous JSON file into a standard reference case.
        """

        case_id = file_path.stem

        part_name = self._extract_part_name(
            data=data,
            fallback=case_id.replace("_", " ").title(),
        )

        material = self._extract_material(data)

        searchable_text = self._flatten_to_text(
            {
                "case_id": case_id,
                "part_name": part_name,
                "material": material,
                "complete_case": data,
            }
        )

        return ReferenceCase(
            case_id=case_id,
            file_path=file_path,
            part_name=part_name,
            material=material,
            searchable_text=searchable_text,
            raw_data=data,
        )

    def _extract_part_name(
        self,
        data: dict[str, Any],
        fallback: str,
    ) -> str:
        """
        Extract the most suitable part name from the JSON case.
        """

        part_section = data.get("part")

        if isinstance(part_section, str):
            cleaned = self._clean_text(part_section)
            return cleaned or fallback

        if isinstance(part_section, dict):
            preferred_keys = (
                "part_name",
                "name",
                "description",
                "title",
                "component",
                "component_name",
                "part_number",
                "drawing_number",
            )

            for key in preferred_keys:
                value = part_section.get(key)

                if isinstance(value, str):
                    cleaned = self._clean_text(value)

                    if cleaned:
                        return cleaned

            # Fall back to the first meaningful string in the part section.
            for value in part_section.values():
                if isinstance(value, str):
                    cleaned = self._clean_text(value)

                    if cleaned:
                        return cleaned

        discovered = self._find_first_value(
            data,
            {
                "part_name",
                "component_name",
                "component",
                "description",
                "title",
            },
        )

        if discovered is not None:
            extracted = self._value_to_text(discovered)

            if extracted:
                return self._clean_text(extracted)

        return fallback

    def _extract_material(
        self,
        data: dict[str, Any],
    ) -> str:
        """
        Extract only the material grade or material description.

        This intentionally avoids combining:
        material + quantity + stock size + manufacturing notes.
        """

        for key in ("material", "raw_material"):
            if key not in data:
                continue

            extracted = self._extract_material_from_value(
                data[key]
            )

            if extracted:
                return extracted

        discovered = self._find_first_value(
            data,
            {
                "material",
                "material_grade",
                "grade",
                "steel_grade",
                "material_specification",
                "recommended_grade",
            },
        )

        if discovered is not None:
            extracted = self._extract_material_from_value(
                discovered
            )

            if extracted:
                return extracted

        return "Unknown"

    def _extract_material_from_value(
        self,
        value: Any,
    ) -> str | None:
        """
        Recursively extract the most likely material value.
        """

        if isinstance(value, str):
            cleaned = self._clean_material_text(value)
            return cleaned or None

        if isinstance(value, (int, float)):
            return str(value)

        if isinstance(value, list):
            for item in value:
                extracted = self._extract_material_from_value(
                    item
                )

                if extracted:
                    return extracted

            return None
            
        if isinstance(value, dict):
            preferred_keys = (
                "material",
                "material_grade",
                "grade",
                "steel_grade",
                "recommended_grade",
                "specification",
                "material_specification",
                "description",
                "type",
            )

            # First check fields that are most likely to contain a material.
            for key in preferred_keys:
                child = value.get(key)

                if child not in (None, "", [], {}):
                    extracted = self._extract_material_from_value(
                        child
                    )

                    if extracted:
                        return extracted

            # If no preferred field exists, inspect other fields but skip
            # fields that clearly describe quantity, size, or process notes.
            ignored_key_terms = (
                "quantity",
                "qty",
                "size",
                "dimension",
                "diameter",
                "length",
                "note",
                "reason",
                "allowance",
                "condition",
                "pieces",
                "stock_size",
            )

            for key, child in value.items():
                normalized_key = key.lower()

                if any(
                    term in normalized_key
                    for term in ignored_key_terms
                ):
                    continue

                extracted = self._extract_material_from_value(
                    child
                )

                if extracted:
                    return extracted

        return None

    def _clean_material_text(
        self,
        value: str,
    ) -> str:
        """
        Remove quantity, stock size, and notes from combined material strings.
        """

        cleaned = self._clean_text(value)

        separators = (
            " | ",
            "\r\n",
            "\n",
            "\r",
            "\t",
            "; quantity",
            ", quantity",
        )

        lowered = cleaned.lower()

        for separator in separators:
            separator_lower = separator.lower()

            if separator_lower in lowered:
                position = lowered.find(separator_lower)
                cleaned = cleaned[:position].strip()
                lowered = cleaned.lower()

        return cleaned

    @staticmethod
    def _clean_text(value: str) -> str:
        """
        Remove repeated spaces, tabs, and line breaks.
        """

        return " ".join(value.strip().split())

    def _find_first_value(
        self,
        value: Any,
        target_keys: set[str],
    ) -> Any:
        """
        Recursively find the first meaningful value matching a target key.
        """

        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in target_keys:
                    if child not in (None, "", [], {}):
                        return child

            for child in value.values():
                result = self._find_first_value(
                    child,
                    target_keys,
                )

                if result not in (None, "", [], {}):
                    return result

        elif isinstance(value, list):
            for child in value:
                result = self._find_first_value(
                    child,
                    target_keys,
                )

                if result not in (None, "", [], {}):
                    return result

        return None

    def _value_to_text(
        self,
        value: Any,
    ) -> str | None:
        """
        Return the first meaningful text value.

        This function does not join unrelated dictionary values.
        """

        if isinstance(value, str):
            cleaned = self._clean_text(value)
            return cleaned or None

        if isinstance(value, (int, float, bool)):
            return str(value)

        if isinstance(value, list):
            for item in value:
                extracted = self._value_to_text(item)

                if extracted:
                    return extracted

            return None

        if isinstance(value, dict):
            for child in value.values():
                extracted = self._value_to_text(child)

                if extracted:
                    return extracted

        return None

    def _flatten_to_text(
        self,
        value: Any,
    ) -> str:
        """
        Convert the complete JSON case into searchable lowercase text.
        """

        text_parts: list[str] = []

        def walk(item: Any) -> None:
            if item is None:
                return

            if isinstance(item, dict):
                for key, child in item.items():
                    text_parts.append(
                        key.replace("_", " ")
                    )
                    walk(child)

            elif isinstance(item, list):
                for child in item:
                    walk(child)

            elif isinstance(
                item,
                (str, int, float, bool),
            ):
                text_parts.append(str(item))

        walk(value)

        return self._clean_text(
            " ".join(text_parts)
        ).lower()

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """
        Convert text into unique searchable tokens.
        """

        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "this",
            "that",
            "part",
            "make",
            "manufacture",
            "manufacturing",
            "material",
            "process",
            "component",
        }

        return {
            token
            for token in re.findall(
                r"[a-z0-9]+",
                text.lower(),
            )
            if len(token) > 1
            and token not in stop_words
        }

    def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[tuple[float, ReferenceCase]]:
        """
        Search the loaded manufacturing cases using weighted keywords.

        Higher importance is given to:
        1. Part-name matches
        2. Material matches
        3. Case-ID matches
        4. General case-content matches
        """

        if top_k < 1:
            raise ValueError(
                "top_k must be at least 1."
            )

        if not self.cases:
            self.load()

        query_tokens = self._tokenize(query)

        if not query_tokens:
            return []

        results: list[
            tuple[float, ReferenceCase]
        ] = []

        for case in self.cases:
            complete_tokens = self._tokenize(
                case.searchable_text
            )

            part_tokens = self._tokenize(
                case.part_name
            )

            material_tokens = self._tokenize(
                case.material
            )

            case_id_tokens = self._tokenize(
                case.case_id
            )

            score = 0.0
            matched_tokens = 0

            for token in query_tokens:
                token_score = 0.0

                if token in part_tokens:
                    token_score += 8.0

                if token in material_tokens:
                    token_score += 6.0

                if token in case_id_tokens:
                    token_score += 5.0

                if token in complete_tokens:
                    token_score += 1.0

                if token_score > 0:
                    matched_tokens += 1
                    score += token_score

            coverage = (
                matched_tokens / len(query_tokens)
            )

            score += coverage * 10.0

            if score > 0:
                results.append(
                    (score, case)
                )

        results.sort(
            key=lambda result: (
                result[0],
                result[1].case_id,
            ),
            reverse=True,
        )

        return results[:top_k]