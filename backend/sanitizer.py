"""Deterministic, stateless text processing."""

import re

from detector import matches
from errors import APIError


def decode_text(content: bytes) -> str:
    try:
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = content.decode("utf-16")
        else:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = content.decode("cp949")
        if "\x00" in text:
            raise ValueError("Binary content")
        return text
    except (UnicodeError, ValueError) as exc:
        raise APIError(422, "VALIDATION_ERROR", "검사 가능한 텍스트 인코딩이 아닙니다.") from exc


def sanitize(text: str, actions: dict[str, str]) -> str:
    replacements = []
    for kind, match in matches(text):
        action = actions.get(kind)
        if not action:
            continue
        value = ""
        if action == "MASK":
            if kind == "RRN":
                digits = re.sub(r"\D", "", match.group())
                value = digits[:6] + "-" + digits[6] + "******"
            elif kind == "PHONE":
                digits = re.sub(r"\D", "", match.group())
                value = digits[:3] + "-****-" + digits[-4:]
            else:
                value = "[REDACTED]"
        replacements.append((match.start(), match.end(), value))
    # Merge overlapping detections: a selected deletion wins; otherwise redact the union.
    merged = []
    for start, end, value in sorted(replacements):
        if merged and start < merged[-1][1]:
            previous = merged.pop()
            merged.append(
                (
                    previous[0],
                    max(previous[1], end),
                    "" if not value or not previous[2] else "[REDACTED]",
                )
            )
        else:
            merged.append((start, end, value))
    parts, cursor = [], 0
    for start, end, value in merged:
        parts.extend((text[cursor:start], value))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)
