"""Phase 2 T2.4 LLM 网关 — 多 provider 路由 / 限流 / 成本统计 / 故障转移 / 模型分级单测

覆盖：
- MockProvider / OpenAICompatProvider / AnthropicProvider 基础调用（mock client）
- 多 provider 顺序路由：优先第一个可用
- 故障转移：主 provider 异常自动切换备用 provider
- 全 provider 失败返回降级文案（不抛错）
- 令牌桶限流：超限降级
- 成本统计：按 provider / 租户 / 模型维度可查
- 模型分级：简单问题走小模型，复杂问题走大模型
- 分级后成本下降（简单问题命中低成本小模型）

真实 DeepSeek/OpenAI/Anthropic 为外部依赖，用 mock client 验证接口契约。
"""
from __future__ import annotations

import pytest


class _FakeChatMessage:
    def __init__(self, content):
        self.content = content


class _FakeUsage:
    def __init__(self, p=10, c=5):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.total_tokens = p + c


class _FakeOpenAIResponse:
    def __init__(self, content="openai answer", p=10, c=5):
        self.choices = [_FakeChatMessage(content)]
        self.usage = _FakeUsage(p, c)


class _FakeAnthropicResponse:
    def __init__(self, content="anthropic answer"):
        self.content = [type("Block", (), {"text": content})()]
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


# ======================================================================
# 1. Provider 基础调用
# ======================================================================

def test_mock_provider_returns_fixed_text():
    from src.core.llm_gateway import MockProvider
    p = MockProvider(name="mock", model="mock-small", response="hello")
    assert p.generate([{"role": "user", "content": "hi"}])[0] == "hello"
    assert p.name == "mock" and p.model == "mock-small"


def test_openai_provider_calls_client(monkeypatch):
    from src.core.llm_gateway import OpenAICompatProvider
    client = type("C", (), {})()
    calls = {}
    def fake_create(**kwargs):
        calls.update(kwargs)
        return _FakeOpenAIResponse("openai answer")
    client.chat = type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(fake_create)})()})()
    p = OpenAICompatProvider(name="deepseek", api_key="k", base_url="https://x", model="deepseek-chat", client=client)
    out, usage = p.generate([{"role": "user", "content": "q"}])
    assert out == "openai answer"
    assert usage["prompt_tokens"] == 10
    assert calls["model"] == "deepseek-chat"


def test_anthropic_provider_calls_client():
    from src.core.llm_gateway import AnthropicProvider
    client = type("C", (), {})()
    def fake_create(**kwargs):
        return _FakeAnthropicResponse("anthropic answer")
    client.messages = type("M", (), {"create": staticmethod(fake_create)})()
    p = AnthropicProvider(name="anthropic", api_key="k", model="claude-sonnet-4-20250514", client=client)
    out, usage = p.generate([{"role": "user", "content": "q"}])
    assert out == "anthropic answer"
    assert usage["completion_tokens"] == 5


# ======================================================================
# 2. 路由 / 故障转移 / 降级
# ======================================================================

def test_gateway_routes_to_first_available():
    from src.core.llm_gateway import LLMGateway, MockProvider
    p1 = MockProvider(name="a", model="a-model", response="from a")
    p2 = MockProvider(name="b", model="b-model", response="from b")
    g = LLMGateway(providers=[p1, p2])
    answer, meta = g.generate([{"role": "user", "content": "q"}])
    assert answer == "from a"
    assert meta["provider"] == "a"


def test_gateway_failover_on_provider_error():
    from src.core.llm_gateway import LLMGateway, MockProvider
    p1 = MockProvider(name="a", model="a-model", response="x", fail=True)
    p2 = MockProvider(name="b", model="b-model", response="from b")
    g = LLMGateway(providers=[p1, p2])
    answer, meta = g.generate([{"role": "user", "content": "q"}])
    assert answer == "from b"
    assert meta["provider"] == "b"
    assert meta["failed"] == ["a"]


def test_gateway_all_fail_returns_degraded():
    from src.core.llm_gateway import LLMGateway, MockProvider
    g = LLMGateway(providers=[
        MockProvider(name="a", model="a-model", response="x", fail=True),
        MockProvider(name="b", model="b-model", response="y", fail=True),
    ], fallback_text="知识库中未找到相关信息")
    answer, meta = g.generate([{"role": "user", "content": "q"}])
    assert answer == "知识库中未找到相关信息"
    assert meta["status"] == "degraded"


def test_gateway_rate_limit_degrades():
    from src.core.llm_gateway import LLMGateway, MockProvider
    g = LLMGateway(providers=[MockProvider(name="a", model="a-model", response="ok")],
                   rate_rpm=1)
    g.generate([{"role": "user", "content": "q"}])  # 消耗 1 令牌
    answer, meta = g.generate([{"role": "user", "content": "q2"}])  # 桶空 -> 降级
    assert meta["status"] == "degraded"
    assert meta["reason"] == "rate_limited"


# ======================================================================
# 3. 成本统计
# ======================================================================

def test_cost_tracker_records_and_queries_by_dimension():
    from src.core.llm_gateway import CostTracker
    ct = CostTracker()
    ct.record("deepseek", "t1", "deepseek-chat", 100, 50)
    ct.record("deepseek", "t1", "deepseek-chat", 100, 50)
    ct.record("deepseek", "t2", "deepseek-chat", 100, 50)
    ct.record("openai", "t1", "gpt-4o", 200, 100)

    all_ = ct.summary()
    assert all_["requests"] == 4

    by_provider = ct.summary(provider="deepseek")
    assert by_provider["requests"] == 3

    by_tenant = ct.summary(tenant_id="t1")
    assert by_tenant["requests"] == 3

    by_model = ct.summary(model="deepseek-chat")
    assert by_model["requests"] == 3

    both = ct.summary(provider="deepseek", tenant_id="t1", model="deepseek-chat")
    assert both["requests"] == 2
    assert both["prompt_tokens"] == 200


def test_gateway_records_cost_after_success():
    from src.core.llm_gateway import CostTracker, LLMGateway, MockProvider
    ct = CostTracker()
    g = LLMGateway(providers=[MockProvider(name="mock", model="mock-small", response="ok",
                                           prompt_tokens=30, completion_tokens=20)],
                   cost_tracker=ct)
    g.generate([{"role": "user", "content": "q"}], tenant_id="t1")
    s = ct.summary(provider="mock", tenant_id="t1")
    assert s["requests"] == 1
    assert s["prompt_tokens"] == 30 and s["completion_tokens"] == 20
    assert s["cost_usd"] > 0


# ======================================================================
# 4. 模型分级
# ======================================================================

def test_classify_complexity():
    from src.core.llm_gateway import LLMGateway, MockProvider
    g = LLMGateway(providers=[MockProvider(name="a", model="a-model", response="ok")])
    assert g.classify_complexity("今天天气怎么样") == "simple"
    assert g.classify_complexity("请对比 A 与 B 在三个维度的异同，并结合参考资料论证") == "complex"


def test_tiering_simple_uses_small_model():
    from src.core.llm_gateway import LLMGateway, MockProvider
    small = MockProvider(name="deepseek", model="deepseek-chat", tier="small", response="small")
    large = MockProvider(name="deepseek-reasoner", model="deepseek-reasoner", tier="large", response="large")
    g = LLMGateway(providers=[small, large])
    answer, meta = g.generate([{"role": "user", "content": "今天天气"}], complexity="simple")
    assert answer == "small"
    assert meta["model"] == "deepseek-chat"


def test_tiering_complex_uses_large_model():
    from src.core.llm_gateway import LLMGateway, MockProvider
    small = MockProvider(name="deepseek", model="deepseek-chat", tier="small", response="small")
    large = MockProvider(name="deepseek-reasoner", model="deepseek-reasoner", tier="large", response="large")
    g = LLMGateway(providers=[small, large])
    answer, meta = g.generate([{"role": "user", "content": "请对比论证"}], complexity="complex")
    assert answer == "large"
    assert meta["model"] == "deepseek-reasoner"


def test_tiering_cuts_cost_for_simple_questions():
    from src.core.llm_gateway import CostTracker, LLMGateway, MockProvider
    ct = CostTracker()
    # 小模型定价低，大模型定价高
    small = MockProvider(name="deepseek", model="deepseek-chat", tier="small", response="small",
                         prompt_tokens=10, completion_tokens=5)
    large = MockProvider(name="openai", model="gpt-4o", tier="large", response="large",
                         prompt_tokens=10, completion_tokens=5)
    g = LLMGateway(providers=[small, large], cost_tracker=ct)
    for _ in range(5):
        g.generate([{"role": "user", "content": "简单问题"}], tenant_id="t1", complexity="simple")
    s = ct.summary(tenant_id="t1")
    assert s["requests"] == 5
    assert s["cost_usd"] == pytest.approx(
        5 * (10 / 1000 * g.PROMPT_PRICE + 5 / 1000 * g.COMPLETION_PRICE), rel=1e-6
    )
    # 分级：简单问题全部走小模型，未产生大模型成本
    assert ct.summary(model="gpt-4o")["requests"] == 0
