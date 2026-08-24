"""pytest fixtures for shared test configuration"""
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_chroma_system_cache():
    """测试后清理 chromadb SharedSystemClient 单例缓存。

    修复全量 pytest 下 Chroma 状态污染 flaky：同一进程内多个测试以不同
    settings 创建 PersistentClient 时，chromadb 单例会抛
    "An instance of Chroma already exists ... with different settings"。
    """
    yield
    try:
        from chromadb.api.client import SharedSystemClient
        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


@pytest.fixture
def mock_llm():
    """Mock openai.OpenAI，返回固定响应。

    Usage in test::

        def test_something(mock_llm):
            gen = Generator()
            # mock_llm is already patched, gen.generate() will use it
    """
    mock_response = Mock()
    mock_response.choices = [
        Mock(message=Mock(content="这是基于知识库的测试回答[1]。"))
    ]
    mock_response.usage = Mock(
        prompt_tokens=50,
        completion_tokens=30,
        total_tokens=80,
    )

    mock_client = Mock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("openai.OpenAI", return_value=mock_client):
        yield mock_client
