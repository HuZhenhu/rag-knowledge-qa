"""Phase 2 T2.3 消息队列任务编排 — 生产者-消费者抽象单测

覆盖：
- 抽象接口：publish / consume / ack / nack / get_backlog / close
- MemoryQueueBackend：FIFO、积压计数、ack 不重投、nack 重投且 attempts+1、超时返回 None
- FileQueueBackend：持久化（新实例可消费）、nack 重投 attempts 递增
- KafkaQueueBackend：mock kafka-python，验证 bootstrap_servers / topic 透传与积压
- RabbitMQQueueBackend：mock pika，验证 routing_key / basic_get / message_count 积压
- ConsumerWorker：成功 ack、失败重投、超过 max_delivery 进死信
- 工厂：QUEUE_BACKEND 配置选择正确后端

本机无 Kafka/RabbitMQ，生产后端用 mock 验证接口契约；真实集群由 compose 提供。
"""
from __future__ import annotations

import json
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest


# ======================================================================
# 1. 抽象接口与 Memory 实现
# ======================================================================

def test_abstract_interface_exists():
    from src.core.queue_backend import MessageQueueBackend
    for name in ("publish", "consume", "ack", "nack", "get_backlog", "close"):
        assert hasattr(MessageQueueBackend, name), f"缺少抽象方法 {name}"


def test_memory_publish_consume_fifo():
    from src.core.queue_backend import MemoryQueueBackend
    b = MemoryQueueBackend()
    id1 = b.publish("rag.tasks", {"q": "hello"})
    id2 = b.publish("rag.tasks", {"q": "world"})
    assert isinstance(id1, str) and id1
    assert b.get_backlog("rag.tasks") == 2
    m1 = b.consume("rag.tasks")
    assert m1.id == id1 and m1.payload == {"q": "hello"}
    assert b.get_backlog("rag.tasks") == 1  # 已取出未 ack，不算积压
    m2 = b.consume("rag.tasks")
    assert m2.id == id2
    assert b.consume("rag.tasks", timeout=0.0) is None  # 空队列超时返回 None


def test_memory_ack_and_nack():
    from src.core.queue_backend import MemoryQueueBackend
    b = MemoryQueueBackend()
    mid = b.publish("rag.tasks", {"q": "x"})
    m = b.consume("rag.tasks")
    assert b.ack(m.id) is True
    assert b.consume("rag.tasks", timeout=0.0) is None  # ack 后不重投

    mid2 = b.publish("rag.tasks", {"q": "y"})
    m2 = b.consume("rag.tasks")
    assert b.nack(m2.id) is True
    assert m2.attempts == 1
    again = b.consume("rag.tasks")
    assert again.id == mid2 and again.attempts == 1  # nack 重投，attempts 保留
    assert b.ack(again.id) is True


def test_memory_backlog_counts_only_pending():
    from src.core.queue_backend import MemoryQueueBackend
    b = MemoryQueueBackend()
    for i in range(5):
        b.publish("t", {"i": i})
    assert b.get_backlog("t") == 5
    b.consume("t")
    b.consume("t")
    assert b.get_backlog("t") == 3


# ======================================================================
# 2. File 持久化实现
# ======================================================================

def test_file_queue_persists_across_instances(tmp_path):
    from src.core.queue_backend import FileQueueBackend
    b1 = FileQueueBackend(dir_path=str(tmp_path))
    b1.publish("rag.tasks", {"q": "persisted"})
    b1.close()

    b2 = FileQueueBackend(dir_path=str(tmp_path))
    m = b2.consume("rag.tasks")
    assert m is not None and m.payload == {"q": "persisted"}
    assert b2.get_backlog("rag.tasks") == 0
    assert b2.ack(m.id) is True
    b2.close()


def test_file_queue_nack_redelivers_with_attempts(tmp_path):
    from src.core.queue_backend import FileQueueBackend
    b = FileQueueBackend(dir_path=str(tmp_path))
    mid = b.publish("rag.tasks", {"q": "retry"})
    m = b.consume("rag.tasks")
    assert b.nack(m.id) is True
    m2 = b.consume("rag.tasks")
    assert m2.id == mid and m2.attempts == 1
    b.close()


# ======================================================================
# 3. Kafka / RabbitMQ 生产后端（mock 验证接口契约）
# ======================================================================

class _FakeKafka:
    """伪 kafka-python 模块，用于验证 KafkaQueueBackend 的连接/发布/积压。"""

    def __init__(self):
        self.producer_class = MagicMock()
        self.consumer_class = MagicMock()
        self.admin_class = MagicMock()
        self._fake = True


def _install_fake_kafka():
    if "kafka" in sys.modules and not getattr(sys.modules["kafka"], "_FAKE_KAFKA", False):
        return sys.modules["kafka"]
    fake = types.ModuleType("kafka")
    fake._FAKE_KAFKA = True
    fake.KafkaProducer = MagicMock()
    fake.KafkaConsumer = MagicMock()
    fake.KafkaAdminClient = MagicMock()
    fake.KafkaProducer.return_value.send.return_value = MagicMock()
    sys.modules["kafka"] = fake
    return fake


def test_kafka_backend_publishes_and_reports_backlog(monkeypatch):
    fake = _install_fake_kafka()
    from src.core import queue_backend as qb
    from src.core.queue_backend import KafkaQueueBackend

    prod = fake.KafkaProducer.return_value
    admin = fake.KafkaAdminClient.return_value
    # 模拟 describe_topics：topic 单分区 end_offset=5
    admin.describe_topics.return_value = [
        {"topic": "rag.tasks", "partitions": [{"partition": 0, "leader": 1}]}
    ]
    admin.list_offsets.return_value = {("rag.tasks", 0): MagicMock(offset=5)}

    b = KafkaQueueBackend(bootstrap_servers="kafka:9092", group_id="rag-worker",
                          producer=prod, consumer=fake.KafkaConsumer.return_value, admin=admin)
    mid = b.publish("rag.tasks", {"q": "kafka"})
    assert prod.send.called
    send_call = prod.send.call_args
    assert send_call.args[0] == "rag.tasks"
    value_arg = send_call.args[1]  # send(topic, value)，value_serializer 在真实 KafkaProducer 侧生效
    assert value_arg["payload"]["q"] == "kafka"

    fake.KafkaConsumer.return_value.poll.return_value = {
        "rag.tasks": [MagicMock(value=json.dumps(value_arg).encode("utf-8"))]
    }
    m = b.consume("rag.tasks")
    assert m is not None and m.payload == {"q": "kafka"}

    backlog = b.get_backlog("rag.tasks", admin=admin)
    assert backlog == 5


def test_rabbitmq_backend_publishes_and_reports_backlog():
    pika = types.ModuleType("pika")
    pika._FAKE_PIKA = True
    pika.BlockingConnection = MagicMock()
    sys.modules.setdefault("pika", pika)

    from src.core.queue_backend import RabbitMQQueueBackend
    channel = pika.BlockingConnection.return_value.channel.return_value
    b = RabbitMQQueueBackend(host="mq:5672", user="u", password="p",
                             connection=pika.BlockingConnection.return_value, channel=channel)
    b.publish("rag.tasks", {"q": "rabbit"})
    call = channel.basic_publish.call_args
    assert call.kwargs.get("routing_key") == "rag.tasks"

    # basic_get 返回 (method, properties, body)；message_count 来自 queue_declare(passive)
    channel.queue_declare.return_value.method.message_count = 7
    channel.basic_get.return_value = (MagicMock(delivery_tag=1), MagicMock(),
                                      json.dumps({"payload": {"q": "rabbit"}}).encode("utf-8"))
    m = b.consume("rag.tasks")
    assert m is not None and m.payload == {"q": "rabbit"}
    assert b.ack("1") is True
    channel.basic_ack.assert_called_once()

    assert b.get_backlog("rag.tasks") == 7
    b.close()


# ======================================================================
# 4. ConsumerWorker 重试与死信
# ======================================================================

def test_worker_ack_on_success():
    from src.core.queue_backend import ConsumerWorker, MemoryQueueBackend
    b = MemoryQueueBackend()
    mid = b.publish("rag.tasks", {"q": "ok"})
    handled = []

    w = ConsumerWorker(b, topic="rag.tasks", handler=lambda p: handled.append(p))
    status = w.process_one()
    assert status == "ack"
    assert handled == [{"q": "ok"}]
    assert b.consume("rag.tasks", timeout=0.0) is None  # 已 ack


def test_worker_nack_and_redelivery_until_success():
    from src.core.queue_backend import ConsumerWorker, MemoryQueueBackend
    b = MemoryQueueBackend()
    b.publish("rag.tasks", {"q": "flaky"})
    calls = {"n": 0}

    def handler(p):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "done"

    w = ConsumerWorker(b, topic="rag.tasks", handler=handler, max_delivery=3)
    assert w.process_one() == "retry"   # 第一次失败
    assert w.process_one() == "retry"   # 第二次失败
    assert w.process_one() == "ack"     # 第三次成功
    assert calls["n"] == 3


def test_worker_dead_letter_after_max_delivery():
    from src.core.queue_backend import ConsumerWorker, MemoryQueueBackend
    b = MemoryQueueBackend()
    b.publish("rag.tasks", {"q": "poison"})
    dead = []

    w = ConsumerWorker(b, topic="rag.tasks",
                       handler=lambda p: (_ for _ in ()).throw(RuntimeError("always fail")),
                       max_delivery=2,
                       on_dead_letter=lambda m: dead.append(m))
    assert w.process_one() == "retry"
    assert w.process_one() == "dead"
    assert len(dead) == 1 and dead[0].payload == {"q": "poison"}
    assert b.get_backlog("rag.tasks") == 0  # 死信后不再积压


def test_worker_get_backlog_monitoring():
    from src.core.queue_backend import ConsumerWorker, MemoryQueueBackend
    b = MemoryQueueBackend()
    for i in range(4):
        b.publish("rag.tasks", {"i": i})
    w = ConsumerWorker(b, topic="rag.tasks", handler=lambda p: None)
    assert w.backlog() == 4


# ======================================================================
# 5. 工厂与生产配置
# ======================================================================

def test_factory_selects_memory(monkeypatch):
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    from src.core.queue_backend import get_queue_backend, MemoryQueueBackend
    assert isinstance(get_queue_backend(), MemoryQueueBackend)


def test_factory_selects_kafka(monkeypatch):
    _install_fake_kafka()
    monkeypatch.setenv("QUEUE_BACKEND", "kafka")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("KAFKA_GROUP_ID", "rag-worker")
    from src.core.queue_backend import get_queue_backend, KafkaQueueBackend
    b = get_queue_backend()
    assert isinstance(b, KafkaQueueBackend)
    b.close()


def test_factory_selects_rabbitmq(monkeypatch):
    monkeypatch.setenv("QUEUE_BACKEND", "rabbitmq")
    monkeypatch.setenv("RABBITMQ_HOST", "mq")
    monkeypatch.setenv("RABBITMQ_PORT", "5672")
    from src.core.queue_backend import get_queue_backend, RabbitMQQueueBackend
    b = get_queue_backend()
    assert isinstance(b, RabbitMQQueueBackend)
    b.close()


def test_production_mq_compose_exists():
    for f in ("docker-compose.kafka.yml", "docker-compose.rabbitmq.yml"):
        assert __import__("pathlib").Path(f).exists(), f"缺少生产编排 {f}"
