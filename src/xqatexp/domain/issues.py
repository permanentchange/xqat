from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any

from xqatexp.domain.enums import Severity


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: Severity
    stage: str
    scope: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""
    decision_date: date | None = None
    security_id: str | None = None
    field: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
