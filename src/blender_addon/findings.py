"""One finding, shared by every checker.

A **finding** is a problem found in the scene, described the same way
everywhere: a stable code, a severity, a localized message and the thing it
is about. The material doctor produces them (chapter 14), the export check
(chapter 17) and the UV analyzer (chapter 19) — and all of them are rendered
by the same report code.

The message lives in ``i18n`` under ``find.<code>``, so the same finding can
be shown to the user in their language and to the model in English:

    finding.text()        # the interface language
    finding.text("en")    # for the model's context
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from . import i18n

# Severities, from "this breaks the render" to "just so you know".
ERROR = "error"
WARNING = "warning"
INFO = "info"

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True)
class Finding:
    """One problem found in the scene."""

    code: str
    severity: str
    key: str                    # i18n key, "find.<code>"
    params: Dict = field(default_factory=dict)
    subject: str = ""           # what the finding is about
    fix: str = ""               # the fix id, "" when nothing can be done
    material: str = ""          # the material it belongs to

    def text(self, lang: Optional[str] = None) -> str:
        """The finding as a sentence, in the given language."""
        return i18n.T(self.key, lang=lang, **self.params)


def make(
    code: str,
    severity: str,
    params: Dict,
    subject: str = "",
    fix: str = "",
    material: str = "",
    key_prefix: str = "find",
) -> Finding:
    """Build a finding whose message lives under ``<key_prefix>.<code>``."""
    return Finding(
        code=code,
        severity=severity,
        key=f"{key_prefix}.{code}",
        params=params,
        subject=subject,
        fix=fix,
        material=material,
    )


def sorted_findings(findings):
    """The findings, worst first: errors, then warnings, then notes."""
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.code, f.subject),
    )


def summary(findings) -> Dict[str, int]:
    """How many findings of each severity."""
    counts = {ERROR: 0, WARNING: 0, INFO: 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def format_findings(findings, lang: Optional[str] = None) -> str:
    """The findings as a readable list, in the given language."""
    if not findings:
        return i18n.T("doctor.clean", lang=lang)
    marks = {ERROR: "✗", WARNING: "!", INFO: "·"}
    lines = []
    for finding in sorted_findings(findings):
        lines.append(f"{marks[finding.severity]} {finding.text(lang)}")
    return "\n".join(lines)
