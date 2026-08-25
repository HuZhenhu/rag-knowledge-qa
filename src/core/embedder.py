"""Embedding"""
try:
    from sentence_transformers import SentenceTransformer
except (ImportError, AttributeError, OSError) as _e:
    SentenceTransformer = None

from src.config import EMBEDDING_MODEL, EMBEDDING_HALF_PRECISION


class Embedder:
    """文本向量化（本地模型，支持 half 量化 + 启动预热）"""

    def __init__(self, model_name: str = EMBEDDING_MODEL,
                 half_precision: bool | None = None):
        self.model_name = model_name
        self.half_precision = EMBEDDING_HALF_PRECISION if half_precision is None else half_precision
        self.model = None

    def _load_model(self):
        """懒加载模型"""
        if self.model is None:
            if SentenceTransformer is None:
                raise ImportError("sentence-transformers 加载失败，请检查 torch 版本兼容性")
            print(f"加载Embedding模型: {self.model_name}")
            self.model = SentenceTransformer(self.model_name)
            if self.half_precision and hasattr(self.model, "half"):
                print("  Embedding 模型已切换 half 精度（fp16）")
                self.model = self.model.half()

    def warmup(self):
        """启动预热：强制加载模型并跑一次小批量 encode，消除首请求冷启动"""
        self._load_model()
        if self.model is None:
            return
        self.model.encode(["warmup"], show_progress_bar=False)

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
