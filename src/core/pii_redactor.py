"""PII 脱敏模块（P0-2）

基于内置正则的兜底实现（不引入 presidio / spacy 等重型依赖，避免模型下载与网络问题），
覆盖四类常见敏感信息：
- 手机号：1[3-9] 开头 11 位
- 身份证号：17 位数字 + 数字 / X
- 邮箱：标准邮箱
- IPv4：点分十进制

三种模式：
- mask（默认）：按类型部分掩码（如 138****5678）
- remove：直接删除
- replace：替换为占位符（默认 [PII]）

入库前脱敏（config.USE_PII_REDACTION=true 时生效）与日志输出掩码（mask_text）共用本模块。
"""
import re
from dataclasses import dataclass, field

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IPV4_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")

# (类型名, 正则)
PII_PATTERNS = (
    ("phone", PHONE_RE),
    ("id_card", ID_CARD_RE),
    ("email", EMAIL_RE),
    ("ipv4", IPV4_RE),
)


@dataclass
class RedactResult:
    """脱敏结果"""
    text: str
    count: int = 0
    spans: list = field(default_factory=list)  # (start, end, kind)，基于脱敏过程中的当前文本偏移


def _mask(kind: str, value: str) -> str:
    """按类型生成掩码文本"""
    if kind == "phone":
        return value[:3] + "****" + value[-4:]
    if kind == "id_card":
        return value[:6] + "********" + value[-4:]
    if kind == "email":
        _, _, domain = value.partition("@")
        return "***@" + domain
    if kind == "ipv4":
        parts = value.split(".")
        return ".".join(parts[:2] + ["*", "*"])
    return "[PII]"


def _redact_text_with(text: str, mode: str, placeholder: str) -> RedactResult:
    """核心脱敏逻辑"""
    if not text:
        return RedactResult(text=text)
    result = RedactResult(text=text)
    for kind, pattern in PII_PATTERNS:
        def _repl(m, _kind=kind):
            result.spans.append((m.start(), m.end(), _kind))
            raw = m.group(0)
            if mode == "remove":
                return ""
            if mode == "replace":
                return placeholder
            return _mask(_kind, raw)
        result.text = pattern.sub(_repl, result.text)
    result.count = len(result.spans)
    return result


def redact_text(text: str, mode: str = "mask", placeholder: str = "[PII]") -> RedactResult:
    """对单个文本脱敏，返回 RedactResult(text/count/spans)"""
    return _redact_text_with(text, mode, placeholder)


def redact_texts(texts: list[str], mode: str = "mask", placeholder: str = "[PII]") -> list[str]:
    """批量脱敏（入库前使用），返回脱敏后的文本列表"""
    return [_redact_text_with(t, mode, placeholder).text for t in texts]


def mask_text(text: str) -> str:
    """日志输出掩码：固定 mask 模式，快速、无结构化返回"""
    if not text:
        return text
    for kind, pattern in PII_PATTERNS:
        text = pattern.sub(lambda m, _k=kind: _mask(_k, m.group(0)), text)
    return text
