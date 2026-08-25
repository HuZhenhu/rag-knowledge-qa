"""T1.3 多worker+启动预热+Embedding 量化 红测试"""
from unittest.mock import MagicMock, patch


def test_config_has_t13_switches():
    from src.config import (
        EMBEDDING_HALF_PRECISION,
        RERANKER_HALF_PRECISION,
        WARMUP_ON_START,
        UVICORN_WORKERS,
    )
    assert isinstance(EMBEDDING_HALF_PRECISION, bool)
    assert isinstance(RERANKER_HALF_PRECISION, bool)
    assert isinstance(WARMUP_ON_START, bool)
    assert isinstance(UVICORN_WORKERS, int) and UVICORN_WORKERS >= 1


def test_embedder_warmup_loads_model():
    from src.core.embedder import Embedder
    fake = MagicMock()
    fake.encode.return_value = [[0.1] * 4]
    with patch("src.core.embedder.SentenceTransformer", return_value=fake):
        emb = Embedder(model_name="dummy")
        emb.warmup()
        assert emb.model is not None
        fake.encode.assert_called()


def test_embedder_half_precision_applies_half():
    from src.core.embedder import Embedder
    fake = MagicMock()
    fake.encode.return_value = [[0.1] * 4]
    with patch("src.core.embedder.SentenceTransformer", return_value=fake):
        emb = Embedder(model_name="dummy", half_precision=True)
        emb.warmup()
        fake.half.assert_called()


def test_embedder_half_off_skips_half():
    from src.core.embedder import Embedder
    fake = MagicMock()
    fake.encode.return_value = [[0.1] * 4]
    with patch("src.core.embedder.SentenceTransformer", return_value=fake):
        emb = Embedder(model_name="dummy", half_precision=False)
        emb.warmup()
        fake.half.assert_not_called()


def test_reranker_warmup_loads_model():
    from src.core.reranker import Reranker
    fake = MagicMock()
    fake.predict.return_value = [0.5]
    with patch("src.core.reranker.CrossEncoder", return_value=fake):
        rk = Reranker(model_name="dummy")
        rk.warmup()
        assert rk.model is not None
        fake.predict.assert_called()


def test_main_exposes_warmup_models():
    import main
    assert callable(getattr(main, "warmup_models", None))


def test_main_wires_lifespan_and_workers():
    import main
    src = open(main.__file__, encoding="utf-8").read()
    assert "lifespan=" in src
    assert "workers=" in src


def test_dockerfile_has_4_workers():
    src = open("Dockerfile", encoding="utf-8").read()
    assert "--workers" in src
    assert "4" in src
