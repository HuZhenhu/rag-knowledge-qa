"""文件监听器 — 监听 data/ 目录变化，自动触发增量索引"""
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from src.config import DATA_DIR, WATCHER_DEBOUNCE_SECONDS

logger = logging.getLogger(__name__)

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {".md", ".txt", ".docx", ".pdf", ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".bmp"}


class _DebounceHandler(FileSystemEventHandler):
    """防抖动文件事件处理器

    短时间内多次写入同一个文件只触发一次处理（等 DEBOUNCE_SECONDS，P2-9 起
    该窗口可通过环境变量 WATCHER_DEBOUNCE_SECONDS 配置，默认 5 秒）。
    """

    DEBOUNCE_SECONDS = WATCHER_DEBOUNCE_SECONDS

    def __init__(self, on_change_callback, debounce_seconds: float | None = None):
        super().__init__()
        self._callback = on_change_callback
        self._debounce_seconds = debounce_seconds if debounce_seconds is not None else WATCHER_DEBOUNCE_SECONDS
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def on_created(self, event):
        self._handle(event)

    def on_modified(self, event):
        self._handle(event)

    def on_deleted(self, event):
        self._handle(event)

    def _handle(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        # 过滤：只处理支持的格式
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        # 过滤：忽略临时文件（~开头、.tmp/.swp 结尾）
        if path.name.startswith("~") or path.name.startswith(".") or path.suffix in {".tmp", ".swp"}:
            return

        with self._lock:
            self._pending.add(str(path))
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self):
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
            self._timer = None

        if paths:
            logger.info("检测到 %d 个文件变化，开始增量索引", len(paths))
            try:
                self._callback(paths)
            except Exception:
                logger.exception("增量索引失败")


class FileWatcher:
    """文件监听器：监听 data/ 目录，变化时自动增量索引"""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self._observer: Observer | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        """启动监听"""
        if self._running:
            logger.warning("文件监听器已在运行")
            return

        handler = _DebounceHandler(self._on_change)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.data_dir), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        self._running = True
        logger.info("文件监听器已启动，监听目录: %s", self.data_dir)

    def stop(self):
        """停止监听"""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._running = False
        logger.info("文件监听器已停止")

    def _on_change(self, paths: list[str]):
        """文件变化回调：执行路径级增量索引，并通过 WebSocket 广播结果

        P2-9: 事件驱动同步——只对事件给出的具体路径做 hash 对比与增/删/改，
        避免全目录重扫，实现亚分钟级响应。
        """
        import asyncio
        from src.core.incremental_indexer import IncrementalIndexer
        from src.core.data_monitor import get_data_monitor

        indexer = IncrementalIndexer()
        monitor = get_data_monitor()

        # 广播：索引开始
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(monitor.on_file_change(paths, "indexing"))
        except RuntimeError:
            pass

        stats = indexer.sync_paths(paths)
        logger.info("增量索引完成: %s", stats)

        # 广播：索引完成
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(monitor.on_index_complete(stats))
        except RuntimeError:
            pass


# 全局单例
_watcher: FileWatcher | None = None
_watcher_lock = threading.Lock()


def get_watcher() -> FileWatcher:
    """获取全局文件监听器实例"""
    global _watcher
    if _watcher is None:
        with _watcher_lock:
            if _watcher is None:
                _watcher = FileWatcher()
    return _watcher
