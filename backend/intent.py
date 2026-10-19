"""Metadata-only SecureGuard assistant; never reads or changes file content."""

import re

LABELS = {
    "RRN": "주민등록번호",
    "PHONE": "휴대전화번호",
    "EMAIL": "이메일",
    "AWS_ACCESS_KEY": "AWS Access Key",
    "API_KEY": "API Key·토큰",
    "CARD": "카드번호",
}
ALIASES = {
    "RRN": ("주민", "rrn"),
    "PHONE": ("전화", "휴대폰", "휴대전화", "phone"),
    "EMAIL": ("이메일", "메일", "email"),
    "AWS_ACCESS_KEY": ("aws", "액세스 키", "access key"),
    "API_KEY": ("api", "토큰", "token", "비밀 키"),
    "CARD": ("카드", "card"),
}


def interpret(message: str, findings: list[dict]) -> dict:
    text = message.lower()
    available = [kind for kind in LABELS if any(f["type"] == kind for f in findings)]
    summary = ", ".join(f"{LABELS[f['type']]} {f['count']}건" for f in findings)
    explanation = (
        f"{summary}이 감지되어 원본 전송을 보류했습니다."
        if findings
        else "탐지된 민감정보가 없습니다."
    )
    options = [
        {"action": action, "targets": available, "label": label}
        for action, label in (
            ("MASK", "마스킹"),
            ("DELETE", "해당 항목 삭제"),
            ("CANCEL", "전송 취소"),
        )
    ]
    options[-1]["targets"] = []
    action, targets = "CLARIFY", []
    mask = any(word in text for word in ("마스킹", "가려", "가리", "가려줘", "가리고", "mask"))
    delete = any(word in text for word in ("삭제", "지워", "제거", "delete", "remove"))
    cancel = any(word in text for word in ("취소", "cancel"))
    # Ambiguous, negated and mixed requests must be confirmed with explicit buttons.
    ambiguous = any(word in text for word in ("말고", "하지", "않", "빼고", "제외"))
    if cancel and not mask and not delete and not ambiguous:
        action = "CANCEL"
    elif mask != delete and not cancel and not ambiguous:
        mentioned = [kind for kind, words in ALIASES.items() if any(w in text for w in words)]
        if re.search(r"전체|모두|전부|\ball\b", text) and not mentioned:
            mentioned = available
        if mentioned and all(kind in available for kind in mentioned):
            action, targets = ("MASK" if mask else "DELETE"), mentioned
    reply = {
        "CLARIFY": explanation + " 처리할 유형과 조치를 버튼으로 선택하세요.",
        "MASK": "선택한 유형을 마스킹한 처리본을 만들고 재검사합니다. 미리보기 확인과 승인 후 전송하세요.",
        "DELETE": "선택한 민감정보 값을 삭제한 처리본을 만들고 재검사합니다. 미리보기 확인과 승인 후 전송하세요.",
        "CANCEL": "전송을 취소하세요. 파일은 전송하지 않습니다.",
    }[action]
    return {"action": action, "targets": targets, "reply": reply, "options": options}
