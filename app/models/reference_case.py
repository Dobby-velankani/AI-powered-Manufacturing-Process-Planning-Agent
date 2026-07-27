from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReferenceCase:
    case_id: str
    file_path: Path
    part_name: str
    material: str
    searchable_text: str
    raw_data: dict[str, Any] = field(repr=False)

    def short_summary(self) -> str:
        return (
            f"Case ID: {self.case_id}\n"
            f"Part: {self.part_name}\n"
            f"Material: {self.material}\n"
            f"File: {self.file_path.name}"
        )