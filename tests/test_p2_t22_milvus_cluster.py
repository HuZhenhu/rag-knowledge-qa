# -*- coding: utf-8 -*-
"""Phase 2 T2.2 向量库集群化 — Milvus 生产后端单测

覆盖：
- 连接参数透传（URI/user/password/timeout/secure）与连接重试
- consistency_level（一致性级别）配置应用于查询
- 抽象接口 add/query/count/delete 行为（mock pymilvus）
- collection 按 kb 隔离
- collection 别名（索引重建与查询互不影响，零停机切换）
- 生产 Milvus 服务端部署配置（docker-compose.milvus.yml）完整性

pymilvus 未安装时注入 FakePymilvus 模块，保证本机单测可跑。
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _install_fake_pymilvus() -> types.ModuleType:
    """向 sys.modules 注入 FakePymilvus（若真实 pymilvus 已装则跳过）"""
    if "pymilvus" in sys.modules and not getattr(sys.modules["pymilvus"], "_FAKE_MILVUS", False):
        return sys.modules["pymilvus"]
    fake = types.ModuleType("pymilvus")
    fake._FAKE_MILVUS = True
    fake.connections = MagicMock()
    fake.utility = MagicMock()
    fake.Collection = MagicMock()
    fake.CollectionSchema = MagicMock()
    fake.FieldSchema = MagicMock()
    fake.DataType = SimpleNamespace(
        INT64="INT64", FLOAT_VECTOR="FLOAT_VECTOR", VARCHAR="VARCHAR", JSON="JSON"
    )
    sys.modules["pymilvus"] = fake
    return fake


FAKE = _install_fake_pymilvus()


def _reset_fake():
    """重置全部 mock。注意：reset_mock() 不清除 side_effect（迭代器会泄漏到下一测试），
    需显式递归清空子 mock 的 side_effect。"""
    def _clear_side_effects(mock):
        mock.side_effect = None
        for child in mock._mock_children.values():
            _clear_side_effects(child)

    for attr in ("connections", "utility", "Collection"):
        _clear_side_effects(getattr(FAKE, attr))
        getattr(FAKE, attr).reset_mock()
    FAKE.utility.has_collection.return_value = False


@pytest.fixture(autouse=True)
def _auto_reset():
    _reset_fake()
    yield
    _reset_fake()


# ======================================================================
# 1. 连接参数透传与重试
# ======================================================================

def test_milvus_connect_passes_production_params(monkeypatch):
    from src.config import (
        MILVUS_CONNECT_RETRIES,
        MILVUS_PASSWORD,
        MILVUS_SECURE,
        MILVUS_TIMEOUT_SECONDS,
        MILVUS_URI,
        MILVUS_USER,
    )
    monkeypatch.setattr("src.config.MILVUS_URI", "http://milvus:19530")
    monkeypatch.setattr("src.config.MILVUS_USER", "root")
    monkeypatch.setattr("src.config.MILVUS_PASSWORD", "secret")
    monkeypatch.setattr("src.config.MILVUS_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr("src.config.MILVUS_SECURE", True)
    monkeypatch.setattr("src.config.MILVUS_CONNECT_RETRIES", 1)

    import src.core.milvus_backend as mb
    mb.MILVUS_URI = "http://milvus:19530"
    mb.MILVUS_USER = "root"
    mb.MILVUS_PASSWORD = "secret"
    mb.MILVUS_TIMEOUT_SECONDS = 15
    mb.MILVUS_SECURE = True
    mb.MILVUS_CONNECT_RETRIES = 1

    from src.core.milvus_backend import MilvusBackend
    b = MilvusBackend()
    call_kwargs = FAKE.connections.connect.call_args.kwargs
    assert call_kwargs["uri"] == "http://milvus:19530"
    assert call_kwargs.get("user") == "root"
    assert call_kwargs.get("password") == "secret"
    assert call_kwargs.get("timeout") == 15
    assert call_kwargs.get("secure") is True


def test_milvus_connect_retries_on_failure(monkeypatch):
    """生产 Milvus 冷启动慢，连接失败应重试"""
    import src.core.milvus_backend as mb
    monkeypatch.setattr(mb, "MILVUS_CONNECT_RETRIES", 3)
    monkeypatch.setattr(mb, "MILVUS_URI", "http://milvus:19530")
    FAKE.connections.connect.side_effect = [RuntimeError("down"), RuntimeError("down"), None]

    from src.core.milvus_backend import MilvusBackend
    b = MilvusBackend()
    assert FAKE.connections.connect.call_count == 3


def test_milvus_connect_gives_up_after_retries(monkeypatch):
    import src.core.milvus_backend as mb
    monkeypatch.setattr(mb, "MILVUS_CONNECT_RETRIES", 2)
    monkeypatch.setattr(mb, "MILVUS_URI", "http://milvus:19530")
    FAKE.connections.connect.side_effect = RuntimeError("down")

    from src.core.milvus_backend import MilvusBackend
    with pytest.raises(RuntimeError):
        MilvusBackend()


# ======================================================================
# 2. consistency_level 一致性级别配置
# ======================================================================

def test_milvus_query_applies_consistency_level(monkeypatch):
    import src.core.milvus_backend as mb
    monkeypatch.setattr(mb, "MILVUS_CONSISTENCY_LEVEL", "Bounded")
    monkeypatch.setattr(mb, "MILVUS_URI", "http://milvus:19530")

    from src.core.milvus_backend import MilvusBackend
    b = MilvusBackend()
    # 模拟已存在 collection 且有数据
    FAKE.utility.has_collection.return_value = True
    col = MagicMock()
    col.num_entities = 5
    col.search.return_value = [["hits"]]
    hit = SimpleNamespace(distance=0.1, entity=MagicMock())
    hit.entity.get.side_effect = lambda k, default="": {
        "document": "doc1",
        "metadata": {"chunk_id": "c1"},
    }.get(k, default)
    col.search.return_value = [[hit]]
    FAKE.Collection.return_value = col

    res = b.query(query_embedding=[0.1] * 8, n_results=5, collection_name="kb1")
    assert res["documents"] == [["doc1"]]
    # search 调用必须带 consistency_level
    assert col.search.call_args.kwargs.get("consistency_level") == "Bounded"


# ======================================================================
# 3. 抽象接口行为（add / count / delete / collection 隔离）
# ======================================================================

def test_milvus_add_uses_collection_isolation(monkeypatch):
    """不同 kb 使用不同 collection，外部 chunk_id 存 metadata"""
    import src.core.milvus_backend as mb
    monkeypatch.setattr(mb, "MILVUS_URI", "http://milvus:19530")

    from src.core.milvus_backend import MilvusBackend
    b = MilvusBackend()
    col = MagicMock()
    FAKE.Collection.return_value = col

    b.add(
        ids=["c1", "c2"],
        documents=["doc1", "doc2"],
        embeddings=[[0.1] * 8, [0.2] * 8],
        metadatas=[{"source_file": "a.pdf"}, {"source_file": "b.pdf"}],
        collection_name="kb_tenantA",
    )
    # 使用 kb_tenantA collection
    assert FAKE.Collection.call_args.args[0] == "kb_tenantA"
    rows = col.insert.call_args.args[0]
    assert len(rows) == 2
    assert rows[0][3]["chunk_id"] == "c1"
    assert rows[0][3]["source_file"] == "a.pdf"


def test_milvus_count_and_delete(monkeypatch):
    import src.core.milvus_backend as mb
    monkeypatch.setattr(mb, "MILVUS_URI", "http://milvus:19530")

    from src.core.milvus_backend import MilvusBackend
    b = MilvusBackend()
    col = MagicMock()
    col.num_entities = 42
    FAKE.Collection.return_value = col

    # count
    FAKE.utility.has_collection.return_value = True
    assert b.count(collection_name="kb1") == 42

    # delete 按 chunk_id 表达式
    FAKE.utility.has_collection.return_value = True
    b.delete(ids=["c1", "c2"], collection_name="kb1")
    expr = col.delete.call_args.kwargs.get("expr")
    assert expr is not None
    assert 'metadata["chunk_id"] in ["c1","c2"]' == expr


# ======================================================================
# 4. collection 别名 — 索引重建与查询互不影响（零停机）
# ======================================================================

def test_milvus_alias_create_and_switch(monkeypatch):
    """生产建索引期间用 alias 指向新 collection，查询走 alias 不中断"""
    import src.core.milvus_backend as mb
    monkeypatch.setattr(mb, "MILVUS_URI", "http://milvus:19530")

    from src.core.milvus_backend import MilvusBackend
    b = MilvusBackend()
    b.create_alias(collection_name="kb1", alias="kb1_active")
    assert FAKE.utility.create_alias.called
    assert FAKE.utility.create_alias.call_args.kwargs.get("collection_name") == "kb1"

    b.switch_alias(collection_name="kb1_new", alias="kb1_active")
    assert FAKE.utility.alter_alias.called
    assert FAKE.utility.alter_alias.call_args.kwargs.get("collection_name") == "kb1_new"


# ======================================================================
# 5. 生产部署配置完整性
# ======================================================================

def test_production_milvus_compose_exists():
    """docker-compose.milvus.yml 必须包含 etcd / minio / milvus-standalone"""
    import os
    from pathlib import Path

    root = Path(os.getcwd())
    compose = root / "docker-compose.milvus.yml"
    assert compose.exists(), "缺少 docker-compose.milvus.yml"
    text = compose.read_text(encoding="utf-8").lower()
    assert "etcd" in text
    assert "minio" in text
    assert "milvus" in text
    # standalone 模式
    assert "standalone" in text


def test_milvus_uri_points_to_server_in_compose_env():
    """生产 compose 中 MILVUS_URI 指向 milvus 服务端而非 lite 文件"""
    import os
    from pathlib import Path

    root = Path(os.getcwd())
    compose = root / "docker-compose.milvus.yml"
    assert compose.exists()
    text = compose.read_text(encoding="utf-8")
    assert "19530" in text, "MILVUS 服务端端口 19530 必须暴露"


def test_milvus_config_keys_present():
    from src.config import (
        MILVUS_CONSISTENCY_LEVEL,
        MILVUS_CONNECT_RETRIES,
        MILVUS_PASSWORD,
        MILVUS_SECURE,
        MILVUS_TIMEOUT_SECONDS,
        MILVUS_USER,
    )
    assert isinstance(MILVUS_USER, str)
    assert isinstance(MILVUS_PASSWORD, str)
    assert MILVUS_CONNECT_RETRIES >= 1
    assert MILVUS_TIMEOUT_SECONDS > 0
    assert MILVUS_CONSISTENCY_LEVEL
