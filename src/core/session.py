"""会话管理

T2.1 无状态化：会话存储迁出进程内 dict，改为后端抽象
（MemorySessionBackend 进程内 fallback + RedisSessionBackend 生产多副本共享）。
任意 API 副本共享同一后端 → 会话不丢失、任意副本可处理任意请求。
"""
import time
import datetime
from dataclasses import dataclass, field
from typing import Any

from src.config import (
    MAX_HISTORY_ROUNDS,
    SESSION_TIMEOUT_MINUTES,
    USE_CONVERSATION_SUMMARY,
    SUMMARY_THRESHOLD_ROUNDS,
)


@dataclass
class Message:
    """对话消息"""
    role: str  # user 或 assistant
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class Session:
    """会话"""
    session_id: str
    messages: list[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    summary: str = ""  # M8: 对话摘要


class SessionManager:
    """会话管理器（后端可插拔，默认进程内 memory）。"""

    def __init__(self, timeout_minutes: int = SESSION_TIMEOUT_MINUTES,
                 backend: Any = None):
        from src.config import SESSION_BACKEND
        from src.core.session_backend import make_session_backend
        # T2.1: backend 可注入（测试用）；未传则按配置创建（memory|redis）
        self.backend = backend if backend is not None else make_session_backend(SESSION_BACKEND)
        self.timeout_seconds = timeout_minutes * 60
        self._last_cleanup = time.time()
        self._summarizer = None  # 懒加载

    # ---------------- T2.1 序列化 ----------------

    @staticmethod
    def _session_to_dict(s: Session) -> dict[str, Any]:
        return {
            "session_id": s.session_id,
            "messages": [
                {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                for m in s.messages
            ],
            "created_at": s.created_at,
            "last_active": s.last_active,
            "summary": s.summary,
        }

    @staticmethod
    def _session_from_dict(d: dict[str, Any]) -> Session:
        s = Session(
            session_id=d["session_id"],
            created_at=d.get("created_at", time.time()),
            last_active=d.get("last_active", time.time()),
            summary=d.get("summary", ""),
        )
        s.messages = [
            Message(role=m["role"], content=m["content"],
                    timestamp=m.get("timestamp", time.time()))
            for m in d.get("messages", [])
        ]
        return s

    def _get_summarizer(self):
        """懒加载对话摘要器"""
        if self._summarizer is None:
            from src.core.conversation_summarizer import ConversationSummarizer
            self._summarizer = ConversationSummarizer()
        return self._summarizer

    def get_or_create_session(self, session_id: str) -> Session:
        """获取或创建会话（从后端加载，不存在则新建并持久化）"""
        # 每5分钟自动清理一次过期会话
        if time.time() - self._last_cleanup > 300:
            self.cleanup_expired()
            self._last_cleanup = time.time()

        data = self.backend.get(session_id)
        if data is None:
            session = Session(session_id=session_id)
        else:
            session = self._session_from_dict(data)

        session.last_active = time.time()
        self.save_session(session)
        return session

    def save_session(self, session: Session) -> None:
        """将会话写回后端（T2.1：持久化到可插拔存储）。"""
        self.backend.set(session.session_id, self._session_to_dict(session))

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """添加消息到会话"""
        session = self.get_or_create_session(session_id)
        session.messages.append(Message(role=role, content=content))
        session.last_active = time.time()

        # M8: 对话摘要 — 超过阈值轮数时压缩历史
        if USE_CONVERSATION_SUMMARY:
            self._maybe_summarize(session)

        # 保留最近N轮对话（每轮2条消息：user + assistant）
        max_messages = MAX_HISTORY_ROUNDS * 2
        if len(session.messages) > max_messages:
            session.messages = session.messages[-max_messages:]

        self.save_session(session)

    def _maybe_summarize(self, session: Session) -> None:
        """如果对话超过阈值，压缩早期消息为摘要"""
        threshold_messages = SUMMARY_THRESHOLD_ROUNDS * 2
        if len(session.messages) <= threshold_messages:
            return

        summarizer = self._get_summarizer()
        old_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in session.messages
        ]
        session.summary = summarizer.summarize(old_messages)

    def get_history(self, session_id: str) -> list[dict]:
        """获取对话历史（含摘要信息）"""
        session = self.get_or_create_session(session_id)
        return [
            {"role": msg.role, "content": msg.content}
            for msg in session.messages
        ]

    def get_history_with_summary(self, session_id: str) -> list[dict]:
        """获取对话历史，带摘要作为system message。

        Returns:
            [{"role": "system", "content": "摘要..."}, ...最近的对话...]
        """
        session = self.get_or_create_session(session_id)
        messages: list[dict] = []

        if session.summary:
            messages.append({
                "role": "system",
                "content": f"之前对话的摘要：\n{session.summary}",
            })

        messages.extend(
            {"role": msg.role, "content": msg.content}
            for msg in session.messages
        )
        return messages

    def get_summary(self, session_id: str) -> str:
        """获取会话摘要"""
        session = self.get_or_create_session(session_id)
        return session.summary

    def export_session_markdown(self, session_id: str) -> str:
        """将对话导出为Markdown格式"""
        session = self.get_or_create_session(session_id)
        if not session.messages:
            return "# 空对话\n\n该会话没有任何消息。"

        created = datetime.datetime.fromtimestamp(session.created_at)
        lines = [
            "# 对话记录",
            "",
            f"- **会话ID**: `{session.session_id}`",
            f"- **创建时间**: {created.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **消息数量**: {len(session.messages)}",
            "",
            "---",
            "",
        ]

        if session.summary:
            lines.extend([
                "## 对话摘要",
                "",
                session.summary,
                "",
                "---",
                "",
            ])

        for msg in session.messages:
            role_label = "**用户**" if msg.role == "user" else "**助手**"
            ts = datetime.datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")
            lines.extend([
                f"### {role_label} ({ts})",
                "",
                msg.content,
                "",
                "---",
                "",
            ])

        return "\n".join(lines)

    def cleanup_expired(self) -> None:
        """清理过期会话（经后端扫描删除）"""
        current_time = time.time()
        expired_ids = [
            sid for sid, data in self.backend.scan()
            if current_time - data.get("last_active", current_time) > self.timeout_seconds
        ]
        for sid in expired_ids:
            self.backend.delete(sid)
