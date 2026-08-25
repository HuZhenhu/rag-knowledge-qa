"""增量索引引擎 — 对变化的文件执行索引操作"""
import hashlib
from pathlib import Path

from src.core.document_scanner import (
    ScanResult,
    compute_file_hash,
    get_supported_extensions,
    scan_data_directory,
    update_registry,
)
from src.core.loaders import get_loader_for_file
from src.core.splitter import SmartSplitter
from src.core.embedder import Embedder
from src.core.vector_store import VectorStore
from src.storage.database import (
    init_db,
    get_document_by_path,
    delete_document,
    list_documents,
)


class IncrementalIndexer:
    """增量索引器：处理新增/修改/删除的文件"""

    def __init__(self):
        self.splitter = SmartSplitter()
        self.embedder = Embedder()
        self.vector_store = VectorStore()

    def sync(self, scan_result: ScanResult | None = None) -> dict:
        """执行增量同步

        Args:
            scan_result: 预先扫描的结果，为None则自动扫描

        Returns:
            操作统计 dict
        """
        init_db()

        if scan_result is None:
            scan_result = scan_data_directory()

        stats = {"added": 0, "updated": 0, "deleted": 0, "errors": 0}

        # 处理删除
        for file_path_str in scan_result.deleted:
            try:
                self._delete_file(file_path_str)
                stats["deleted"] += 1
            except Exception as e:
                print(f"  删除失败 {file_path_str}: {e}")
                stats["errors"] += 1

        # 处理修改（先删旧的再重新索引）
        for file_path in scan_result.modified:
            try:
                self._update_file(file_path)
                stats["updated"] += 1
            except Exception as e:
                print(f"  更新失败 {file_path.name}: {e}")
                stats["errors"] += 1

        # 处理新增
        for file_path in scan_result.added:
            try:
                self._add_file(file_path)
                stats["added"] += 1
            except Exception as e:
                print(f"  索引失败 {file_path.name}: {e}")
                stats["errors"] += 1

        # P1-3 缓存失效钩子：检测到任一增/删/改后清空检索/语义缓存
        # （build_index.py 的 build_index_incremental 走本 sync，统一在此失效）
        changed = stats["added"] + stats["updated"] + stats["deleted"]
        if changed > 0:
            try:
                from src.core.semantic_cache import clear_all_caches
                clear_all_caches()
                print(f"  缓存失效：文档变更 {changed} 项，已清空检索/语义缓存")
            except Exception as e:
                print(f"  缓存清理失败: {e}")

        return stats

    def sync_paths(self, paths: list[str]) -> dict:
        """P2-9: 路径级增量同步（事件驱动，仅处理变更文件）

        由 watcher 文件系统事件回调触发，只对事件给出的具体路径做 hash 对比与
        增/删/改索引，避免每次事件全目录重扫，实现亚分钟级事件驱动同步。
        数据库 CDC（binlog/WAL）不适用：数据源为本地文件而非数据库事务日志。

        Args:
            paths: 文件系统事件给出的绝对/相对路径列表

        Returns:
            操作统计 dict（added/updated/deleted/errors）
        """
        from src.config import DATA_DIR
        init_db()

        stats = {"added": 0, "updated": 0, "deleted": 0, "errors": 0}
        supported = get_supported_extensions()
        existing = {r.file_path: r for r in list_documents()}
        base_parent = DATA_DIR.parent

        def to_rel(p: str) -> str:
            try:
                return str(Path(p).resolve().relative_to(Path(base_parent).resolve()))
            except ValueError:
                return str(p)

        for p in paths:
            try:
                path = Path(p)
                if not path.is_file():
                    # 文件不存在 → 按删除处理
                    rel = to_rel(p)
                    if rel in existing:
                        self._delete_file(rel)
                        stats["deleted"] += 1
                    continue
                if path.suffix.lower() not in supported:
                    continue
                rel = to_rel(p)
                cur = compute_file_hash(path)
                if rel in existing:
                    if existing[rel].file_hash != cur:
                        self._update_file(path)
                        stats["updated"] += 1
                else:
                    self._add_file(path)
                    stats["added"] += 1
            except Exception as e:
                print(f"  同步失败 {p}: {e}")
                stats["errors"] += 1

        changed = stats["added"] + stats["updated"] + stats["deleted"]
        if changed > 0:
            try:
                from src.core.semantic_cache import clear_all_caches
                clear_all_caches()
                print(f"  缓存失效：文档变更 {changed} 项，已清空检索/语义缓存")
            except Exception as e:
                print(f"  缓存清理失败: {e}")

        return stats

    def _add_file(self, file_path: Path) -> None:
        """索引新文件：加载→切片→Embedding→追加到向量库"""
        print(f"  [新增] {file_path.name}")

        # 计算hash
        file_hash = compute_file_hash(file_path)

        # 获取loader
        loader = get_loader_for_file(file_path)
        if loader is None:
            update_registry(file_path, file_hash, 0, "error", "没有合适的loader")
            raise ValueError(f"No loader for {file_path.suffix}")

        # 加载
        doc_elements = loader.load(file_path)

        # 切片
        chunks = self.splitter.split_elements(doc_elements)
        if not chunks:
            update_registry(file_path, file_hash, 0, "indexed")
            return

        # Embedding
        texts = [c.content for c in chunks]
        embeddings = self.embedder.embed(texts)

        # 构建向量库数据
        ids = []
        documents = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            source = chunk.metadata.get("source_file", file_path.name)
            chunk_id = hashlib.md5(f"{source}_{i}".encode()).hexdigest()
            ids.append(chunk_id)
            documents.append(chunk.content)
            metadatas.append(chunk.metadata)

        # 追加到向量库
        self.vector_store.add(
            ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas
        )

        # 更新registry
        update_registry(file_path, file_hash, len(chunks), "indexed")
        print(f"    切分为 {len(chunks)} 个chunk，已索引")

    def _update_file(self, file_path: Path) -> None:
        """更新文件：删旧chunk→重新处理→插入新chunk"""
        print(f"  [更新] {file_path.name}")

        # 先删除旧数据
        self._delete_file_by_path(file_path)

        # 重新索引
        self._add_file(file_path)
        print("    更新完成")

    def _delete_file(self, file_path_str: str) -> None:
        """删除文件的向量数据和registry记录"""
        print(f"  [删除] {file_path_str}")

        # 从向量库中删除
        self._delete_file_by_path_str(file_path_str)

        # 从registry中删除
        doc = get_document_by_path(file_path_str)
        if doc:
            delete_document(doc.id)

    def _delete_file_by_path(self, file_path: Path) -> None:
        """根据文件路径从向量库删除该文件的所有chunk"""
        from src.config import DATA_DIR
        rel_path = str(file_path.relative_to(DATA_DIR.parent))
        self._delete_file_by_path_str(rel_path)

    def _delete_file_by_path_str(self, file_path_str: str) -> None:
        """根据文件路径字符串从向量库删除"""
        try:
            all_data = self.vector_store.get_all()
            ids_to_delete = []
            for i, metadata in enumerate(all_data.get("metadatas", [])):
                if metadata.get("source_file", "") == file_path_str:
                    ids_to_delete.append(all_data["ids"][i])
            if ids_to_delete:
                self.vector_store.delete(ids=ids_to_delete)
        except Exception:
            pass
