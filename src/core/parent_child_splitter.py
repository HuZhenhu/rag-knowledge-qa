"""父子切片器（Parent-Child Chunking, P1-2）

设计目标（RAG 检索质量优化方案 P1-2）：
- child：100~200 tokens 的细粒度检索单元，用于向量/BM25 检索，提高命中精度；
- parent：600~800 tokens 的生成单元，命中 child 后回取其所属 parent 整块送入 LLM，
  保证生成阶段上下文完整（保留段落语义、覆盖 child 之外的相关内容）。

切分策略：
- parent 构建：按段落(\\n\\n)聚合，逐段累计 token 数，达到 PARENT_TOKEN_SIZE 上限即封块；
  允许单段超长时直接独占一个 parent。
- child 构建：在 parent 内按句子边界(。！？；以及换行)切分，累计到 CHILD_TOKEN_SIZE 附近断块；
  单句超长时按字符兜底拆分。
- token 估算：使用 tiktoken cl100k_base（近似中英混合文本，token 数 = 字符数 * 0.65 量级），
  仅供切分决策使用，不参与向量化。

输出：child chunks，每个 child 的 metadata 携带 parent_content / parent_index，
供检索后回取父块送 LLM。
"""
import re
from dataclasses import dataclass, field
from typing import Any

from src.config import CHILD_TOKEN_SIZE, PARENT_TOKEN_SIZE

# 句子边界分隔符（中文为主，兼容英文）
_SENT_SEPARATORS = re.compile(r"(?<=[。！？；!?;])\s*|\n+")

# 段落边界
_PARAGRAPH_SEP = re.compile(r"\n\s*\n")


def _estimate_tokens(text: str) -> int:
    """估算 token 数：优先用真实 tiktoken（cl100k_base），失败时回退到经验值

    重建索引后已用真实 tokenizer 校准：tiktoken 对中英混合文本的计数
    与模型实际切分更接近，保证 child/parent 块大小贴合 CHILD_TOKEN_SIZE /
    PARENT_TOKEN_SIZE 设计值。
    """
    if not text:
        return 0
    try:
        return _count_tiktoken(text)
    except Exception:
        # 回退：经验估算（中文约 1.2 字符/token，英文约 4 字符/token，混排折中 2.2）
        return max(1, int(len(text) / 2.2))


_tiktoken_enc = None


def _count_tiktoken(text: str) -> int:
    """使用 tiktoken cl100k_base 精确计数（模块级惰性加载编码器）"""
    global _tiktoken_enc
    if _tiktoken_enc is None:
        import tiktoken
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
    return len(_tiktoken_enc.encode(text))


@dataclass
class Chunk:
    """切片结果（与 src/core/splitter.Chunk 保持同构）"""
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ParentChildSplitter:
    """父子切片器：长文本 -> child chunks（携带 parent 上下文）"""

    def __init__(self, child_token_size: int = CHILD_TOKEN_SIZE,
                 parent_token_size: int = PARENT_TOKEN_SIZE):
        self.child_token_size = child_token_size
        self.parent_token_size = parent_token_size

    # ---------- 对外入口 ----------
    def split(self, content: str, metadata: dict[str, Any] | None = None) -> list[Chunk]:
        """切分一段长文本为 child chunks（parent 上下文写入每个 child 的 metadata）

        Args:
            content: 源文档文本（可能已按文档结构预分段，此处按段落再聚合）
            metadata: 源文档元数据（source_file 等），逐 child 透传

        Returns:
            list[Chunk]: child chunks，metadata 含 parent_content / parent_index / child_index
        """
        base_meta = dict(metadata or {})
        parents = self._build_parents(content)
        chunks: list[Chunk] = []
        for pi, parent in enumerate(parents):
            child_parts = self._split_children(parent)
            for ci, child in enumerate(child_parts):
                meta = {
                    **base_meta,
                    "content_type": "parent_child_child",
                    "parent_index": pi,
                    "child_index": ci,
                    "parent_content": parent,  # 检索命中后回取整块父文本
                }
                chunks.append(Chunk(content=child, metadata=meta))
        return chunks

    # ---------- parent 构建 ----------
    def _build_parents(self, content: str) -> list[str]:
        """按段落聚合为 parent 块（每块约 PARENT_TOKEN_SIZE，超长段落单独成块）"""
        content = content.strip()
        if not content:
            return []
        # 先按段聚合
        paragraphs = [p.strip() for p in _PARAGRAPH_SEP.split(content) if p.strip()]
        parents: list[str] = []
        buf: list[str] = []
        buf_tokens = 0
        for para in paragraphs:
            pt = _estimate_tokens(para)
            # 单段超长（> 1.5 * parent 上限）：先把已缓冲段落封块，再让该段独占
            if pt > int(self.parent_token_size * 1.5):
                if buf:
                    parents.append("\n".join(buf))
                    buf, buf_tokens = [], 0
                parents.append(para)
                continue
            if buf and buf_tokens + pt > self.parent_token_size:
                parents.append("\n".join(buf))
                buf, buf_tokens = [], 0
            buf.append(para)
            buf_tokens += pt
        if buf:
            parents.append("\n".join(buf))
        return parents

    # ---------- child 构建 ----------
    def _split_children(self, parent: str) -> list[str]:
        """在 parent 内按句子切分为 child（每块约 CHILD_TOKEN_SIZE）"""
        # 按句子/换行切
        pieces = [p.strip() for p in _SENT_SEPARATORS.split(parent) if p.strip()]
        if not pieces:
            pieces = [parent]
        children: list[str] = []
        buf: list[str] = []
        buf_tokens = 0
        for piece in pieces:
            pt = _estimate_tokens(piece)
            # 单句超长：先封缓冲，再按字符硬切
            if pt > self.child_token_size * 2:
                if buf:
                    children.append("".join(buf))
                    buf, buf_tokens = [], 0
                children.extend(self._hard_split(piece))
                continue
            if buf and buf_tokens + pt > self.child_token_size:
                children.append("".join(buf))
                buf, buf_tokens = [], 0
            buf.append(piece)
            buf_tokens += pt
        if buf:
            children.append("".join(buf))
        # 兜底：避免出现空 child
        return [c for c in children if c.strip()]

    @staticmethod
    def _hard_split(text: str, char_size: int = 500) -> list[str]:
        """超长无标点文本按字符硬切"""
        out = []
        for i in range(0, len(text), char_size):
            out.append(text[i:i + char_size])
        return out
