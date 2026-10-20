"""PII filter: questions are masked before they are logged or sent to any LLM.

The data itself has no personal information (district-week counts), but a user might type a name,
phone number or CNIC into the question box. Those never leave the machine and never reach a log.
"""
from __future__ import annotations

import re

PATTERNS = [
    ("CNIC", re.compile(r"\b\d{5}-?\d{7}-?\d\b")),                       # Pakistani national id
    ("PHONE", re.compile(r"(?:\+92|0092|\b0)3\d{2}[-\s]?\d{7}\b")),        # Pakistani mobile
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("PHONE", re.compile(r"\b\d{3,4}[-\s]\d{6,8}\b")),                     # landline
]


def mask(text: str) -> tuple[str, list[str]]:
    found = []
    for label, rx in PATTERNS:
        if rx.search(text):
            found.append(label)
            text = rx.sub(f"[{label}]", text)
    return text, found
