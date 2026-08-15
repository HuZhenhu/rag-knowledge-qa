"""FAISS 后端 — 支持 SQ8（int8 标量量化）压缩

与 ChromaBackend 同一套抽象接口，切换方式：VECTOR_STORE_BACKEND=faiss。

索引类型（FAISS_INDEX_TYPE）：
- hnsw-sq8（默认）：IndexHNSWSQ —— HNSW 图 + SQ8 量化，内存约为 float32 的 1/4
- flat        ：IndexFlatIP + IDMap —— 精确检索，数据量小时最快最准

余弦相似度：向量归一化 + 内积（METRIC_INNER_PRODUCT），与 Chroma 的 hnsw:space=cosine 对齐。
query 返回 distance = 1 - cos（范围 [0,2]，越小越相关），与现有 Retriever 的 score 逻辑兼容。

持久化：每个 collection 对应 FAISS_INDEX_DIR 下两个文件
- {name}.index : faiss 二进制索引
- {name}.json  : {dim, ids, documents, metadatas}

删除语义：
- flat 模式：IndexFlat 支持 remove_ids，真删除
- hnsw-sq8 模式：HNSW 不支持删除，触发索引重建（向量从 SQ 索引反量化，有微小精度损失）
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

import numpy as np

from src.config import (
    FAISS_HNSW_EF_CONSTRUCTION,
    FAISS_HNSW_EF_SEARCH,
    FAISS_HNSW_M,
    FAISS_INDEX_DIR,
    FAISS_INDEX_TYPE,
)
from src.core.vector_store import DEFAULT_COLLECTION, VectorStoreBackend

logger = logging.getLogger(__name__)


class FaissBackend(VectorStoreBackend):
    """FAISS 后端实现（内存索引 + 磁盘持久化）"""

    def __init__(self) -> None:
        try:
            import faiss
        except ImportError as exc:
            raise ImportError(
                "FAISS 后端需要 faiss-cpu，请先安装：pip install faiss-cpu"
            ) from exc
        self._faiss = faiss
        self._index_type = FAISS_INDEX_TYPE.lower()
        if self._index_type not in {"hnsw-sq8", "flat"}:
            raise ValueError(f"不支持的 FAISS_INDEX_TYPE: {self._index_type!r}（可选 hnsw-sq8 / flat）")

        FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
        self._dim: int | None = None
        self._lock = threading.Lock()
        # name -> {"ids": [str], "documents": [str], "metadatas": [dict|None],
        #          "index": faiss.Index, "next_id": int}
        self._collections: dict[str, dict[str, Any]] = {}
        self._load_all()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _paths(self, name: str) -> tuple[Path, Path]:
        return FAISS_INDEX_DIR / f"{name}.index", FAISS_INDEX_DIR / f"{name}.json"

    def _load_all(self) -> None:
        for idx_path in FAISS_INDEX_DIR.glob("*.index"):
            name = idx_path.stem
            meta_path = FAISS_INDEX_DIR / f"{name}.json"
            if not meta_path.exists():
                continue
            try:
                index = self._faiss.read_index(str(idx_path))
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                n = len(meta["ids"])
                self._collections[name] = {
                    "ids": meta["ids"],
                    "documents": meta["documents"],
                    "metadatas": meta.get("metadatas", [None] * n),
                    "index": index,
                    "next_id": n,
                }
                self._dim = int(meta.get("dim", 0)) or self._dim
                logger.info("FAISS 加载 collection=%s (%d 条)", name, n)
            except Exception as exc:  # 单个 collection 损坏不阻塞启动
                logger.warning("FAISS 加载 collection=%s 失败: %s", name, exc)

    def _save(self, name: str, col: dict[str, Any]) -> None:
        idx_path, meta_path = self._paths(name)
        self._faiss.write_index(col["index"], str(idx_path))
        meta_path.write_text(
            json.dumps(
                {
                    "dim": self._dim,
                    "ids": col["ids"],
                    "documents": col["documents"],
                    "metadatas": col["metadatas"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _create_index(self, dim: int) -> Any:
        faiss = self._faiss
        if self._index_type == "flat":
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
        else:
            index = faiss.IndexHNSWSQ(dim, FAISS_HNSW_M, faiss.ScalarQuantizer.QT_8bit)
            index.hnsw.efConstruction = FAISS_HNSW_EF_CONSTRUCTION
            index.hnsw.efSearch = FAISS_HNSW_EF_SEARCH
        return index

    def _get_collection(self, name: str, dim: int | None = None) -> dict[str, Any]:
        """获取或创建 collection（返回内部 dict）"""
        col = self._collections.get(name)
        if col is None:
            if dim is None:
                raise ValueError(f"collection {name!r} 不存在，且未提供维度")
            if self._dim is None:
                self._dim = dim
            elif self._dim != dim:
                raise ValueError(f"向量维度不一致：已有 {self._dim}，收到 {dim}")
            col = {
                "ids": [],
                "documents": [],
                "metadatas": [],
                "index": self._create_index(dim),
                "next_id": 0,
            }
            self._collections[name] = col
        return col

    def _rebuild(self, name: str, col: dict[str, Any], keep: list[int]) -> None:
        """hnsw-sq8 删除时重建索引（向量经 SQ 反量化，精度略有损失）"""
        faiss = self._faiss
        dim = self._dim
        vectors = np.vstack([col["index"].reconstruct(int(i)) for i in keep]).astype(np.float32)
        new_index = self._create_index(int(dim))
        faiss.normalize_L2(vectors)
        new_index.add_with_ids(vectors, np.arange(len(keep), dtype=np.int64))
        col["index"] = new_index
        col["ids"] = [col["ids"][i] for i in keep]
        col["documents"] = [col["documents"][i] for i in keep]
        col["metadatas"] = [col["metadatas"][i] for i in keep]
        col["next_id"] = len(keep)
        self._save(name, col)

    # ------------------------------------------------------------------
    # 抽象接口实现
    # ------------------------------------------------------------------

    def add(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict] | None = None,
        collection_name: str | None = None,
    ) -> None:
        name = collection_name or DEFAULT_COLLECTION
        if not embeddings:
            return
        with self._lock:
            col = self._get_collection(name, dim=len(embeddings[0]))
            vectors = np.asarray(embeddings, dtype=np.float32)
            self._faiss.normalize_L2(vectors)
            start = col["next_id"]
            ids_arr = np.arange(start, start + len(vectors), dtype=np.int64)
            col["index"].add_with_ids(vectors, ids_arr)
            col["ids"].extend(ids)
            col["documents"].extend(documents)
            col["metadatas"].extend(metadatas if metadatas else [None] * len(ids))
            col["next_id"] += len(vectors)
            self._save(name, col)

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
        collection_name: str | None = None,
    ) -> dict:
        name = collection_name or DEFAULT_COLLECTION
        col = self._collections.get(name)
        if col is None or col["index"].ntotal == 0:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}

        q = np.asarray([query_embedding], dtype=np.float32)
        self._faiss.normalize_L2(q)
        n = min(int(n_results), col["index"].ntotal)
        # IP 分数（归一化后）即余弦相似度；转为 1-cos 距离，与 Retriever 的 score 兼容
        scores, indices = col["index"].search(q, n)

        docs: list[str] = []
        metas: list[Any] = []
        dists: list[float] = []
        out_ids: list[str] = []
        for pos, internal in enumerate(indices[0]):
            if internal == -1:
                continue
            i = int(internal)
            docs.append(col["documents"][i])
            metas.append(col["metadatas"][i])
            dists.append(float(1.0 - scores[0][pos]))
            out_ids.append(col["ids"][i])
        return {
            "documents": [docs],
            "metadatas": [metas],
            "distances": [dists],
            "ids": [out_ids],
        }

    def delete(self, ids: list[str], collection_name: str | None = None) -> None:
        name = collection_name or DEFAULT_COLLECTION
        col = self._collections.get(name)
        if col is None or not ids:
            return
        to_delete = set(ids)
        with self._lock:
            keep = [i for i, eid in enumerate(col["ids"]) if eid not in to_delete]
            if len(keep) == len(col["ids"]):
                return
            if self._index_type == "flat":
                # IndexFlat/IDMap 支持 remove_ids 真删除
                removed = [int(i) for i in range(len(col["ids"])) if i not in keep]
                if removed:
                    col["index"].remove_ids(np.asarray(removed, dtype=np.int64))
                col["ids"] = [col["ids"][i] for i in keep]
                col["documents"] = [col["documents"][i] for i in keep]
                col["metadatas"] = [col["metadatas"][i] for i in keep]
                col["next_id"] = len(keep)
                self._save(name, col)
            else:
                self._rebuild(name, col, keep)

    def count(self, collection_name: str | None = None) -> int:
        name = collection_name or DEFAULT_COLLECTION
        col = self._collections.get(name)
        return len(col["ids"]) if col else 0

    def get_all(self, collection_name: str | None = None) -> dict:
        name = collection_name or DEFAULT_COLLECTION
        col = self._collections.get(name)
        if col is None:
            return {"ids": [], "documents": [], "metadatas": []}
        return {
            "ids": list(col["ids"]),
            "documents": list(col["documents"]),
            "metadatas": [dict(m) if m else {} for m in col["metadatas"]],
        }

    def list_collections(self) -> list[str]:
        return sorted(self._collections.keys())

    def delete_collection(self, collection_name: str) -> None:
        name = collection_name or DEFAULT_COLLECTION
        with self._lock:
            self._collections.pop(name, None)
            idx_path, meta_path = self._paths(name)
            for p in (idx_path, meta_path):
                if p.exists():
                    try:
                        p.unlink()
                    except OSError as exc:
                        logger.warning("删除 %s 失败: %s", p, exc)

