from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FormField:
    label: str
    field_type: str
    required: bool = False
    manual_confirmation: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class FormInspectionResult:
    url: str
    inspected: bool = False
    fields: list[FormField] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def inspect_form(url: str) -> FormInspectionResult:
    """Placeholder for the v2 Playwright implementation.

    The v1 agent deliberately does not inspect or fill forms. This function exists
    so later browser work has a clear integration point without implying current
    form answers came from a real page.
    """

    return FormInspectionResult(
        url=url,
        inspected=False,
        warnings=["Form inspection is not implemented in v1; standard form answers only."],
    )
