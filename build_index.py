"""构建向量索引

支持两种模式：
- 全量模式（--full）：清空重建
- 增量模式（默认）：只处理变化的文件

M6增强：
- 多文件并行加载（ThreadPoolExecutor）
- 批量Embedding（一次encode N个chunk）
- 文档去重（hash + 相似度）
"""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.config import DATA_DIR, PARALLEL_LOAD_WORKERS, BATCH_EMBEDDING_SIZE
from src.core.loaders import LOADERS, get_loader_for_file
from src.core.splitter import SmartSplitter
from src.core.embedder import Embedder
from src.core.vector_store import VectorStore
from src.core.acl import enrich_acl_metadata


def _load_single_file(file_path: Path, splitter: SmartSplitter) -> list:
    """加载并切分单个文件（线程安全）"""
    loader = get_loader_for_file(file_path)
    if loader is None:
        return []
    doc_elements = loader.load(file_path)
    return splitter.split_elements(doc_elements)


def build_index_full():
    """全量模式：清空重建整个向量库"""
    print("=" * 50)
    print("全量构建向量索引（M6批量优化）")
    print("=" * 50)

    from src.storage.database import init_db
    init_db()

    # 初始化组件
    splitter = SmartSplitter()
    embedder = Embedder()
    vector_store = VectorStore()

    # 1. 扫描所有支持的文件
    print("\n[1/6] 扫描文档文件...")
    supported_extensions = set()
    for loader_cls in LOADERS:
        supported_extensions.update(loader_cls.supported_extensions())

    all_files: list[Path] = []
    for ext in supported_extensions:
        all_files.extend(DATA_DIR.rglob(f"*{ext}"))
    all_files = sorted(set(all_files))
    print(f"找到 {len(all_files)} 个文档文件")

    if not all_files:
        print("没有找到任何文档文件，请检查data目录")
        return

    # 2. 文档去重
    print("\n[2/6] 文档去重...")
    from src.core.deduplicator import deduplicate, print_dedup_report
    dedup_result = deduplicate(all_files, embedder=embedder)
    print_dedup_report(dedup_result)
    files_to_index = dedup_result.unique_files
    print(f"去重后待索引: {len(files_to_index)} 个文件")

    # 3. 并行加载 + 切片
    print(f"\n[3/6] 并行加载文档（{PARALLEL_LOAD_WORKERS} 线程）...")
    all_chunks = []
    file_stats: dict[str, int] = {}
    failed_files: list[str] = []

    with ThreadPoolExecutor(max_workers=PARALLEL_LOAD_WORKERS) as pool:
        future_map = {
            pool.submit(_load_single_file, fp, splitter): fp
            for fp in files_to_index
        }
        for future in as_completed(future_map):
            fp = future_map[future]
            try:
                chunks = future.result()
                all_chunks.extend(chunks)
                file_stats[fp.name] = len(chunks)
                print(f"  {fp.name}: {len(chunks)} chunks")
            except Exception as e:
                failed_files.append(fp.name)
                print(f"  {fp.name}: 加载失败 - {e}")

    print(f"\n共切分为 {len(all_chunks)} 个chunk（{len(failed_files)} 个文件失败）")

    if not all_chunks:
        print("没有找到任何文档内容")
        return

    # 4. 批量Embedding
    print(f"\n[4/6] 批量生成向量（batch_size={BATCH_EMBEDDING_SIZE}）...")
    texts = [chunk.content for chunk in all_chunks]

    all_embeddings: list[list[float]] = []
    batch_size = BATCH_EMBEDDING_SIZE
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        batch_emb = embedder.embed(batch)
        all_embeddings.extend(batch_emb)
        done = min(start + batch_size, len(texts))
        print(f"  已处理 {done}/{len(texts)} chunks")

    print(f"向量维度: {len(all_embeddings[0])}")

    # 5. 批量写入向量库
    print("\n[5/6] 批量写入向量库...")
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    def _sanitize_metadata(meta: dict) -> dict:
        """清理metadata，确保所有值是ChromaDB支持的类型"""
        clean = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                clean[k] = v
            elif isinstance(v, list):
                clean[k] = str(v)
            elif v is None:
                clean[k] = ""
            else:
                clean[k] = str(v)
        return clean

    for i, chunk in enumerate(all_chunks):
        source = chunk.metadata.get("source_file", "")
        chunk_id = hashlib.md5(f"{source}_{i}".encode()).hexdigest()
        ids.append(chunk_id)
        documents.append(chunk.content)
        # P0-1 chunk 级 ACL 元数据注入（doc_id/owner_user_id/allowed_roles/kb_id）
        metadatas.append(_sanitize_metadata(enrich_acl_metadata(chunk.metadata)))

    # 清空旧数据
    try:
        old_count = vector_store.count()
        if old_count > 0:
            print(f"  清空旧数据: {old_count} 条")
            old_data = vector_store.get_all()
            vector_store.delete(ids=old_data["ids"])
    except Exception:
        pass

    # 分批写入（避免单次内存过大）
    write_batch = BATCH_EMBEDDING_SIZE * 5  # 500条一批写入
    for start in range(0, len(ids), write_batch):
        end = start + write_batch
        vector_store.add(
            ids=ids[start:end],
            documents=documents[start:end],
            embeddings=all_embeddings[start:end],
            metadatas=metadatas[start:end],
        )
        print(f"  写入 {min(end, len(ids))}/{len(ids)} 条")

    print(f"  成功添加 {len(ids)} 条记录")

    # 6. 统计信息
    print("\n[6/6] 统计信息...")
    total_count = vector_store.count()
    print(f"向量库总记录数: {total_count}")

    print("\n各文件chunk数:")
    for file_name, count in sorted(file_stats.items()):
        print(f"  {file_name}: {count}")

    if failed_files:
        print(f"\n失败文件 ({len(failed_files)}):")
        for name in failed_files:
            print(f"  {name}")

    # 按元素类型统计
    type_stats: dict[str, int] = {}
    for chunk in all_chunks:
        elem_type = chunk.metadata.get("element_type", "unknown")
        type_stats[elem_type] = type_stats.get(elem_type, 0) + 1

    print("\n各元素类型chunk数:")
    for elem_type, count in sorted(type_stats.items()):
        print(f"  {elem_type}: {count}")

    print("\n" + "=" * 50)
    print("全量索引构建完成!")
    print("=" * 50)


def build_index_incremental():
    """增量模式：只处理变化的文件"""
    from src.core.document_scanner import scan_data_directory
    from src.core.incremental_indexer import IncrementalIndexer

    print("=" * 50)
    print("增量构建向量索引")
    print("=" * 50)

    from src.storage.database import init_db
    init_db()

    # 1. 扫描变化
    print("\n[1/3] 扫描文件变化...")
    scan_result = scan_data_directory()
    print(f"  {scan_result.summary()}")

    if not scan_result.has_changes:
        print("\n没有检测到文件变化，索引已是最新")
        return

    # 2. 执行增量索引
    print("\n[2/3] 执行增量索引...")
    indexer = IncrementalIndexer()
    stats = indexer.sync(scan_result)

    # 3. 输出统计
    print("\n[3/3] 同步完成")
    print(f"  新增索引: {stats['added']} 个")
    print(f"  更新索引: {stats['updated']} 个")
    print(f"  删除索引: {stats['deleted']} 个")
    if stats["errors"] > 0:
        print(f"  错误: {stats['errors']} 个")

    print("\n" + "=" * 50)
    print("增量索引完成!")
    print("=" * 50)


def build_index():
    """构建向量索引（默认增量模式）"""
    parser = argparse.ArgumentParser(description="构建向量索引")
    parser.add_argument(
        "--full", action="store_true",
        help="全量模式：清空并重建整个向量库"
    )
    args = parser.parse_args()

    if args.full:
        build_index_full()
    else:
        build_index_incremental()


if __name__ == "__main__":
    build_index()
