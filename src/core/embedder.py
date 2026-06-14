"""Embedding"""
try:
    from sentence_transformers import SentenceTransformer
except (ImportError, AttributeError, OSError) as _e:
    SentenceTransformer = None

from src.config import EMBEDDING_MODEL


class Embedder:
    """文本向量化（本地模型）"""

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        self.model_name = model_name
        self.model = None

    def _load_model(self):
        """懒加载模型"""
        if self.model is None:
            if SentenceTransformer is None:
                raise ImportError("sentence-transformers 加载失败，请检查 torch 版本兼容性")
            print(f"加载Embedding模型: {self.model_name}")
            self.model = SentenceTransformer(self.model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """将文本列表转换为向量列表"""
        self._load_model()
        embeddings = self.model.encode(texts, show_progress_bar=True)
        return embeddings.tolist()

    def embed_single(self, text: str) -> list[float]:
        """将单个文本转换为向量"""
        self._load_model()
        embedding = self.model.encode([text])
        return embedding[0].tolist()

    def get_dimension(self) -> int:
        """获取向量维度"""
        self._load_model()
        return self.model.get_sentence_embedding_dimension()
