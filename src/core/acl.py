"""检索链路 ACL / 租户隔离 辅助模块（P0-1）

设计要点（与 src/core/document_scanner.update_registry 保持一致）：
- chunk 级元数据携带 ACL 字段：doc_id / owner_user_id / allowed_roles / kb_id
- doc_id 采用 md5(相对路径) 生成，与 document_scanner 的文档注册 ID 同源，供权限表关联
- 查询侧：build_acl_filter 构造 Chroma where（检索前过滤）；assert_sources_allowed 做运行时归属断言（防绕过）
- 默认由 config.ACL_ENFORCE 控制关闭，不影响现有功能
"""
import hashlib

# 公共文档默认可见角色
PUBLIC_ALLOWED_ROLES = ("reader", "writer", "admin")


def compute_doc_id(source_file: str) -> str:
    """计算文档 ID：md5(相对路径)，与 document_scanner.update_registry 一致"""
    if not source_file:
        return ""
    return hashlib.md5(source_file.encode("utf-8")).hexdigest()


def enrich_acl_metadata(
    meta: dict,
    *,
    owner_user_id: str = "",
    kb_id: str = "",
) -> dict:
    """为 chunk metadata 补齐 ACL 字段（缺失才补，不覆盖已存在值）"""
    meta = dict(meta or {})
    source = meta.get("source_file") or meta.get("source") or ""
    if not meta.get("doc_id"):
        meta["doc_id"] = compute_doc_id(source)
    if not meta.get("owner_user_id"):
        meta["owner_user_id"] = owner_user_id
    if not meta.get("allowed_roles"):
        meta["allowed_roles"] = ",".join(PUBLIC_ALLOWED_ROLES)
    if not meta.get("kb_id"):
        meta["kb_id"] = kb_id
    return meta


def build_acl_filter(
    readable_ids: list[str] | None,
    *,
    role: str | None = None,
    admin_roles: tuple[str, ...] = ("admin",),
) -> dict | None:
    """构造检索前过滤条件（Chroma where）

    - role 命中 admin_roles 或 readable_ids is None（未配置权限）→ 返回 None（不过滤，豁免）
    - readable_ids 为空列表（用户无任何可读文档）→ 恒不匹配过滤，检索结果必为空
    - 否则 → {"doc_id": {"$in": readable_ids}}
    """
    if role and role in admin_roles:
        return None
    if readable_ids is None:
        return None
    if not readable_ids:
        return {"doc_id": {"$eq": "__no_access__"}}
    return {"doc_id": {"$in": readable_ids}}


def allowed_doc_ids_from_filter(acl_filter: dict | None) -> set[str] | None:
    """从 acl_filter 提取允许的 doc_id 集合（用于运行时断言）；None 表示不校验"""
    if not acl_filter or not isinstance(acl_filter, dict):
        return None
    cond = acl_filter.get("doc_id")
    if not isinstance(cond, dict):
        return None
    if "$in" in cond and isinstance(cond["$in"], list):
        return set(cond["$in"])
    if "$eq" in cond and isinstance(cond["$eq"], str):
        return {cond["$eq"]}
    return None


def assert_sources_allowed(sources: list[dict], allowed_ids: set[str] | None) -> tuple[list[dict], int]:
    """运行时归属断言：剔除 doc_id 不在允许集合内的越权来源

    - allowed_ids is None → 不校验，原样返回 (sources, 0)
    - 返回 (过滤后的 sources, 剔除条数)
    """
    if allowed_ids is None:
        return sources, 0
    kept: list[dict] = []
    removed = 0
    for s in sources:
        doc_id = (s.get("metadata") or {}).get("doc_id", "")
        if doc_id in allowed_ids:
            kept.append(s)
        else:
            removed += 1
    return kept, removed
