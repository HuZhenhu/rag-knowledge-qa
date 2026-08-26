"""T1.5 有界任务队列 + 令牌桶限流 + 指数退避重试 + 熔断降级 单元测试（红）

验收映射：
- 超限请求返回"排队中"而非挂死 -> BoundedTaskQueue 满时 LLMGuard.submit 返回 status="queued"
- DeepSeek 限流不导致错误率 > 1% -> 令牌桶超时 / 熔断打开返回 status="degraded"（降级文案而非抛错）
"""
import time

import pytest

from src.core.llm_guard import (
    BoundedTaskQueue,
    CircuitBreaker,
    CircuitOpenError,
    ExponentialBackoff,
    LLMGuard,
    QueueFullError,
    TokenBucket,
    guarded_llm_invoke,
)


class FakeClock:
    """可注入时钟：手动推进时间，避免真实 sleep"""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


# ======================================================================
# TokenBucket 令牌桶
# ======================================================================

def test_token_bucket_initial_capacity():
    bucket = TokenBucket(capacity=10.0, refill_rate=1.0, clock=FakeClock())
    assert bucket.available == pytest.approx(10.0)
    assert bucket.acquire(10.0) is True


def test_token_bucket_acquire_drains_until_empty():
    bucket = TokenBucket(capacity=2.0, refill_rate=1.0, clock=FakeClock())
    assert bucket.acquire(1.0) is True
    assert bucket.acquire(1.0) is True
    assert bucket.acquire(1.0) is False  # 令牌耗尽，非阻塞返回 False


def test_token_bucket_refill_over_time():
    clock = FakeClock()
    bucket = TokenBucket(capacity=5.0, refill_rate=2.0, clock=clock)
    bucket.acquire(5.0)
    assert bucket.available == 0.0
    clock.advance(1.0)  # 1 秒后补充 2 个令牌
    assert bucket.available == pytest.approx(2.0)


def test_token_bucket_wait_acquire_blocks_then_succeeds():
    clock = FakeClock()
    bucket = TokenBucket(capacity=1.0, refill_rate=1.0, clock=clock)
    bucket.acquire(1.0)
    # 无令牌时 wait_acquire 轮询等待：推进时间后应成功
    assert bucket.wait_acquire(1.0, timeout=5.0, poll_interval=0.01, _sleep=clock.advance) is True


def test_token_bucket_wait_acquire_timeout_returns_false():
    clock = FakeClock()
    bucket = TokenBucket(capacity=1.0, refill_rate=0.0, clock=clock)  # 永不补充
    bucket.acquire(1.0)
    assert bucket.wait_acquire(1.0, timeout=1.0, poll_interval=0.01, _sleep=clock.advance) is False


# ======================================================================
# ExponentialBackoff 指数退避重试
# ======================================================================

def test_backoff_delay_grows_exponentially():
    backoff = ExponentialBackoff(max_retries=4, base_delay=0.1, max_delay=1.6)
    delays = [backoff.delay(attempt) for attempt in range(4)]
    # 0.1 -> 0.2 -> 0.4 -> 0.8（指数增长）
    assert delays == pytest.approx([0.1, 0.2, 0.4, 0.8])


def test_backoff_delay_capped_at_max():
    backoff = ExponentialBackoff(max_retries=10, base_delay=1.0, max_delay=4.0)
    assert backoff.delay(5) == pytest.approx(4.0)  # 封顶


def test_backoff_execute_success_first_try():
    calls = []
    backoff = ExponentialBackoff(max_retries=3, base_delay=0.01, max_delay=0.05)

    def ok():
        calls.append(1)
        return "ok"

    assert backoff.execute(ok) == "ok"
    assert len(calls) == 1  # 一次成功即停


def test_backoff_execute_retries_then_succeeds():
    calls = []
    backoff = ExponentialBackoff(max_retries=3, base_delay=0.01, max_delay=0.05)

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("upstream timeout")
        return "recovered"

    assert backoff.execute(flaky) == "recovered"
    assert len(calls) == 3


def test_backoff_execute_raises_after_max_retries():
    calls = []
    backoff = ExponentialBackoff(max_retries=3, base_delay=0.01, max_delay=0.05)

    def always_fail():
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        backoff.execute(always_fail)
    assert len(calls) == 4  # 1 次原始 + 3 次重试


def test_backoff_retryable_filter():
    """非瞬时异常（如业务错误）不重试"""
    calls = []

    def retryable(e):
        return isinstance(e, TimeoutError)

    backoff = ExponentialBackoff(max_retries=3, base_delay=0.01, max_delay=0.05)

    def business_error():
        calls.append(1)
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        backoff.execute(business_error, retryable=retryable)
    assert len(calls) == 1  # 未重试


# ======================================================================
# CircuitBreaker 熔断器
# ======================================================================

def test_circuit_closed_by_default():
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=30.0, clock=FakeClock())
    assert breaker.state == "closed"
    assert breaker.allow_request() is True


def test_circuit_trips_open_after_failures():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=30.0, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow_request() is False  # 快速失败


def test_circuit_stays_closed_below_threshold():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=30.0, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"


def test_circuit_half_open_after_reset_timeout():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=10.0, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    clock.advance(10.0)
    assert breaker.state == "half_open"
    assert breaker.allow_request() is True  # 放行一个探测请求


def test_circuit_success_in_half_open_resets_to_closed():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=10.0, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(10.0)
    breaker.record_success()  # 半开状态探测成功
    assert breaker.state == "closed"
    assert breaker.allow_request() is True


def test_circuit_failure_in_half_open_reopens():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=10.0, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(10.0)
    assert breaker.state == "half_open"
    breaker.record_failure()  # 半开探测失败 -> 重新打开
    assert breaker.state == "open"


def test_circuit_open_raises_circuit_open_error_on_call():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=10.0, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "never")
    # 打开状态不执行目标函数
    assert breaker.failure_count == 2


# ======================================================================
# BoundedTaskQueue 有界任务队列
# ======================================================================

def test_bounded_queue_accepts_until_max():
    q = BoundedTaskQueue(maxsize=2)
    assert q.submit("job1") is True
    assert q.submit("job2") is True
    assert q.full() is True


def test_bounded_queue_full_rejects():
    q = BoundedTaskQueue(maxsize=1)
    q.submit("job1")
    assert q.full() is True
    assert q.submit("job2") is False  # 超限拒绝，不挂死


def test_bounded_queue_release_then_accept():
    q = BoundedTaskQueue(maxsize=1)
    q.submit("job1")
    q.done()
    assert q.submit("job2") is True


# ======================================================================
# LLMGuard 组合防护
# ======================================================================

def make_guard(clock, *, queue_max=10, capacity=5.0, refill=5.0,
               retries=2, fail_threshold=3, reset_timeout=30.0,
               degraded="知识库中未找到相关信息"):
    bucket = TokenBucket(capacity=capacity, refill_rate=refill, clock=clock)
    backoff = ExponentialBackoff(max_retries=retries, base_delay=0.01, max_delay=0.05, clock=clock)
    breaker = CircuitBreaker(failure_threshold=fail_threshold, reset_timeout=reset_timeout, clock=clock)
    queue = BoundedTaskQueue(maxsize=queue_max)
    guard = LLMGuard(bucket=bucket, backoff=backoff, breaker=breaker, queue=queue,
                     degraded_response=degraded)
    return guard


def test_guard_returns_ok_on_success():
    clock = FakeClock()
    guard = make_guard(clock)

    def fn():
        return {"answer": "hello"}

    result, status = guard.submit(fn)
    assert status == "ok"
    assert result["answer"] == "hello"


def test_guard_queue_full_returns_queued():
    """超限请求返回排队中而非挂死（T1.5 验收）"""
    clock = FakeClock()
    guard = make_guard(clock, queue_max=1)
    guard.queue.submit("busy")  # 占满队列
    assert guard.queue.full() is True

    def fn():
        return {"answer": "x"}

    result, status = guard.submit(fn)
    assert status == "queued"
    assert result is None


def test_guard_retries_transient_error_then_ok():
    clock = FakeClock()
    guard = make_guard(clock, retries=2)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise TimeoutError("upstream timeout")
        return {"answer": "recovered"}

    result, status = guard.submit(flaky)
    assert status == "ok"
    assert result["answer"] == "recovered"
    assert len(calls) == 2


def test_guard_circuit_breaks_and_degrades_after_repeated_failures():
    """连续失败达阈值 -> 熔断打开 -> 后续请求返回降级响应而非错误（T1.5 验收）"""
    clock = FakeClock()
    guard = make_guard(clock, retries=0, fail_threshold=3)

    def failing():
        raise RuntimeError("deepseek down")

    # 前 3 次失败触发熔断
    for _ in range(3):
        result, status = guard.submit(failing)
        assert status == "degraded"

    assert guard.breaker.state == "open"

    # 熔断打开后：不调用目标函数，直接降级返回
    calls = []
    result, status = guard.submit(lambda: calls.append(1) or {"answer": "never"})
    assert status == "degraded"
    assert result == "知识库中未找到相关信息"
    assert len(calls) == 0  # 目标函数未被调用


def test_guard_token_bucket_exhausted_wait_then_degrade():
    """令牌桶无令牌且等待超时 -> 返回降级响应，不产生错误（限流不导致错误率>1%）"""
    clock = FakeClock()
    guard = make_guard(clock, capacity=1.0, refill=0.0)  # 永不补充
    guard.bucket.acquire(1.0)  # 耗尽令牌

    def fn():
        return {"answer": "x"}

    result, status = guard.submit(fn, wait_timeout=0.1, wait_poll=0.01, _sleep=clock.advance)
    assert status == "degraded"
    assert result == "知识库中未找到相关信息"


def test_guard_recovers_after_circuit_reset_timeout():
    """熔断半开探测成功 -> 恢复 normal 服务"""
    clock = FakeClock()
    guard = make_guard(clock, retries=0, fail_threshold=2, reset_timeout=10.0)

    def failing():
        raise RuntimeError("down")

    for _ in range(2):
        guard.submit(failing)
    assert guard.breaker.state == "open"

    clock.advance(10.0)  # 重置窗口过后进入 half_open

    def ok():
        return {"answer": "back"}

    result, status = guard.submit(ok)
    assert status == "ok"
    assert guard.breaker.state == "closed"


# ======================================================================
# guarded_llm_invoke 集成辅助（LLM 调用点薄包装）
# ======================================================================

def test_guarded_invoke_none_guard_runs_directly():
    """guard 为 None（开关关闭）时直接执行，保持现行为"""
    calls = []

    def fn():
        calls.append(1)
        return "real"

    assert guarded_llm_invoke(None, fn) == "real"
    assert len(calls) == 1


def test_guarded_invoke_ok_returns_result():
    clock = FakeClock()
    guard = make_guard(clock)

    def fn():
        return "real-answer"

    assert guarded_llm_invoke(guard, fn) == "real-answer"


def test_guarded_invoke_queued_returns_fallback():
    """队列满 -> 排队中 -> 返回降级文案而非错误"""
    clock = FakeClock()
    guard = make_guard(clock, queue_max=1)
    guard.queue.submit("busy")

    def fn():
        raise AssertionError("不应被调用")

    assert guarded_llm_invoke(guard, fn) == "知识库中未找到相关信息"


def test_guarded_invoke_circuit_open_returns_fallback():
    """熔断打开 -> 快速失败 -> 返回降级文案，目标函数不被调用"""
    clock = FakeClock()
    guard = make_guard(clock, retries=0, fail_threshold=2)
    guard.breaker.record_failure()
    guard.breaker.record_failure()
    assert guard.breaker.state == "open"

    calls = []

    def fn():
        calls.append(1)
        return "never"

    assert guarded_llm_invoke(guard, fn) == "知识库中未找到相关信息"
    assert len(calls) == 0
