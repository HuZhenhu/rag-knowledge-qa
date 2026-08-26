"""Milvus 后端（pymilvus）

与 ChromaBackend 同一套抽象接口，切换方式：VECTOR_STORE_BACKEND=milvus。

- URI（MILVUS_URI）：支持 Milvus Lite 本地文件（如 ./milvus_lite.db，需 pip install milvus-lite）
  或远程 Milvus（如 http://localhost:19530）
- 每个知识库对应一个 collection：
  id (int64 主键) + embedding (float_vector) + document (varchar) + metadata (json)
- 索引（MILVUS_INDEX_TYPE）：HNSW（默认）/ IVF_FLAT
- 量化（MILVUS_QUANTIZER，Milvus 2.4+）：空 / SCALAR / QUANTIZE_BIT，SCALAR 即 SQ8 风格
- 查询：COSINE 度量，返回 distance = 1 - cos（与现有 Retriever 的 score 逻辑兼容）

外部 chunk_id（如 md5）存放在 metadata["chunk_id"]，主键用自增 int64。
"""
from __future__ import annotations

import logging
from typing import Any

from src.config import (
    MILVUS_CONSISTENCY_LEVEL,
    MILVUS_CONNECT_RETRIES,
    MILVUS_INDEX_NLIST,
    MILVUS_INDEX_NPROBE,
    MILVUS_INDEX_TYPE,
    MILVUS_METRIC_TYPE,
    MILVUS_PASSWORD,
    MILVUS_QUANTIZER,
    MILVUS_SECURE,
    MILVUS_TIMEOUT_SECONDS,
    MILVUS_URI,
    MILVUS_USER,
)
from src.core.vector_store import DEFAULT_COLLECTION, VectorStoreBackend

logger = logging.getLogger(__name__)

_METRIC_MAP = {
    "cosine": "COSINE",
    "ip": "IP",
    "l2": "L2",
}


class MilvusBackend(VectorStoreBackend):
    """Milvus 后端实现"""

    def __init__(self) -> None:
        try:
            from pymilvus import connections, utility
        except ImportError as exc:
            raise ImportError(
                "Milvus 后端需要 pymilvus，请先安装：pip install pymilvus"
            ) from exc
        self._connections = connections
        self._utility = utility
        self._metric = _METRIC_MAP.get(MILVUS_METRIC_TYPE.lower(), "COSINE")
        self._consistency_level = MILVUS_CONSISTENCY_LEVEL
        self._dim: int | None = None
        self._connect_with_retry()
        logger.info("Milvus 已连接: %s（consistency=%s）", MILVUS_URI, self._consistency_level)

    def _connect_with_retry(self) -> None:
        """连接 Milvus，失败按 MILVUS_CONNECT_RETRIES 重试（生产集群冷启动慢）"""
        connect_kwargs: dict[str, Any] = {"alias": "default", "uri": str(MILVUS_URI)}
        if MILVUS_USER:
            connect_kwargs["user"] = MILVUS_USER
        if MILVUS_PASSWORD:
            connect_kwargs["password"] = MILVUS_PASSWORD
        if MILVUS_TIMEOUT_SECONDS > 0:
            connect_kwargs["timeout"] = MILVUS_TIMEOUT_SECONDS
        if MILVUS_SECURE:
            connect_kwargs["secure"] = True

        last_exc: Exception | None = None
        for attempt in range(1, max(MILVUS_CONNECT_RETRIES, 1) + 1):
            try:
                self._connections.connect(**connect_kwargs)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Milvus 连接失败（第 %d/%d 次，uri=%s）：%s",
                    attempt, max(MILVUS_CONNECT_RETRIES, 1), MILVUS_URI, exc,
                )
                if attempt < max(MILVUS_CONNECT_RETRIES, 1):
                    import time

                    time.sleep(min(0.5 * (2 ** (attempt - 1)), 5.0))
        raise RuntimeError(
            f"Milvus 连接失败（MILVUS_URI={MILVUS_URI}，重试 {MILVUS_CONNECT_RETRIES} 次）：{last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _ensure_collection(self, name: str, dim: int | None = None):
        """获取或创建 Milvus collection，返回 Collection 对象"""
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema

        if self._utility.has_collection(name, using="default"):
            col = Collection(name, using="default")
            if dim is not None and self._dim is None:
                self._dim = dim
            return col

        if dim is None:
            raise ValueError(f"collection {name!r} 不存在，且未提供维度")

        if self._dim is None:
            self._dim = dim
        elif self._dim != dim:
            raise ValueError(f"向量维度不一致：已有 {self._dim}，收到 {dim}")

        schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
                FieldSchema(name="document", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="metadata", dtype=DataType.JSON),
            ],
            description=name,
        )
        col = Collection(name, schema=schema, using="default")
        self._create_index(col)
        return col

    def _create_index(self, col) -> None:
        index_type = MILVUS_INDEX_TYPE.upper()
        params: dict[str, Any] = {"metric_type": self._metric}
        if index_type == "HNSW":
            params["M"] = 16
            params["efConstruction"] = 200
        elif index_type == "IVF_FLAT":
            params["nlist"] = MILVUS_INDEX_NLIST
        else:
            raise ValueError(f"不支持的 MILVUS_INDEX_TYPE: {MILVUS_INDEX_TYPE!r}（可选 HNSW / IVF_FLAT）")
        if MILVUS_QUANTIZER:
            params["quantizer_type"] = MILVUS_QUANTIZER.upper()
        col.create_index("embedding", {"index_type": index_type, "metric_type": self._metric, "params": params})
        logger.info("Milvus collection=%s 建索引: %s %s", col.name, index_type, params)

    def _search_params(self) -> dict:
        if MILVUS_INDEX_TYPE.upper() == "IVF_FLAT":
            return {"metric_type": self._metric, "params": {"nprobe": MILVUS_INDEX_NPROBE}}
        return {"metric_type": self._metric}

    def _get_next_id(self, col) -> int:
        """自增主键：取当前最大 id + 1"""
        try:
            res = col.query(expr="id >= 0", output_fields=["id"], limit=1, sort_by="id", desc=True)
            return int(res[0]["id"]) + 1 if res else 0
        except Exception:
            return 0

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
        col = self._ensure_collection(name, dim=len(embeddings[0]))
        start_id = self._get_next_id(col)
        rows = []
        for i, (chunk_id, doc, emb) in enumerate(zip(ids, documents, embeddings)):
            meta = dict(metadatas[i]) if metadatas and metadatas[i] else {}
            meta["chunk_id"] = chunk_id  # 外部 id 存进 metadata，主键用自增 int64
            rows.append([start_id + i, emb, doc, meta])
        col.insert(rows)
        col.flush()

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
        collection_name: str | None = None,
    ) -> dict:
        name = collection_name or DEFAULT_COLLECTION
        if not self._utility.has_collection(name, using="default"):
            return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}
        from pymilvus import Collection
        col = Collection(name, using="default")
        if col.num_entities == 0:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}
        col.load()
        try:
            res = col.search(
                data=[query_embedding],
                anns_field="embedding",
                param=self._search_params(),
                limit=int(n_results),
                output_fields=["document", "metadata"],
                consistency_level=self._consistency_level,
            )
        except Exception as exc:
            logger.warning("Milvus 检索失败: %s", exc)
            return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}

        docs: list[str] = []
        metas: list[Any] = []
        dists: list[float] = []
        out_ids: list[str] = []
        for hit in res[0]:
            entity = hit.entity
            meta = dict(entity.get("metadata") or {})
            docs.append(str(entity.get("document", "")))
            metas.append(meta)
            dists.append(float(hit.distance))  # COSINE 距离 = 1-cos，越小越相关
            out_ids.append(str(meta.get("chunk_id", "")))
        return {
            "documents": [docs],
            "metadatas": [metas],
            "distances": [dists],
            "ids": [out_ids],
        }

    def delete(self, ids: list[str], collection_name: str | None = None) -> None:
        name = collection_name or DEFAULT_COLLECTION
        if not ids or not self._utility.has_collection(name, using="default"):
            return
        from pymilvus import Collection
        col = Collection(name, using="default")
        chunk_ids = ",".join(f'"{cid}"' for cid in ids)
        expr = f'metadata["chunk_id"] in [{chunk_ids}]'
        col.delete(expr=expr)
        col.flush()

    def count(self, collection_name: str | None = None) -> int:
        name = collection_name or DEFAULT_COLLECTION
        if not self._utility.has_collection(name, using="default"):
            return 0
        from pymilvus import Collection
        return Collection(name, using="default").num_entities

    def get_all(self, collection_name: str | None = None) -> dict:
        name = collection_name or DEFAULT_COLLECTION
        if not self._utility.has_collection(name, using="default"):
            return {"ids": [], "documents": [], "metadatas": []}
        from pymilvus import Collection
        col = Collection(name, using="default")
        col.load()
        n = col.num_entities
        if n == 0:
            return {"ids": [], "documents": [], "metadatas": []}
        res = col.query(
            expr="id >= 0",
            output_fields=["id", "document", "metadata"],
            limit=max(n, 1),
        )
        ids, docs, metas = [], [], []
        for row in res:
            meta = dict(row.get("metadata") or {})
            ids.append(str(meta.get("chunk_id", "")))
            docs.append(str(row.get("document", "")))
            metas.append(meta)
        return {"ids": ids, "documents": docs, "metadatas": metas}

    def list_collections(self) -> list[str]:
        return sorted(self._utility.list_collections(using="default"))

    def delete_collection(self, collection_name: str) -> None:
        name = collection_name or DEFAULT_COLLECTION
        if self._utility.has_collection(name, using="default"):
            self._utility.drop_collection(name, using="default")

    # ------------------------------------------------------------------
    # collection 别名 — 索引重建与查询互不影响（零停机切换）
    # ------------------------------------------------------------------

    def create_alias(self, collection_name: str, alias: str) -> None:
        """为 collection 创建别名。

        生产流程：新 collection 建好索引后 create_alias 指向它，
        应用查询走 alias，重建期间旧 collection 查询不中断。
        """
        self._utility.create_alias(
            collection_name=collection_name, alias=alias, using="default"
        )

    def switch_alias(self, collection_name: str, alias: str) -> None:
        """将别名切换指向另一个 collection（索引重建完成后的原子切换）。"""
        self._utility.alter_alias(
            collection_name=collection_name, alias=alias, using="default"
        )
