"""T3.2 缓存策略深化 — 前缀缓存 / 按领域阈值 / 热门问题预热 / 淘汰策略。

内容：
- 语义缓存阈值按领域调优（可配置）：domain 命中其专属阈值，未配置回退全局阈值
- 前缀缓存：相同规范化前缀的相近问法放宽命中（客服高频场景提升命中率）
- 热门问题预热：warmup 批量写入，后续直接命中
- 缓存淘汰：TTL 过期清理 + LRU 超上限淘汰
- 向后兼容：原有 get(query, acl_fp, embed_query) 签名行为不变
"""
import math
import time

import pytest

from src.config import (
    SEMANTIC_CACHE_PREFIX_ENABLED,
    SEMANTIC_CACHE_TTL_SECONDS,
    SEMANTIC_CACHE_MAX_ENTRIES,
    SEMANTIC_CACHE_DOMAIN_THRESHOLDS,
)
from src.core.cache_policy import (
    normalize_prefix,
    prefix_matches,
    resolve_threshold,
    warmup_parse,
    expired_entry,
    pick_lru_keys,
)
from src.core.semantic_cache import SemanticCache


# ---- 测试用确定性伪 embed：query->向量，余弦可精确控制 -------------------
V_Q_SHORT = [1.0, 0.0]          # "如何办理离职？"
V_Q_SIM090 = [0.9, math.sqrt(1 - 0.9 * 0.9)]   # 与 V_Q_SHORT 余弦≈0.90
V_Q_SIM088 = [0.88, math.sqrt(1 - 0.88 * 0.88)]  # 与 V_Q_SHORT 余弦≈0.88
V_ORTH = [0.0, 1.0]             # 正交，余弦≈0.0


class FakeEmbed:
    """query -> 固定向量（余弦可精确控制）"""

    def __init__(self, table):
        self.table = table

    def __call__(self, query):
        return list(self.table.get(query, V_ORTH))


@pytest.fixture()
def cache(tmp_path):
    db = tmp_path / "sem_cache.db"
    return SemanticCache(db_path=db)


def _sim(a, b):
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


# ============ 1. 按领域阈值（可配置） ============
def test_domain_threshold_resolution():
    d = {"faq": 0.88, "tech": 0.94}
    assert resolve_threshold("faq", 0.92, d) == 0.88
    assert resolve_threshold("tech", 0.92, d) == 0.94
    # 未配置领域 -> 全局默认
    assert resolve_threshold("general", 0.92, d) == 0.92
    assert resolve_threshold("none", 0.92, d) == 0.92


def test_domain_config_loaded_from_env():
    assert isinstance(SEMANTIC_CACHE_DOMAIN_THRESHOLDS, dict)


def test_domain_threshold_enables_hit_when_global_misses(tmp_path):
    db = tmp_path / "d.db"
    sc = SemanticCache(db_path=db, domain_thresholds={"faq": 0.88})
    q_hit = "公司年假政策是什么？"
    q_new = "年假一共有多少天？"
    embed = FakeEmbed({q_hit: V_Q_SHORT, q_new: V_Q_SIM090})
    sc.set(q_hit, "年假15天", [], "none", embed)
    # 全局 0.92 不命中（0.90 < 0.92）
    hit = sc.get(q_new, "none", embed)
    assert hit is None
    # faq 领域 0.88 -> 命中（0.90 >= 0.88）
    hit_faq = sc.get(q_new, "none", embed, domain="faq")
    assert hit_faq is not None
    assert hit_faq[0] == "年假15天"
    assert hit_faq[2] >= 0.89  # sim≈0.90


# ============ 2. 前缀缓存 ============
def test_prefix_normalization():
    assert prefix_matches("如何办理离职？", "如何办理离职流程")
    assert prefix_matches("如何办理离职流程", "如何办理离职？")
    assert normalize_prefix("  打卡规则  ") == normalize_prefix("打卡规则")
    assert normalize_prefix("A-B!")


def test_prefix_enabled_boosts_near_variants(tmp_path):
    db = tmp_path / "p.db"
    sc = SemanticCache(db_path=db, domain_thresholds={}, prefix_enabled=True)
    q_hist = "如何办理离职？"
    q_var = "如何办理离职流程？"
    embed = FakeEmbed({q_hist: V_Q_SHORT, q_var: V_Q_SIM090})
    sc.set(q_hist, "联系HR办理", [], "none", embed)
    # 全局 0.92 纯语义不命中
    assert _sim(V_Q_SHORT, V_Q_SIM090) < 0.92
    # 前缀相同 -> 放宽阈值命中（替代问法共享答案）
    hit = sc.get(q_var, "none", embed)
    assert hit is not None
    assert hit[0] == "联系HR办理"
    assert hit[2] >= 0.86


def test_prefix_disabled_keeps_old_behavior(tmp_path):
    db = tmp_path / "p2.db"
    sc = SemanticCache(db_path=db, prefix_enabled=False)
    q_hist = "如何办理离职？"
    q_var = "如何办理离职流程？"
    embed = FakeEmbed({q_hist: V_Q_SHORT, q_var: V_Q_SIM090})
    sc.set(q_hist, "联系HR办理", [], "none", embed)
    assert sc.get(q_var, "none", embed) is None  # 0.90 < 0.92 不命中


def test_prefix_no_false_positive_across_families(tmp_path):
    """不同前缀族不互相放宽命中"""
    db = tmp_path / "p3.db"
    sc = SemanticCache(db_path=db, prefix_enabled=True)
    q_hist = "如何办理离职？"
    q_other = "工资什么时候发？"
    embed = FakeEmbed({q_hist: V_Q_SHORT, q_other: V_ORTH})
    sc.set(q_hist, "联系HR", [], "none", embed)
    assert normalize_prefix(q_hist) != normalize_prefix(q_other)
    assert sc.get(q_other, "none", embed) is None


def test_prefix_config_loaded():
    assert isinstance(SEMANTIC_CACHE_PREFIX_ENABLED, bool)


# ============ 3. 热门问题预热 ============
def test_warmup_parse_simple_strings():
    items = warmup_parse(["如何办理离职？", "打卡规则是什么？"])
    assert len(items) == 2
    assert items[0][0] == "如何办理离职？"
    assert items[0][1] == ""


def test_warmup_parse_dict_with_answer():
    items = warmup_parse([
        {"q": "如何办理离职？", "a": "联系HR办理", "sources": ["hr.md"]},
    ])
    assert items[0] == ("如何办理离职？", "联系HR办理", ["hr.md"])


def test_warmup_populates_cache(tmp_path):
    db = tmp_path / "w.db"
    sc = SemanticCache(db_path=db)
    embed = FakeEmbed({"如何办理离职？": V_Q_SHORT})
    n = sc.warmup([("如何办理离职？", "联系HR办理", ["hr.md"])], embed)
    assert n == 1
    assert sc.size == 1
    hit = sc.get("如何办理离职？", "none", embed)
    assert hit is not None
    assert hit[0] == "联系HR办理"
    assert hit[1] == ["hr.md"]


def test_warmup_with_plain_str(tmp_path):
    db = tmp_path / "w2.db"
    sc = SemanticCache(db_path=db)
    embed = FakeEmbed({"打卡规则是什么？": V_Q_SHORT})
    n = sc.warmup(["打卡规则是什么？"], embed)
    assert n == 1  # 纯字符串：answer 空仍写入（占位去重由上层保证）


# ============ 4. 淘汰策略：TTL + LRU ============
def test_expired_entry_judgement():
    now = time.time()
    entry = {"created_at": now - 100, "last_access_ts": now - 100, "access_count": 1}
    assert expired_entry(entry, now, ttl_seconds=50) is True
    assert expired_entry(entry, now, ttl_seconds=200) is False
    # ttl<=0 视为不过期
    assert expired_entry(entry, now, ttl_seconds=0) is False


def test_lru_pick_prefers_least_recently_used():
    entries = {
        "a": {"last_access_ts": 10.0},  # 最旧
        "b": {"last_access_ts": 30.0},
        "c": {"last_access_ts": 20.0},
    }
    picked = pick_lru_keys(entries, k=2)
    assert sorted(picked) == ["a", "c"]


def test_ttl_eviction_removes_stale(tmp_path):
    db = tmp_path / "e.db"
    sc = SemanticCache(db_path=db, ttl_seconds=100)
    embed = FakeEmbed({"a": V_Q_SHORT, "b": V_ORTH})
    sc.set("a", "A", [], "none", embed)
    sc.set("b", "B", [], "none", embed)
    assert sc.size == 2
    # 手工把 a 标记为过期（created_at 很久以前）
    removed = sc.evict(ttl_seconds=1, now=time.time() + 500)
    assert removed >= 1


def test_max_entries_lru_eviction(tmp_path):
    db = tmp_path / "e2.db"
    sc = SemanticCache(db_path=db, max_entries=2)
    embed = FakeEmbed({"q1": V_Q_SHORT, "q2": [0.5, math.sqrt(0.75)], "q3": V_ORTH})
    sc.set("q1", "1", [], "none", embed)
    sc.set("q2", "2", [], "none", embed)
    sc.set("q3", "3", [], "none", embed)
    assert sc.size <= 2  # 超上限自动淘汰
    b = sc.evict(max_entries=2)
    assert sc.size <= 2


def test_access_count_increments_on_hit(tmp_path):
    db = tmp_path / "c.db"
    sc = SemanticCache(db_path=db)
    embed = FakeEmbed({"q1": V_Q_SHORT})
    sc.set("q1", "答", [], "none", embed)
    sc.get("q1", "none", embed)
    stats = sc.stats("none")
    assert stats["hits"] >= 1
    assert stats["access_count"] >= 1


# ============ 5. 向后兼容 ============
def test_legacy_signature_unchanged(tmp_path):
    db = tmp_path / "leg.db"
    sc = SemanticCache(db_path=db)
    embed = FakeEmbed({"q1": V_Q_SHORT, "q2": V_Q_SHORT})
    sc.set("q1", "答", ["s.md"], "fp1", embed)
    hit = sc.get("q2", "fp1", embed)  # 旧签名无 domain
    assert hit is not None
    assert hit[0] == "答"
    assert hit[2] == pytest.approx(1.0, abs=1e-3)


def test_config_booleans_valid():
    assert isinstance(SEMANTIC_CACHE_TTL_SECONDS, int)
    assert isinstance(SEMANTIC_CACHE_MAX_ENTRIES, int)
    assert SEMANTIC_CACHE_MAX_ENTRIES > 0
