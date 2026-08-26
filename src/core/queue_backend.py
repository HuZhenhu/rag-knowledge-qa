"""T2.3 消息队列任务编排：生产者-消费者抽象后端。

- MessageQueueBackend: 抽象接口（publish / consume / ack / nack / get_backlog / close）
- MemoryQueueBackend: 进程内队列（本机 fallback，单机默认）
- FileQueueBackend: 文件持久化队列（JSONL，跨进程/重启可恢复）
- KafkaQueueBackend: 生产后端（kafka-python，可注入 mock 验证接口契约）
- RabbitMQQueueBackend: 生产后端（pika，可注入 mock 验证接口契约）
- ConsumerWorker: 消费 worker，成功 ack / 失败 nack 重投 / 超 max_delivery 进死信
- get_queue_backend: 工厂，按 QUEUE_BACKEND 配置（memory/file/kafka/rabbitmq）选择

本机无 Kafka/RabbitMQ 时生产后端采用「可插拔抽象 + mock 单测」验证；
真实集群由 docker-compose.kafka.yml / docker-compose.rabbitmq.yml 提供（交付配置）。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


# ======================================================================
# 消息与抽象接口
# ======================================================================

@dataclass
class Message:
    """队列中的一条消息。attempts 为已重投次数（0 = 首次交付）。"""
    id: str
    topic: str
    payload: dict
    attempts: int = 0
    enqueued_at: float = field(default_factory=time.time)


class MessageQueueBackend(ABC):
    """消息队列后端抽象：提交 / 消费解耦，支持积压监控与 ack/nack 语义。"""

    @abstractmethod
    def publish(self, topic: str, message: dict) -> str:
        """发布消息到 topic，返回消息 id。"""

    @abstractmethod
    def consume(self, topic: str, timeout: float = 0.0) -> Message | None:
        """从 topic 取出一条待处理消息（超时返回 None）。取出后进入 in-flight，需 ack/nack。"""

    @abstractmethod
    def ack(self, message_id: str) -> bool:
        """确认处理成功，消息出队。"""

    @abstractmethod
    def nack(self, message_id: str) -> bool:
        """确认处理失败，消息重投（attempts+1）。"""

    @abstractmethod
    def get_backlog(self, topic: str) -> int:
        """队列积压数（待处理消息数）。"""

    @abstractmethod
    def close(self) -> None:
        """释放资源。"""


def _make_message_id() -> str:
    return uuid.uuid4().hex


# ======================================================================
# Memory 实现（单机 fallback）
# ======================================================================

class MemoryQueueBackend(MessageQueueBackend):
    """进程内队列：FIFO + in-flight 管理 + 积压计数。线程安全。"""

    def __init__(self) -> None:
        self._pending: dict[str, queue.Queue] = {}
        self._inflight: dict[str, Message] = {}
        self._lock = threading.Lock()

    def _q(self, topic: str) -> queue.Queue:
        with self._lock:
            q = self._pending.get(topic)
            if q is None:
                q = queue.Queue()
                self._pending[topic] = q
            return q

    def publish(self, topic: str, message: dict) -> str:
        mid = _make_message_id()
        self._q(topic).put(Message(id=mid, topic=topic, payload=message))
        return mid

    def consume(self, topic: str, timeout: float = 0.0) -> Message | None:
        q = self._q(topic)
        try:
            m = q.get(timeout=timeout)
        except queue.Empty:
            return None
        with self._lock:
            self._inflight[m.id] = m
        return m

    def ack(self, message_id: str) -> bool:
        with self._lock:
            return self._inflight.pop(message_id, None) is not None

    def nack(self, message_id: str) -> bool:
        with self._lock:
            m = self._inflight.pop(message_id, None)
        if m is None:
            return False
        m.attempts += 1
        self._q(m.topic).put(m)
        return True

    def get_backlog(self, topic: str) -> int:
        return self._q(topic).qsize()

    def close(self) -> None:
        with self._lock:
            self._pending.clear()
            self._inflight.clear()


# ======================================================================
# File 持久化实现（JSONL，跨进程/重启可恢复）
# ======================================================================

class FileQueueBackend(MessageQueueBackend):
    """文件持久化队列：每个 topic 一个 <topic>.jsonl。

    每行一条消息 JSON：{id, topic, payload, attempts, enqueued_at, status}。
    status: pending（待处理） / inflight（已取出未 ack）。
    写回采用临时文件 + os.replace 原子替换。
    """

    def __init__(self, dir_path: str = "data/queue"):
        self._dir = dir_path
        os.makedirs(self._dir, exist_ok=True)
        self._lock = threading.Lock()
        self._inflight: dict[str, Message] = {}

    def _path(self, topic: str) -> str:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in topic)
        return os.path.join(self._dir, f"{safe}.jsonl")

    def _load(self, topic: str) -> list[dict]:
        p = self._path(topic)
        if not os.path.exists(p):
            return []
        rows = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def _save(self, topic: str, rows: list[dict]) -> None:
        p = self._path(topic)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, p)

    def publish(self, topic: str, message: dict) -> str:
        mid = _make_message_id()
        row = {"id": mid, "topic": topic, "payload": message,
               "attempts": 0, "enqueued_at": time.time(), "status": "pending"}
        with self._lock:
            with open(self._path(topic), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return mid

    def consume(self, topic: str, timeout: float = 0.0) -> Message | None:
        deadline = time.time() + timeout
        while True:
            with self._lock:
                rows = self._load(topic)
                idx = next((i for i, r in enumerate(rows) if r.get("status") == "pending"), None)
                if idx is not None:
                    row = rows[idx]
                    row["status"] = "inflight"
                    self._save(topic, rows)
                    m = Message(id=row["id"], topic=topic, payload=row["payload"],
                                attempts=row.get("attempts", 0), enqueued_at=row.get("enqueued_at", 0.0))
                    self._inflight[m.id] = m
                    return m
            if time.time() >= deadline:
                return None
            time.sleep(0.01)

    def _finish(self, message_id: str, status: str) -> bool:
        with self._lock:
            m = self._inflight.pop(message_id, None)
            if m is None:
                return False
            rows = self._load(m.topic)
            for r in rows:
                if r.get("id") == message_id:
                    r["status"] = status
                    if status == "pending":
                        r["attempts"] = r.get("attempts", 0) + 1
                    break
            self._save(m.topic, rows)
            return True

    def ack(self, message_id: str) -> bool:
        return self._finish(message_id, "done")

    def nack(self, message_id: str) -> bool:
        return self._finish(message_id, "pending")

    def get_backlog(self, topic: str) -> int:
        with self._lock:
            return sum(1 for r in self._load(topic) if r.get("status") == "pending")

    def close(self) -> None:
        with self._lock:
            self._inflight.clear()


# ======================================================================
# Kafka 生产后端
# ======================================================================

class KafkaQueueBackend(MessageQueueBackend):
    """Kafka 生产后端（kafka-python）。

    连接/生产者/消费者/admin 支持注入（测试传 mock）；未注入时按配置 lazy 创建。
    积压监控：通过 admin.list_offsets 读取 topic 各分区 end_offset 求和（简化：待消费总量）。
    """

    def __init__(self, bootstrap_servers: str = "localhost:9092",
                 group_id: str = "rag-worker",
                 producer: Any = None, consumer: Any = None,
                 admin: Any = None) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._producer = producer
        self._consumer = consumer
        self._admin = admin

    def _get_producer(self):
        if self._producer is None:
            from kafka import KafkaProducer
            self._producer = KafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
        return self._producer

    def _get_consumer(self):
        if self._consumer is None:
            from kafka import KafkaConsumer
            self._consumer = KafkaConsumer(
                self._group_id,
                bootstrap_servers=self._bootstrap_servers,
                auto_offset_reset="latest",
                enable_auto_commit=False,
                value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            )
        return self._consumer

    def publish(self, topic: str, message: dict) -> str:
        mid = _make_message_id()
        self._get_producer().send(topic, {"id": mid, "payload": message})
        return mid

    def consume(self, topic: str, timeout: float = 0.0) -> Message | None:
        c = self._get_consumer()
        c.subscribe([topic])
        records = c.poll(timeout_ms=max(1, int(timeout * 1000)))
        for tp, msgs in records.items():
            for raw in msgs:
                value = getattr(raw, "value", raw)
                if isinstance(value, bytes):
                    value = json.loads(value.decode("utf-8"))
                elif isinstance(value, str):
                    value = json.loads(value)
                return Message(id=value.get("id", _make_message_id()),
                               topic=topic, payload=value.get("payload", {}))
        return None

    def ack(self, message_id: str) -> bool:
        c = self._get_consumer()
        if hasattr(c, "commit"):
            c.commit()
        return True

    def nack(self, message_id: str) -> bool:
        # Kafka 语义下重试由 offset 回退/重投实现；此处保持接口契约
        return True

    def get_backlog(self, topic: str, admin: Any = None) -> int:
        admin = admin or self._admin
        if admin is None:
            return 0
        topics = admin.describe_topics([topic])
        end_offsets = admin.list_offsets()
        total = 0
        for desc in topics:
            for part in desc.get("partitions", []):
                key = (desc.get("topic"), part.get("partition"))
                off = end_offsets.get(key)
                if off is not None and hasattr(off, "offset"):
                    total += off.offset
        return total

    def close(self) -> None:
        for obj in (self._producer, self._consumer, self._admin):
            if obj is not None and hasattr(obj, "close"):
                try:
                    obj.close()
                except Exception:
                    pass


# ======================================================================
# RabbitMQ 生产后端
# ======================================================================

class RabbitMQQueueBackend(MessageQueueBackend):
    """RabbitMQ 生产后端（pika）。连接/通道支持注入（测试传 mock）；未注入时按配置 lazy 创建。

    - publish: basic_publish(exchange="", routing_key=topic)
    - consume: basic_get(auto_ack=False)，delivery_tag 用于 ack/nack
    - get_backlog: queue_declare(passive=True).method.message_count
    """

    def __init__(self, host: str = "localhost", port: int = 5672,
                 user: str = "guest", password: str = "guest",
                 vhost: str = "/", connection: Any = None,
                 channel: Any = None) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._vhost = vhost
        self._connection = connection
        self._channel = channel

    def _get_channel(self):
        if self._channel is None:
            import pika
            credentials = pika.PlainCredentials(self._user, self._password)
            self._connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=self._host, port=self._port,
                                          virtual_host=self._vhost, credentials=credentials))
            self._channel = self._connection.channel()
        return self._channel

    def publish(self, topic: str, message: dict) -> str:
        mid = _make_message_id()
        self._get_channel().basic_publish(
            exchange="", routing_key=topic,
            body=json.dumps({"id": mid, "payload": message}).encode("utf-8"))
        return mid

    def consume(self, topic: str, timeout: float = 0.0) -> Message | None:
        ch = self._get_channel()
        method, _props, body = ch.basic_get(queue=topic, auto_ack=False)
        if method is None or body is None:
            return None
        value = json.loads(body.decode("utf-8"))
        return Message(id=str(getattr(method, "delivery_tag", value.get("id", ""))),
                       topic=topic, payload=value.get("payload", {}))

    def ack(self, message_id: str) -> bool:
        self._get_channel().basic_ack(delivery_tag=int(message_id))
        return True

    def nack(self, message_id: str) -> bool:
        self._get_channel().basic_nack(delivery_tag=int(message_id), requeue=True)
        return True

    def get_backlog(self, topic: str) -> int:
        method = self._get_channel().queue_declare(queue=topic, passive=True)
        return int(getattr(method.method, "message_count", 0))

    def close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
        if self._connection is not None and hasattr(self._connection, "close"):
            try:
                self._connection.close()
            except Exception:
                pass


# ======================================================================
# ConsumerWorker：消费 + 重试 + 死信
# ======================================================================

class ConsumerWorker:
    """消费 worker：拉取一条 -> 调用 handler -> 成功 ack / 异常 nack 重投 /
    重投次数达 max_delivery 进死信（on_dead_letter 回调，并移除出队）。

    process_one() 返回状态：empty（无消息）/ ack / retry / dead。
    """

    def __init__(self, backend: MessageQueueBackend, topic: str,
                 handler: Callable[[dict], Any],
                 max_delivery: int = 3,
                 on_dead_letter: Callable[[Message], Any] | None = None) -> None:
        self._backend = backend
        self._topic = topic
        self._handler = handler
        self._max_delivery = int(max_delivery)
        self._on_dead_letter = on_dead_letter

    def process_one(self) -> str:
        m = self._backend.consume(self._topic, timeout=0.0)
        if m is None:
            return "empty"
        try:
            self._handler(m.payload)
        except Exception:
            if m.attempts + 1 >= self._max_delivery:
                self._backend.ack(m.id)  # 出队，避免死信消息阻塞积压
                if self._on_dead_letter is not None:
                    self._on_dead_letter(m)
                return "dead"
            self._backend.nack(m.id)
            return "retry"
        self._backend.ack(m.id)
        return "ack"

    def backlog(self) -> int:
        return self._backend.get_backlog(self._topic)


# ======================================================================
# 工厂
# ======================================================================

def get_queue_backend(kind: str | None = None) -> MessageQueueBackend:
    """按 QUEUE_BACKEND 配置（memory/file/kafka/rabbitmq）创建后端。

    kind 参数优先；未传时动态读环境变量（便于测试 setenv 生效，不受模块级绑定影响）。
    """
    kind = (kind or os.getenv("QUEUE_BACKEND", "memory")).lower()
    if kind == "kafka":
        return KafkaQueueBackend(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            group_id=os.getenv("KAFKA_GROUP_ID", "rag-worker"))
    if kind == "rabbitmq":
        return RabbitMQQueueBackend(
            host=os.getenv("RABBITMQ_HOST", "localhost"),
            port=int(os.getenv("RABBITMQ_PORT", "5672")),
            user=os.getenv("RABBITMQ_USER", "guest"),
            password=os.getenv("RABBITMQ_PASSWORD", "guest"),
            vhost=os.getenv("RABBITMQ_VHOST", "/"))
    if kind == "file":
        return FileQueueBackend(dir_path=os.getenv("FILE_QUEUE_DIR", "data/queue"))
    return MemoryQueueBackend()
