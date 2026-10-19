"""Pure detection rules: return types/counts, never text or match positions."""

import re
from collections import Counter
from datetime import date

TYPES = ("RRN", "PHONE", "EMAIL", "AWS_ACCESS_KEY", "API_KEY", "CARD")
PATTERNS = {
    "PHONE": re.compile(r"(?<!\d)01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)"),
    "EMAIL": re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.ASCII),
    "AWS_ACCESS_KEY": re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    "API_KEY": re.compile(
        r"(?<![\w-])(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
        r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})(?![\w-])"
        r"|\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|bearer)"
        r"[\s\"']*[:= ]\s*[\"']?[A-Za-z0-9_./+\-=]{16,}",
        re.IGNORECASE,
    ),
}
RRN = re.compile(r"(?<!\d)(\d{6})[- ]?([1-4]\d{6})(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def valid_rrn(value: str) -> bool:
    digits = value.replace("-", "").replace(" ", "")
    try:
        century = 1900 if digits[6] in "12" else 2000
        date(century + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
        return False
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    return (11 - sum(int(n) * w for n, w in zip(digits, weights)) % 11) % 10 == int(digits[-1])


def valid_card(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        n = int(digit)
        if index % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def matches(text: str):
    """Internal matches shared by scanning and deterministic sanitization."""
    for kind, pattern in {**PATTERNS, "RRN": RRN, "CARD": CARD}.items():
        for match in pattern.finditer(text):
            if kind == "RRN" and not valid_rrn(match.group()):
                continue
            if kind == "CARD" and not valid_card(match.group()):
                continue
            yield kind, match


def scan(text: str) -> dict:
    counts = Counter(kind for kind, _ in matches(text))
    findings = [{"type": kind, "count": counts[kind]} for kind in TYPES if counts[kind]]
    return {"blocked": bool(findings), "findings": findings}


def scan_file(text: str) -> dict:
    # Scan whole matches so long tokens and boundary-spanning emails are never truncated.
    return scan(text)
