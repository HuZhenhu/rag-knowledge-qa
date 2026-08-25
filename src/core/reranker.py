"""ReRanker重排序"""
from dataclasses import dataclass, field
from typing import Any

from src.config import RERANKER_MODEL
from sentence_transformers import CrossEncoder


@dataclass
class RerankResult:
    """重排序结果"""
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class Reranker:
    """CrossEncoder重排序器"""

    def __init__(self, model_name: str = RERANKER_MODEL):
        self.model_name = model_name
        self.model = None

    def _load_model(self):
        """懒加载模型"""
        if self.model is None:
            print(f"加载ReRanker模型: {self.model_name}")
            self.model = CrossEncoder(self.model_name)

    def warmup(self):
        """启动预热：强制加载模型并跑一次预测，消除首请求冷启动"""
        self._load_model()
        if self.model is None:
            return
        self.model.predict([("warmup", "warmup")])

    def rerank(
        self,
        query: str,
        documents: list[dict],
        top_k: int = 5
    ) -> list[RerankResult]:
        """对检索结果重新排序"""
        if not documents:
            return []

        self._load_model()

        # 构建(query, document)对
        pairs = [(query, doc["content"]) for doc in documents]

        # 计算相关性分数
        scores = self.model.predict(pairs)

        # 组装结果并排序
        results = []
        for i, (doc, score) in enumerate(zip(documents, scores)):
            results.append(RerankResult(
                content=doc["content"],
                metadata=doc.get("metadata", {}),
                score=float(score)
            ))

        # 按分数降序排序
        results.sort(key=lambda x: x.score, reverse=True)

        return results[:top_k]
