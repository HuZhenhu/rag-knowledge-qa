"""T3.4 统一 RAG 引擎接口。

三引擎收敛为统一接口（保留 langchain + agentic，删除 original）。
所有引擎实现需提供：
- 类属性 engine_name：唯一标识（"langchain" / "agentic"），用于配置切换与向量库解析；
- query(question, **kw) -> 响应对象（.answer/.sources/.usage/.timing/.trace_id）；
- query_stream(question, **kw) -> 生成器（逐块输出）。

引擎切换只改配置 RAG_ENGINE（langchain / agentic），不再有 original。
"""
from __future__ import annotations

import abc
from typing import Any, Iterator


class BaseRAGEngine(abc.ABC):
    """RAG 引擎统一抽象接口。"""

    engine_name: str = "base"

    @abc.abstractmethod
    def query(self, question: str, top_k: int | None = None, **kwargs) -> Any:
        """同步查询，返回引擎响应对象。"""

    @abc.abstractmethod
    def query_stream(self, question: str, top_k: int | None = None, **kwargs) -> Iterator[str]:
        """流式查询，逐块产出文本。"""
