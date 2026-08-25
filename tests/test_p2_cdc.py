"""P2-9 CDC 级增量：事件驱动同步单元测试

聚焦 _DebounceHandler（防抖窗口可配置、路径收集与透传）与
IncrementalIndexer.sync_paths（路径级增/删/改分派逻辑）。
DB/向量库/真实索引方法一律 stub，避免污染真实数据。
"""
import time
from pathlib import Path

import pytest

from src.core.incremental_indexer import IncrementalIndexer
from src.core.watcher import _DebounceHandler


# ---------- _DebounceHandler ----------

def _mk_event(kind, src_path):
    _E = type("_E", (), {"is_directory": False, "src_path": src_path})
    return _E()


class TestDebounceHandler:
    def test_debounce_seconds_configurable(self, tmp_path):
        captured = []

        def cb(paths):
            captured.append(list(paths))

        h = _DebounceHandler(cb, debounce_seconds=0.1)
        assert h._debounce_seconds == 0.1

        f = tmp_path / "a.md"
        h._handle(_mk_event("modified", str(f)))
        time.sleep(0.3)
        assert captured, "防抖窗口后应触发回调"
        assert str(f) in captured[0]

    def test_default_debounce_from_config(self):
        from src.config import WATCHER_DEBOUNCE_SECONDS
        h = _DebounceHandler(lambda paths: None)
        assert h._debounce_seconds == WATCHER_DEBOUNCE_SECONDS

    def test_filters_unsupported_and_temp(self, tmp_path):
        h = _DebounceHandler(lambda paths: None)
        # 非支持扩展名
        h._handle(_mk_event("modified", str(tmp_path / "a.tmp")))
        # 临时文件
        h._handle(_mk_event("modified", str(tmp_path / "~a.md")))
        assert not h._pending

    def test_debounce_dedup_multiple_events(self, tmp_path):
        """防抖窗口内多次事件合并为一次回调，且路径去重"""
        captured = []

        def cb(paths):
            captured.append(list(paths))

        h = _DebounceHandler(cb, debounce_seconds=0.1)
        f = tmp_path / "a.md"
        h._handle(_mk_event("modified", str(f)))
        h._handle(_mk_event("modified", str(f)))  # 同文件再次事件
        time.sleep(0.3)
        assert len(captured) == 1, "防抖窗口内多次事件只触发一次回调"
        assert captured[0].count(str(f)) == 1, "同路径应去重"


# ---------- IncrementalIndexer.sync_paths ----------

@pytest.fixture
def indexer_fixture(tmp_path, monkeypatch):
    """构造 sync_paths 测试夹具：DATA_DIR 指向 tmp、stub DB 与索引方法"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("src.config.DATA_DIR", data_dir)

    idx = IncrementalIndexer()
    calls = {"added": [], "updated": [], "deleted": []}

    monkeypatch.setattr("src.core.incremental_indexer.init_db", lambda: None)
    monkeypatch.setattr(idx, "_add_file", lambda p: calls["added"].append(str(p)))
    monkeypatch.setattr(idx, "_update_file", lambda p: calls["updated"].append(str(p)))
    monkeypatch.setattr(idx, "_delete_file", lambda rel: calls["deleted"].append(rel))
    return idx, calls, data_dir, tmp_path


class TestSyncPaths:
    def test_add_new_file(self, indexer_fixture, monkeypatch):
        idx, calls, data_dir, tmp = indexer_fixture
        monkeypatch.setattr("src.core.incremental_indexer.list_documents", lambda: [])
        f = data_dir / "new.md"
        f.write_text("hello cdc", encoding="utf-8")

        stats = idx.sync_paths([str(f)])

        assert stats["added"] == 1
        assert str(f) in calls["added"]

    def test_update_changed_file(self, indexer_fixture, monkeypatch):
        idx, calls, data_dir, tmp = indexer_fixture
        rel = str(Path("data") / "new.md")
        f = data_dir / "new.md"
        f.write_text("v1", encoding="utf-8")

        class _Rec:
            file_path = rel
            file_hash = "stale-hash-different"

        monkeypatch.setattr("src.core.incremental_indexer.list_documents",
                            lambda: [_Rec()])
        stats = idx.sync_paths([str(f)])
        assert stats["updated"] == 1
        assert str(f) in calls["updated"]

    def test_unchanged_file_skipped(self, indexer_fixture, monkeypatch):
        idx, calls, data_dir, tmp = indexer_fixture
        f = data_dir / "new.md"
        f.write_text("same", encoding="utf-8")

        class _Rec:
            file_path = f"data/new.md"

        # hash 一致：构造同 hash 记录
        import hashlib
        cur = hashlib.md5(f.read_bytes()).hexdigest()
        rec = type("_Rec", (), {"file_path": str(Path("data") / "new.md"), "file_hash": cur})()
        monkeypatch.setattr("src.core.incremental_indexer.list_documents",
                            lambda: [rec])
        stats = idx.sync_paths([str(f)])
        assert stats == {"added": 0, "updated": 0, "deleted": 0, "errors": 0}
        assert not calls["added"] and not calls["updated"]

    def test_delete_missing_file(self, indexer_fixture, monkeypatch):
        idx, calls, data_dir, tmp = indexer_fixture
        rel = str(Path("data") / "gone.md")
        f = data_dir / "gone.md"  # 不存在

        class _Rec:
            file_path = rel
            file_hash = "x"

        monkeypatch.setattr("src.core.incremental_indexer.list_documents",
                            lambda: [_Rec()])
        stats = idx.sync_paths([str(f)])
        assert stats["deleted"] == 1
        assert rel in calls["deleted"]

    def test_unsupported_extension_skipped(self, indexer_fixture, monkeypatch):
        idx, calls, data_dir, tmp = indexer_fixture
        f = data_dir / "a.xyz"
        f.write_text("x", encoding="utf-8")
        monkeypatch.setattr("src.core.incremental_indexer.list_documents", lambda: [])
        stats = idx.sync_paths([str(f)])
        assert stats == {"added": 0, "updated": 0, "deleted": 0, "errors": 0}

    def test_clear_all_caches_on_change(self, indexer_fixture, monkeypatch):
        idx, calls, data_dir, tmp = indexer_fixture
        cleared = []
        monkeypatch.setattr("src.core.incremental_indexer.list_documents", lambda: [])
        monkeypatch.setattr(
            "src.core.semantic_cache.clear_all_caches",
            lambda: cleared.append(True),
        )
        f = data_dir / "new.md"
        f.write_text("x", encoding="utf-8")
        idx.sync_paths([str(f)])
        assert cleared, "变更时应清空缓存"
