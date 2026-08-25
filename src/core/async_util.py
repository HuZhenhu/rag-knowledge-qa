"""T1.1 同步重活移出事件循环。

run_in_thread 将同步阻塞调用（RAG 检索 / LLM / 索引同步等）放入线程池执行，
避免阻塞 FastAPI 事件循环，保证并发请求不互相等待。
"""
import asyncio
from collections.abc import AsyncIterator, Callable, Generator
from typing import TypeVar

T = TypeVar("T")


async def run_in_thread(func: Callable[..., T], *args, **kwargs) -> T:
    """在线程池中执行同步函数，返回结果或抛出原异常。

    使用 asyncio.to_thread（Python 3.9+），复用事件循环默认线程池，
    无需自建 Executor，随事件循环生命周期自动管理。
    """
    return await asyncio.to_thread(func, *args, **kwargs)


async def aiter_in_thread(
    gen_fn: Callable[..., Generator],
    *args,
    **kwargs,
) -> AsyncIterator:
    """将同步生成器包装为异步迭代器，生成器在线程池中运行。

    用于 WebSocket 流式场景：LLM 流式生成在后台线程执行，产出经
    asyncio.Queue 桥接回事件循环，避免长时同步生成阻塞事件循环。
    生成器抛出的异常会以原样从异步迭代中抛出。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()

    def _run() -> None:
        try:
            for item in gen_fn(*args, **kwargs):
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:  # noqa: BLE001 - 跨线程传递原始异常
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    loop.run_in_executor(None, _run)

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            return
        if isinstance(item, Exception):
            raise item
        yield item

