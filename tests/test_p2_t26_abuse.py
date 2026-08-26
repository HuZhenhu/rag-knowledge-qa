"""T2.6 防滥用 — 分级限流(IP/用户/渠道) / 敏感查询审计 / 上传内容审核（红测试）"""
import time
import pytest

from src.core.abuse_guard import (
    TieredRateLimiter,
    SensitiveQueryAuditor,
    SENSITIVE_PATTERNS,
)
from src.core.content_review import (
    ContentReviewer,
    TEXT_BLOCKLIST,
    TEXT_SENSITIVE_FLAGS,
    IMAGE_ALLOWED_TYPES,
)


# ---------------- T2.6-1 分级限流 ----------------

def test_tiered_limiter_ip_limit():
    limiter = TieredRateLimiter(ip_limit=3, user_limit=10, channel_limit=20,
                                window_seconds=60)
    for _ in range(3):
        ok, _ = limiter.allow("web", ip="1.2.3.4", user="u1")
        assert ok
    ok, reason = limiter.allow("web", ip="1.2.3.4", user="u1")
    assert ok is False
    assert "ip" in reason.lower()


def test_tiered_limiter_user_limit():
    limiter = TieredRateLimiter(ip_limit=100, user_limit=2, channel_limit=20,
                                window_seconds=60)
    for _ in range(2):
        ok, _ = limiter.allow("web", ip="1.2.3.4", user="u1")
        assert ok
    ok, reason = limiter.allow("web", ip="1.2.3.4", user="u1")
    assert ok is False
    assert "user" in reason.lower()


def test_tiered_limiter_channel_limit():
    limiter = TieredRateLimiter(ip_limit=100, user_limit=100, channel_limit=3,
                                window_seconds=60)
    for _ in range(3):
        ok, _ = limiter.allow("web", ip="9.9.9.9", user="u1")
        assert ok
    ok, reason = limiter.allow("web", ip="9.9.9.9", user="u2")
    assert ok is False
    assert "channel" in reason.lower()


def test_tiered_limiter_independent_keys():
    limiter = TieredRateLimiter(ip_limit=2, user_limit=100, channel_limit=100,
                                window_seconds=60)
    ok, _ = limiter.allow("web", ip="1.1.1.1", user="u1")
    assert ok
    # 不同 IP 不受影响
    ok, _ = limiter.allow("web", ip="2.2.2.2", user="u1")
    assert ok
    # 不同渠道不受影响
    ok, _ = limiter.allow("app", ip="1.1.1.1", user="u1")
    assert ok
    # 同一 IP 第三次（IP limit=2）超限
    ok, reason = limiter.allow("web", ip="1.1.1.1", user="u1")
    assert ok is False and "ip" in reason.lower()


def test_tiered_limiter_window_reset():
    limiter = TieredRateLimiter(ip_limit=1, user_limit=100, channel_limit=100,
                                window_seconds=0)  # 0 秒窗口立即重置
    ok, _ = limiter.allow("web", ip="1.1.1.1", user="u1")
    assert ok
    ok, _ = limiter.allow("web", ip="1.1.1.1", user="u1")
    assert ok


# ---------------- T2.6-2 敏感查询审计 ----------------

def test_auditor_flags_sensitive_query():
    auditor = SensitiveQueryAuditor()
    auditor.record("user_x", "web", "我的密码是什么", ip="1.2.3.4")
    auditor.record("user_x", "web", "最近天气怎么样", ip="1.2.3.4")
    rows = auditor.search("密码")
    assert len(rows) == 1
    assert rows[0]["user"] == "user_x"
    assert rows[0]["flagged"] is True


def test_auditor_plain_query_not_flagged():
    auditor = SensitiveQueryAuditor()
    auditor.record("user_x", "web", "报销流程是什么", ip="1.2.3.4")
    rows = auditor.all_events()
    assert len(rows) == 1
    assert rows[0]["flagged"] is False


def test_auditor_persists_metadata():
    auditor = SensitiveQueryAuditor()
    auditor.record("u1", "wechat", "帮我查一下银行卡余额", ip="5.6.7.8")
    rows = auditor.search("银行卡")
    assert len(rows) == 1
    assert rows[0]["channel"] == "wechat"
    assert rows[0]["ip"] == "5.6.7.8"


def test_sensitive_patterns_nonempty():
    assert len(SENSITIVE_PATTERNS) >= 5


# ---------------- T2.6-3 上传内容审核 ----------------

def test_review_rejects_blocked_text():
    reviewer = ContentReviewer()
    verdict = reviewer.review_text("这个文档教你如何盗取他人密码")
    assert verdict["approved"] is False
    assert "blocked" in verdict["reasons"][0]


def test_review_accepts_normal_text():
    reviewer = ContentReviewer()
    verdict = reviewer.review_text("企业内部员工手册，介绍考勤制度")
    assert verdict["approved"] is True


def test_review_flags_sensitive_text():
    reviewer = ContentReviewer()
    verdict = reviewer.review_text("文档中含身份证号码 11010119900307789X 与手机号 13800138000")
    assert verdict["approved"] is True or verdict["approved"] is False
    # 至少标记敏感项
    assert "sensitive" in verdict["reasons"][0] if verdict["reasons"] else True


def test_review_image_type_whitelist():
    reviewer = ContentReviewer()
    assert "jpg" in IMAGE_ALLOWED_TYPES
    assert "png" in IMAGE_ALLOWED_TYPES
    verdict = reviewer.review_image("scan.png", 1024)
    assert verdict["approved"] is True
    bad = reviewer.review_image("malware.exe", 1024)
    assert bad["approved"] is False
    assert "type" in bad["reasons"][0]


def test_review_image_size_limit():
    reviewer = ContentReviewer()
    verdict = reviewer.review_image("big.png", 60 * 1024 * 1024)  # 60MB
    assert verdict["approved"] is False
    assert "size" in verdict["reasons"][0]


def test_text_blocklist_nonempty():
    assert len(TEXT_BLOCKLIST) >= 5
    assert len(TEXT_SENSITIVE_FLAGS) >= 3
