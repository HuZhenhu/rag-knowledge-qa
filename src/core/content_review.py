"""T2.6 防滥用 — 上传内容审核（文本 + 图片）。

- TEXT_BLOCKLIST: 命中直接拒绝（违禁词，如盗取/攻击类）
- TEXT_SENSITIVE_FLAGS: 命中标记敏感（PII 等），可选阻断或转人工
- IMAGE_ALLOWED_TYPES / IMAGE_MAX_SIZE: 图片类型白名单与大小上限
- ContentReviewer: 统一入口，返回 {"approved": bool, "reasons": [..]}
"""
from __future__ import annotations

import re
from typing import Any

# 违禁内容（命中即拒绝）
TEXT_BLOCKLIST: tuple[str, ...] = (
    "盗取",
    "破解",
    "钓鱼",
    "木马",
    "勒索",
    "色情",
    "毒品",
    "代开发票",
    "赌博",
)

# 敏感内容（命中标记敏感；是否阻断由策略决定）
TEXT_SENSITIVE_FLAGS: tuple[str, ...] = (
    "身份证号",
    "身份证",
    "银行卡",
    "手机号",
    "手机号码",
    "社保号",
    "家庭住址",
    "银行密码",
    "支付密码",
)

IMAGE_ALLOWED_TYPES: tuple[str, ...] = ("jpg", "jpeg", "png", "webp", "bmp", "gif")
IMAGE_MAX_SIZE = 20 * 1024 * 1024  # 20MB

_SENSITIVE_RE = re.compile(r"|".join(re.escape(w) for w in TEXT_SENSITIVE_FLAGS))


class ContentReviewer:
    """上传内容审核器：review_text / review_image。"""

    def review_text(self, text: str) -> dict[str, Any]:
        reasons: list[str] = []
        low = (text or "").lower()
        for w in TEXT_BLOCKLIST:
            if w in low:
                reasons.append(f"blocked: contains banned term '{w}'")
        flagged = [w for w in TEXT_SENSITIVE_FLAGS if w in low]
        if flagged:
            reasons.append("sensitive: " + ",".join(flagged[:3]))
        approved = not any(r.startswith("blocked") for r in reasons)
        return {"approved": approved, "reasons": reasons,
                "sensitive": bool(flagged)}

    def review_image(self, filename: str, size_bytes: int) -> dict[str, Any]:
        reasons: list[str] = []
        ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
        if ext not in IMAGE_ALLOWED_TYPES:
            reasons.append(f"type: unsupported image type '{ext or 'unknown'}'")
        if size_bytes > IMAGE_MAX_SIZE:
            reasons.append(f"size: exceeds {IMAGE_MAX_SIZE} bytes")
        approved = not reasons
        return {"approved": approved, "reasons": reasons}
