"""SQLite数据库管理 — 文档注册表 + 用户体系 + 多知识库 + 审计日志"""
import sqlite3
from datetime import datetime

from src.config import BASE_DIR
from src.storage.models import DocumentRecord

DB_PATH = BASE_DIR / "data" / "rag.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------

def init_db() -> None:
    """初始化所有数据表"""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_registry (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                indexed_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                error_message TEXT DEFAULT ''
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TEXT NOT NULL,
                last_login TEXT DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                owner_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                document_count INTEGER DEFAULT 0,
                chunk_count INTEGER DEFAULT 0,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_permissions (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                permission TEXT NOT NULL DEFAULT 'read',
                FOREIGN KEY (document_id) REFERENCES document_registry(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT DEFAULT '',
                resource_id TEXT DEFAULT '',
                details TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS traces (
                id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                user_id TEXT DEFAULT '',
                status TEXT DEFAULT 'ok',
                stages TEXT DEFAULT '{}',
                total_ms REAL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                user_id TEXT DEFAULT '',
                query TEXT NOT NULL,
                rating INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                total_cases INTEGER NOT NULL,
                success_cases INTEGER NOT NULL,
                retrieval_hit_rate REAL NOT NULL,
                answer_accuracy REAL NOT NULL,
                citation_accuracy REAL NOT NULL,
                avg_semantic_similarity REAL NOT NULL,
                avg_latency_ms REAL NOT NULL,
                details TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Document Registry
# ---------------------------------------------------------------------------

def upsert_document(doc: DocumentRecord) -> None:
    """插入或更新文档记录"""
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT INTO document_registry
                (id, filename, file_path, file_hash, file_type, file_size,
                 chunk_count, status, indexed_at, updated_at, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                filename=excluded.filename,
                file_path=excluded.file_path,
                file_hash=excluded.file_hash,
                file_type=excluded.file_type,
                file_size=excluded.file_size,
                chunk_count=excluded.chunk_count,
                status=excluded.status,
                indexed_at=excluded.indexed_at,
                updated_at=excluded.updated_at,
                error_message=excluded.error_message
        """, (
            doc.id, doc.filename, doc.file_path, doc.file_hash,
            doc.file_type, doc.file_size, doc.chunk_count,
            doc.status, doc.indexed_at, doc.updated_at, doc.error_message,
        ))
        conn.commit()
    finally:
        conn.close()


def get_document(doc_id: str) -> DocumentRecord | None:
    """根据ID获取文档记录"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM document_registry WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)
    finally:
        conn.close()


def get_document_by_path(file_path: str) -> DocumentRecord | None:
    """根据文件路径获取文档记录"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM document_registry WHERE file_path = ?", (file_path,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)
    finally:
        conn.close()


def list_documents(status: str | None = None) -> list[DocumentRecord]:
    """列出所有文档记录，可按状态过滤"""
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM document_registry WHERE status = ? ORDER BY indexed_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM document_registry ORDER BY indexed_at DESC"
            ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def delete_document(doc_id: str) -> None:
    """删除文档记录"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM document_registry WHERE id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()


def get_stats() -> dict:
    """获取索引统计信息"""
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM document_registry").fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM document_registry WHERE status = 'indexed'"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM document_registry WHERE status = 'pending'"
        ).fetchone()[0]
        error = conn.execute(
            "SELECT COUNT(*) FROM document_registry WHERE status = 'error'"
        ).fetchone()[0]
        total_chunks = conn.execute(
            "SELECT COALESCE(SUM(chunk_count), 0) FROM document_registry WHERE status = 'indexed'"
        ).fetchone()[0]
        return {
            "total_documents": total,
            "indexed": indexed,
            "pending": pending,
            "error": error,
            "total_chunks": total_chunks,
        }
    finally:
        conn.close()


def _row_to_record(row: sqlite3.Row) -> DocumentRecord:
    """将数据库行转换为DocumentRecord"""
    return DocumentRecord(
        id=row["id"],
        filename=row["filename"],
        file_path=row["file_path"],
        file_hash=row["file_hash"],
        file_type=row["file_type"],
        file_size=row["file_size"],
        chunk_count=row["chunk_count"],
        status=row["status"],
        indexed_at=row["indexed_at"],
        updated_at=row["updated_at"],
        error_message=row["error_message"],
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def create_user(user_id: str, username: str, password_hash: str,
                role: str = "admin") -> dict | None:
    """创建用户，返回用户字典；用户名已存在时返回 None"""
    conn = _get_conn()
    try:
        try:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, created_at, is_active) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (user_id, username, password_hash, role,
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
            return get_user_by_id(user_id)
        except sqlite3.IntegrityError:
            return None
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> dict | None:
    """根据ID获取用户"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    """根据用户名获取用户"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_user_login(user_id: str) -> None:
    """更新最后登录时间"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_users() -> list[dict]:
    """列出所有用户"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, last_login, is_active FROM users ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Knowledge Bases
# ---------------------------------------------------------------------------

def create_knowledge_base(kb_id: str, name: str, description: str,
                          owner_id: str) -> dict | None:
    """创建知识库"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO knowledge_bases (id, name, description, owner_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kb_id, name, description, owner_id,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return get_knowledge_base(kb_id)
    finally:
        conn.close()


def get_knowledge_base(kb_id: str) -> dict | None:
    """根据ID获取知识库"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_knowledge_bases(owner_id: str | None = None) -> list[dict]:
    """列出知识库，可按 owner 过滤"""
    conn = _get_conn()
    try:
        if owner_id:
            rows = conn.execute(
                "SELECT * FROM knowledge_bases WHERE owner_id = ? ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM knowledge_bases ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_knowledge_base_counts(kb_id: str, document_count: int,
                                 chunk_count: int) -> None:
    """更新知识库的文档数和 chunk 数"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE knowledge_bases SET document_count = ?, chunk_count = ? WHERE id = ?",
            (document_count, chunk_count, kb_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_knowledge_base(kb_id: str) -> None:
    """删除知识库"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Document Permissions
# ---------------------------------------------------------------------------

def set_document_permission(doc_id: str, user_id: str,
                            permission: str = "read") -> dict | None:
    """设置文档权限（插入或更新）"""
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM document_permissions WHERE document_id = ? AND user_id = ?",
            (doc_id, user_id),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE document_permissions SET permission = ? WHERE id = ?",
                (permission, existing["id"]),
            )
            perm_id = existing["id"]
        else:
            import uuid
            perm_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO document_permissions (id, document_id, user_id, permission) "
                "VALUES (?, ?, ?, ?)",
                (perm_id, doc_id, user_id, permission),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM document_permissions WHERE id = ?", (perm_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_document_permissions(doc_id: str) -> list[dict]:
    """获取文档的所有权限"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM document_permissions WHERE document_id = ?", (doc_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_user_readable_doc_ids(user_id: str) -> list[str]:
    """获取用户有 read 权限的所有文档ID"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT document_id FROM document_permissions WHERE user_id = ? AND permission IN ('read', 'write', 'admin')",
            (user_id,),
        ).fetchall()
        return [r["document_id"] for r in rows]
    finally:
        conn.close()


def remove_document_permission(doc_id: str, user_id: str) -> None:
    """移除文档权限"""
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM document_permissions WHERE document_id = ? AND user_id = ?",
            (doc_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------

def create_audit_log(user_id: str, action: str, resource_type: str = "",
                     resource_id: str = "", details: str = "",
                     ip_address: str = "") -> None:
    """写入审计日志"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO audit_logs (user_id, action, resource_type, resource_id, details, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, action, resource_type, resource_id, details, ip_address,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit_logs(user_id: str | None = None, action: str | None = None,
                    limit: int = 100) -> list[dict]:
    """查询审计日志"""
    conn = _get_conn()
    try:
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params: list = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if action:
            query += " AND action = ?"
            params.append(action)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def create_feedback(request_id: str, query: str, rating: int,
                    user_id: str = "") -> int:
    """写入用户反馈，返回新记录的 id

    Args:
        request_id: 关联的请求 ID（trace_id）
        query: 用户查询文本
        rating: 1=赞 / -1=踩
        user_id: 调用者 ID
    """
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO feedback (request_id, user_id, query, rating, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (request_id, user_id, query, rating,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def list_feedback(limit: int = 100) -> list[dict]:
    """列出最近的反馈记录"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_feedback_stats() -> dict:
    """获取反馈统计：总数、赞数、踩数"""
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        positive = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE rating = 1"
        ).fetchone()[0]
        negative = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE rating = -1"
        ).fetchone()[0]
        return {"total": total, "positive": positive, "negative": negative}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Evaluations
# ---------------------------------------------------------------------------

def save_evaluation(version: str, total_cases: int, success_cases: int,
                    retrieval_hit_rate: float, answer_accuracy: float,
                    citation_accuracy: float, avg_semantic_similarity: float,
                    avg_latency_ms: float, details: str = "{}") -> int:
    """保存评测结果，返回记录 ID"""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO evaluations "
            "(version, total_cases, success_cases, retrieval_hit_rate, "
            "answer_accuracy, citation_accuracy, avg_semantic_similarity, "
            "avg_latency_ms, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (version, total_cases, success_cases, retrieval_hit_rate,
             answer_accuracy, citation_accuracy, avg_semantic_similarity,
             avg_latency_ms, details,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


def list_evaluations(limit: int = 20) -> list[dict]:
    """列出最近的评测记录"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM evaluations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_evaluation() -> dict | None:
    """获取最近一次评测结果"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM evaluations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_previous_evaluation() -> dict | None:
    """获取上一次（倒数第二次）评测结果，用于对比"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM evaluations ORDER BY id DESC LIMIT 1 OFFSET 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
