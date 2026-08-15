"""向量数据库抽象层 — 支持多后端切换（ChromaDB/Milvus/FAISS）

通过 config.VECTOR_STORE_BACKEND 选择后端，默认 chroma。
每个知识库对应一个独立的 collection（索引分片）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from src.config import VECTOR_STORE_BACKEND

logger = logging.getLogger(__name__)

# 默认 collection 名称（未指定 kb_id 时使用）
DEFAULT_COLLECTION = "knowledge_base"


# ======================================================================
# 抽象接口
# ======================================================================

class VectorStoreBackend(ABC):
    """向量数据库后端抽象接口"""

    @abstractmethod
    def add(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict] | None = None,
        collection_name: str | None = None,
    ) -> None:
        """添加文档到向量库"""

    @abstractmethod
    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
        collection_name: str | None = None,
    ) -> dict:
        """查询向量库，返回 {documents, metadatas, distances, ids}"""

    @abstractmethod
    def delete(self, ids: list[str], collection_name: str | None = None) -> None:
        """删除文档"""

    @abstractmethod
    def count(self, collection_name: str | None = None) -> int:
        """获取指定 collection 的文档总数"""

    @abstractmethod
    def get_all(self, collection_name: str | None = None) -> dict:
        """获取指定 collection 所有文档"""

    @abstractmethod
    def list_collections(self) -> list[str]:
        """列出所有 collection 名称"""

    @abstractmethod
    def delete_collection(self, collection_name: str) -> None:
        """删除整个 collection"""


# ======================================================================
# ChromaDB 后端
# ======================================================================

class ChromaBackend(VectorStoreBackend):
    """ChromaDB 后端实现"""

    def __init__(self) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        from src.config import CHROMA_DB_DIR

        CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(CHROMA_DB_DIR),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collections: dict[str, Any] = {}

    # ------------------------------------------------------------------

    def _get_collection(self, collection_name: str | None = None) -> Any:
        name = collection_name or DEFAULT_COLLECTION
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    def add(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict] | None = None,
        collection_name: str | None = None,
    ) -> None:
        col = self._get_collection(collection_name)
        # ChromaDB 只接受 str/int/float/bool，过滤掉 None 和不支持的类型
        if metadatas is not None:
            metadatas = [
                {k: v for k, v in m.items() if v is not None and isinstance(v, (str, int, float, bool))}
                for m in metadatas
            ]
        col.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
        collection_name: str | None = None,
    ) -> dict:
        col = self._get_collection(collection_name)
        return col.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
        )

    def delete(self, ids: list[str], collection_name: str | None = None) -> None:
        col = self._get_collection(collection_name)
        col.delete(ids=ids)

    def count(self, collection_name: str | None = None) -> int:
        col = self._get_collection(collection_name)
        return col.count()

    def get_all(self, collection_name: str | None = None) -> dict:
        col = self._get_collection(collection_name)
        return col.get()

    def list_collections(self) -> list[str]:
        return [c.name for c in self._client.list_collections()]

    def delete_collection(self, collection_name: str) -> None:
        self._client.delete_collection(collection_name)
        self._collections.pop(collection_name, None)


# ======================================================================
# 工厂函数
# ======================================================================

_backend_instance: VectorStoreBackend | None = None


def get_vector_store_backend() -> VectorStoreBackend:
    """获取全局向量库后端实例（单例）"""
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance

    backend_name = VECTOR_STORE_BACKEND.lower()
    if backend_name == "chroma":
        _backend_instance = ChromaBackend()
    elif backend_name == "faiss":
        from src.core.faiss_backend import FaissBackend
        _backend_instance = FaissBackend()
    elif backend_name == "milvus":
        from src.core.milvus_backend import MilvusBackend
        _backend_instance = MilvusBackend()
    else:
        raise ValueError(
            f"不支持的向量库后端: {backend_name!r}。"
            f"可选: chroma / faiss / milvus。"
            f"请设置 VECTOR_STORE_BACKEND 环境变量。"
        )
    logger.info("向量库后端已初始化: %s", backend_name)
    return _backend_instance


def reset_backend() -> None:
    """重置后端实例（仅供测试使用）"""
    global _backend_instance
    _backend_instance = None


# ======================================================================
# 对外门面 — VectorStore（保持向后兼容）
# ======================================================================

class VectorStore:
    """向量数据库门面

    对外接口与原有 VectorStore 完全一致，内部委托给 VectorStoreBackend。
    支持按 kb_id 隔离数据：每个知识库对应一个独立 collection。
    """

    def __init__(self, collection_name: str = DEFAULT_COLLECTION) -> None:
        self.collection_name = collection_name
        self._backend = get_vector_store_backend()

    # ------------------------------------------------------------------
    # 多 collection 支持（索引分片）
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_collection_name(name: str) -> str:
        """将 kb_id 转为合法的 collection 名称"""
        return "".join(c if c.isalnum() or c == "_" else "_" for c in name)

    def get_collection_for_kb(self, kb_id: str | None = None) -> str:
        """返回指定知识库对应的 collection 名称"""
        name = kb_id if kb_id else DEFAULT_COLLECTION
        return self._safe_collection_name(name)

    def set_kb(self, kb_id: str | None = None) -> None:
        """切换当前操作的知识库"""
        self.collection_name = kb_id or DEFAULT_COLLECTION

    def _active_collection(self) -> str:
        """返回当前激活的 collection 名称"""
        return self._safe_collection_name(self.collection_name)

    # ------------------------------------------------------------------
    # CRUD（委托给 backend）
    # ------------------------------------------------------------------

    def add(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict] | None = None,
    ) -> None:
        self._backend.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            collection_name=self._active_collection(),
        )

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
    ) -> dict:
        return self._backend.query(
            query_embedding=query_embedding,
            n_results=n_results,
            where=where,
            collection_name=self._active_collection(),
        )

    def delete(self, ids: list[str]) -> None:
        self._backend.delete(ids=ids, collection_name=self._active_collection())

    def count(self) -> int:
        return self._backend.count(collection_name=self._active_collection())

    def health_check(self) -> dict:
        """返回向量库健康状态。

        Returns:
            {"status": "ok"|"error", "collection": ..., "count": ...}
        """
        try:
            collection = self._active_collection()
            count = self._backend.count(collection_name=collection)
            return {"status": "ok", "collection": collection, "count": count}
        except Exception as exc:
            return {
                "status": "error",
                "collection": self._active_collection(),
                "count": 0,
                "error": str(exc),
            }

    def get_all(self) -> dict:
        return self._backend.get_all(collection_name=self._active_collection())

    def query_by_source(self, source_filename: str) -> list[dict]:
        """按来源文件名查询所有 chunk

        Args:
            source_filename: 文件名（metadata 中的 source_file 字段）

        Returns:
            [{"chunk_id": ..., "content": ..., "section": ...,
              "page_number": ..., "content_type": ...}, ...]
        """
        col_name = self._active_collection()
        result = self._backend.get_all(collection_name=col_name)

        chunks: list[dict] = []
        if not result or not result.get("ids"):
            return chunks

        for i, chunk_id in enumerate(result["ids"]):
            metadata = result["metadatas"][i] if result.get("metadatas") else {}
            metadata = metadata or {}
            if metadata.get("source_file") != source_filename:
                continue
            chunks.append({
                "chunk_id": chunk_id,
                "content": result["documents"][i] if result.get("documents") else "",
                "section": metadata.get("section", ""),
                "page_number": metadata.get("page_number"),
                "content_type": metadata.get("element_type", "text"),
            })
        return chunks

    # ------------------------------------------------------------------
    # 跨 collection 操作（M6 索引分片）
    # ------------------------------------------------------------------

    def query_multi_kb(
        self,
        query_embedding: list[float],
        kb_ids: list[str],
        n_results: int = 10,
    ) -> list[dict]:
        """跨多个知识库并行检索，合并结果

        Args:
            query_embedding: 查询向量
            kb_ids: 要检索的知识库ID列表
            n_results: 每个知识库返回的结果数

        Returns:
            合并去重后按距离排序的结果列表
        """
        import concurrent.futures

        all_results: list[dict] = []

        def _search_kb(kb_id: str) -> dict:
            col_name = self._safe_collection_name(kb_id)
            return self._backend.query(
                query_embedding=query_embedding,
                n_results=n_results,
                collection_name=col_name,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(kb_ids), 4)) as pool:
            futures = {pool.submit(_search_kb, kb): kb for kb in kb_ids}
            for future in concurrent.futures.as_completed(futures):
                kb_id = futures[future]
                try:
                    result = future.result()
                    if result and result.get("documents"):
                        for i, doc in enumerate(result["documents"][0]):
                            metadata = result["metadatas"][0][i] if result.get("metadatas") else {}
                            distance = result["distances"][0][i] if result.get("distances") else 0
                            metadata["kb_id"] = kb_id
                            all_results.append({
                                "id": result["ids"][0][i] if result.get("ids") else "",
                                "content": doc,
                                "metadata": metadata,
                                "distance": distance,
                            })
                except Exception as e:
                    logger.warning("跨知识库检索 kb_id=%s 失败: %s", kb_id, e)

        # 按距离升序排序（cosine距离越小越相关）
        all_results.sort(key=lambda x: x["distance"])
        return all_results
