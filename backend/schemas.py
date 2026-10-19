from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from detector import TYPES

DetectionType = Literal["RRN", "PHONE", "EMAIL", "AWS_ACCESS_KEY", "API_KEY", "CARD"]


class ScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=50_000)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: DetectionType
    count: int = Field(ge=1)


class SanitizeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["MASK", "DELETE", "CANCEL"]
    targets: list[DetectionType] = Field(default_factory=list, max_length=len(TYPES))


class SanitizeResult(BaseModel):
    sanitize_id: str | None
    findings_before: list[Finding]
    findings_after: list[Finding]
    preview: str
    clean: bool
    cancelled: bool = False
    sha256: str | None = None
    content_b64: str | None = None


class AssistContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(default="", max_length=255)
    findings: list[Finding] = Field(max_length=len(TYPES))


class AssistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=2000)
    context: AssistContext


class AssistOption(BaseModel):
    action: Literal["MASK", "DELETE", "CANCEL"]
    targets: list[DetectionType]
    label: str


class AssistResult(BaseModel):
    action: Literal["MASK", "DELETE", "CANCEL", "CLARIFY"]
    targets: list[DetectionType]
    reply: str
    options: list[AssistOption]


class ScanResult(BaseModel):
    blocked: bool
    findings: list[Finding]


class FileScanResult(ScanResult):
    filename: str
    scan_id: str
    sha256: str


class LogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["BLOCK"]
    types: list[DetectionType] = Field(min_length=1, max_length=len(TYPES))
