"""P0-2 PII 脱敏 单元测试"""
from src.core.pii_redactor import (
    redact_text,
    redact_texts,
    mask_text,
    RedactResult,
)


def test_redact_phone_mask():
    r = redact_text("联系我 13812345678 即可")
    assert "138****5678" in r.text
    assert "13812345678" not in r.text
    assert r.count >= 1


def test_redact_id_card_mask():
    r = redact_text("证件号 110101199003078510")
    assert "110101********8510" in r.text
    assert r.count >= 1


def test_redact_email_mask():
    r = redact_text("邮箱 zhangsan@example.com 收件")
    assert "***@example.com" in r.text
    assert "zhangsan@example.com" not in r.text


def test_redact_ipv4_mask():
    r = redact_text("服务器 192.168.1.10 端口")
    assert "192.168.*.*" in r.text
    assert "192.168.1.10" not in r.text


def test_redact_remove_mode():
    r = redact_text("手机 13812345678 已停用", mode="remove")
    assert "13812345678" not in r.text
    assert "手机" in r.text


def test_redact_replace_mode():
    r = redact_text("邮箱 test@x.com 已用", mode="replace", placeholder="[PII]")
    assert "[PII]" in r.text
    assert "test@x.com" not in r.text


def test_redact_multiple_types():
    r = redact_text("手机13812345678 邮箱 a@b.com")
    assert r.count == 2


def test_redact_texts_batch():
    out = redact_texts(["电话 13912345678", "正常文本"])
    assert "139****5678" in out[0]
    assert out[1] == "正常文本"


def test_redact_empty():
    assert redact_text("").text == ""
    assert redact_texts([]) == []


def test_redact_result_dataclass():
    r = redact_text("13812345678")
    assert isinstance(r, RedactResult)
    assert isinstance(r.spans, list)
    assert r.spans[0][2] == "phone"


def test_mask_text_log_safe():
    # 日志掩码：固定 mask 模式，返回纯文本
    out = mask_text("用户 13812345678 登录")
    assert "138****5678" in out
    assert "13812345678" not in out


def test_mask_text_plain_passthrough():
    assert mask_text("无敏感信息") == "无敏感信息"
    assert mask_text("") == ""
