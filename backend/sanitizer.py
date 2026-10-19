"""Deterministic text processing and short-lived, memory-only drafts."""

import re
import time
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4

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


@dataclass(frozen=True)
class Draft:
    content: bytes
    filename: str
    deadline: float


class Drafts:
    def __init__(self, ttl: int, max_bytes: int):
        self.ttl, self.max_bytes = ttl, max_bytes
        self.entries: dict[str, Draft] = {}
        self.busy: set[str] = set()
        self.lock = Lock()

    def purge(self):
        with self.lock:
            for identifier, draft in list(self.entries.items()):
                if draft.deadline <= time.monotonic():
                    del self.entries[identifier]

    def add(self, content: bytes, filename: str) -> str:
        self.purge()
        with self.lock:
            if sum(len(d.content) for d in self.entries.values()) + len(content) > self.max_bytes:
                raise APIError(
                    503,
                    "CAPACITY_EXCEEDED",
                    "정제 대기 용량이 가득 찼습니다. 잠시 후 다시 시도하세요.",
                )
            identifier = uuid4().hex
            self.entries[identifier] = Draft(content, filename, time.monotonic() + self.ttl)
            return identifier

    def checkout(self, identifier: str) -> Draft:
        self.purge()
        with self.lock:
            if identifier not in self.entries:
                raise APIError(410, "EXPIRED", "만료되었거나 폐기된 처리본입니다. 다시 정제하세요.")
            if identifier in self.busy:
                raise APIError(409, "IN_PROGRESS", "이미 전송 중인 처리본입니다.")
            self.busy.add(identifier)
            return self.entries[identifier]

    def finish(self, identifier: str, success: bool):
        with self.lock:
            self.busy.discard(identifier)
            draft = self.entries.get(identifier)
            if success or (draft and draft.deadline <= time.monotonic()):
                self.entries.pop(identifier, None)

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.busy.clear()
