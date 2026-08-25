"""P2-7 引用 span 高亮与真实性校验

职责：
1. build_context_with_citations: 将检索到的 sources 格式化为带 [cit:N] 编号的 LLM 上下文，
   并返回「编号 -> 来源」索引（cit_index）供后续引用校验使用。
2. parse_citations: 从模型答案中解析 [cit:N] / [cit:N,M] 引用标注，返回带字符偏移的引用项。
3. validate_citations: 校验引用编号是否对应上下文真实提供的来源（防幻觉引用）；
   若给定真实 chunk_id 集（source_ids），编号对应来源的 chunk_id 也必须命中才视为有效。
4. build_citation_spans: 组装结构化 span 列表（含起止位置、来源定位、有效性标记），供前端高亮。

设计说明：真实检索 ID 集来自 sources 中 metadata.chunk_id（父子回取后父块 id 优先）。
当检索链路未注入 chunk_id（如向量检索相似度搜索不返回 id）时，source_ids 为空，
校验退化为「编号必须存在于 cit_index」——即引用必须是上下文实际提供的来源之一，
仍可拦截模型编造不存在的来源编号，实现防幻觉引用的核心目标。
"""
import re

_CIT_RE = re.compile(r"\[cit:(\d+(?:\s*,\s*\d+)*)\]", re.IGNORECASE)


def build_context_with_citations(sources: list[dict]) -> tuple[str, dict]:
    """将 sources 格式化为带 [cit:N] 编号的上下文，返回 (context, cit_index)。

    cit_index: {str(编号): source_dict}，供 parse/validate 使用。
    """
    blocks: list[str] = []
    cit_index: dict[str, dict] = {}
    for i, s in enumerate(sources, start=1):
        meta = s.get("metadata", {}) or {}
        source_file = meta.get("source_file", "") or meta.get("source", "未知来源")
        loc = _locate(meta)
        header = f"[cit:{i}: {source_file}{loc}]"
        blocks.append(f"{header}\n{s.get('content', '')}")
        cit_index[str(i)] = s
    return "\n\n".join(blocks), cit_index


def _locate(meta: dict) -> str:
    """从 metadata 生成定位描述（章节/页码）"""
    parts = []
    if meta.get("section"):
        parts.append(f"第{meta['section']}节")
    if meta.get("page_number") is not None:
        parts.append(f"第{meta['page_number']}页")
    return "，" + "，".join(parts) if parts else ""


def parse_citations(answer: str) -> list[tuple[str, int, int]]:
    """解析答案中的引用标注，返回 [(citation_id, start, end)]（字符偏移）。

    支持 [cit:1] 与 [cit:1,2] 多编号展开为多个 span。
    """
    found: list[tuple[str, int, int]] = []
    for m in _CIT_RE.finditer(answer):
        ids = [p.strip() for p in m.group(1).split(",") if p.strip()]
        for cid in ids:
            found.append((cid, m.start(), m.end()))
    return found


def validate_citations(cited: list[tuple[str, int, int]], cit_index: dict,
                       source_ids: set[str] | None = None) -> dict[str, bool]:
    """校验引用编号真实性，返回 {citation_id: valid}。

    - 编号不在 cit_index（上下文未提供该来源）→ 幻觉引用，valid=False；
    - 提供 source_ids（真实 doc_id 集）且非空时，编号对应来源的 doc_id 不在集内 → 无效；
    - source_ids 为空或未提供 → 退化为编号存在性校验。
    """
    valid_map: dict[str, bool] = {}
    for cid, _start, _end in cited:
        if cid in valid_map:
            continue
        src = cit_index.get(cid)
        ok = src is not None
        if ok and source_ids:
            sid = str((src.get("metadata", {}) or {}).get("doc_id", ""))
            if sid and sid not in source_ids:
                ok = False
        valid_map[cid] = ok
    return valid_map


def build_citation_spans(answer: str, cit_index: dict, valid: dict[str, bool]) -> list[dict]:
    """组装结构化引用 span 列表。

    每条 span: {citation_id, start, end, text, source_file, doc_id, section,
                page_number, content_type, valid}
    """
    spans: list[dict] = []
    for cid, start, end in parse_citations(answer):
        src = cit_index.get(cid)
        meta = (src.get("metadata", {}) or {}) if src else {}
        spans.append({
            "citation_id": cid,
            "start": start,
            "end": end,
            "text": answer[start:end],
            "source_file": meta.get("source_file", "") or meta.get("source", ""),
            "doc_id": meta.get("doc_id", ""),
            "section": meta.get("section", ""),
            "page_number": meta.get("page_number"),
            "content_type": meta.get("content_type", ""),
            "valid": valid.get(cid, False),
        })
    return spans
