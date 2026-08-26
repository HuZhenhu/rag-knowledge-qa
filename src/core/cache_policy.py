"""T3.2 缓存策略深化 — 前缀规范化 / 按领域阈值 / 热门问题预热 / 淘汰策略。

提供纯函数与工具，供 src/core/semantic_cache.py 集成：
- normalize_prefix：规范化查询文本为"前缀指纹"（去空白标点 → 小写 → 取前 width 字符）
- resolve_threshold：按领域解析命中阈值（未配置领域回退全局默认）
- warmup_parse：解析热门问题配置（纯字符串 或 {q,a,sources} dict）
- expired_entry / pick_lru_keys：TTL 过期判断与 LRU 淘汰选择
"""
from __future__ import annotations

import re
import time

# 空白 / 全半角标点 / 符号统一清洗（含 CJK 标点区 \u3000-\u303f 与全角符号区 \uff00-\uff5f）
_NON_ALNUM_RE = re.compile(
    r"[\s\u3000-\u303f\uff00-\uff5f.,!?;:()\[\]{}\"'`~@#$%^&*_+=<>/\\|-]+"
)


def normalize_prefix(text: str, width: int = 16) -> str:
    """规范化查询为前缀指纹。

    去空白/标点 → 统一小写 → 取前 width 字符。
    相同前缀指纹代表高度相近的"同族问法"，用于前缀缓存放宽命中。
    """
    cleaned = _NON_ALNUM_RE.sub("", (text or "").strip()).lower()
    return cleaned[:max(1, int(width))] if cleaned else ""


def prefix_matches(text_a: str, text_b: str, width: int = 16) -> bool:
    """前缀家族判定：两个文本的规范化前缀互为前缀（或一致）。

    "如何办理离职？"(如何办理离职) 与 "如何办理离职流程？"(如何办理离职流程) 判定 True；
    不同族（如"如何办理离职" vs "工资什么时候发"）判定 False。
    注意：本判定仅用于放宽语义阈值，最终仍由余弦相似度把关，避免误命中。
    """
    pa = normalize_prefix(text_a, width)
    pb = normalize_prefix(text_b, width)
    if not pa or not pb:
        return False
    return pa == pb or pa.startswith(pb) or pb.startswith(pa)


def resolve_threshold(domain: str | None, default_threshold: float,
                      domain_thresholds: dict[str, float] | None = None) -> float:
    """按领域解析命中阈值。

    Args:
        domain: 领域名（如 "faq" / "tech" / "general"）；None 或未配置时回退 default_threshold。
        default_threshold: 全局默认阈值（SEMANTIC_CACHE_THRESHOLD）。
        domain_thresholds: 领域->阈值映射（可配置）。
    """
    if domain and domain_thresholds:
        t = domain_thresholds.get(domain)
        if t is not None:
            return float(t)
    return float(default_threshold)


def warmup_parse(hot: list) -> list[tuple[str, str, list]]:
    """解析热门问题配置为 (query, answer, sources) 三元组列表。

    支持三种输入形态：
    - 纯字符串：["如何办理离职？", ...] → (q, "", [])
    - dict：{"q": ..., "a": ..., "sources": [...]}
    - 三元组/列表：(q, a, sources)
    """
    out: list[tuple[str, str, list]] = []
    for item in hot or []:
        if isinstance(item, str):
            out.append((item, "", []))
        elif isinstance(item, dict):
            out.append((str(item.get("q", "")), str(item.get("a", "")),
                        list(item.get("sources") or [])))
        elif isinstance(item, (list, tuple)) and item:
            q = str(item[0])
            a = str(item[1]) if len(item) > 1 and item[1] is not None else ""
            srcs = list(item[2]) if len(item) > 2 and item[2] else []
            out.append((q, a, srcs))
    return out


def expired_entry(entry: dict, now: float, ttl_seconds: int) -> bool:
    """TTL 过期判断。ttl<=0 表示永不过期。缺少时间戳视为未过期（兼容旧条目）。"""
    if ttl_seconds <= 0:
        return False
    created = entry.get("created_at")
    if created is None:
        return False
    return (now - float(created)) > ttl_seconds


def pick_lru_keys(entries: dict[str, dict], k: int) -> list[str]:
    """从 entries 中选出最久未访问（last_access_ts 或 created_at 最小）的 k 个 key。

    用于超上限 LRU 淘汰；entries 为空或 k<=0 返回空列表。
    """
    if k <= 0 or not entries:
        return []
    ranked = sorted(
        entries.items(),
        key=lambda kv: float(kv[1].get("last_access_ts") or kv[1].get("created_at") or 0.0),
    )
    # 确保不返回超过现有数量
    return [key for key, _ in ranked[: max(0, min(k, len(entries)))]]


def touch_updates(hit: bool, now: float | None = None) -> dict:
    """统计访问元数据（用于命中率观测与 LRU）。返回增量字段。"""
    now = now if now is not None else time.time()
    if hit:
        return {"access_count": 1, "last_access_ts": now}
    return {"last_access_ts": now}
