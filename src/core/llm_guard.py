"""T1.5 有界任务队列 + DeepSeek 令牌桶限流 + 指数退避重试 + 熔断降级

设计：
- TokenBucket: 令牌桶限流（线程安全，时钟可注入，支持阻塞等待与超时）
- ExponentialBackoff: 指数退避重试（可筛选重试异常类型）
- CircuitBreaker: 熔断器（closed -> open -> half_open -> closed，探测放行）
- BoundedTaskQueue: 有界任务队列（满则拒绝，上层转"排队中"）
- LLMGuard: 组合防护。submit() 顺序：队列检查 -> 令牌桶限流 -> 熔断检查 -> 重试执行。
  队列满返回 status="queued"；限流超时 / 熔断打开 / 执行失败返回 status="degraded" 降级文案，
  不向上抛错（保证 DeepSeek 限流/故障不导致错误率上升）。
"""
import threading
import time
from dataclasses import dataclass


class QueueFullError(Exception):
    """有界任务队列已满"""


class CircuitOpenError(Exception):
    """熔断器打开，快速失败"""


# ======================================================================
# TokenBucket 令牌桶限流
# ======================================================================

class TokenBucket:
    """令牌桶限流器。

    - capacity: 桶容量（最大突发）
    - refill_rate: 每秒补充令牌数
    - clock: 可注入时钟（测试用），默认 time.monotonic
    """

    def __init__(self, capacity: float, refill_rate: float, clock=time.monotonic):
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._clock = clock
        self._available = float(capacity)
        self._last = clock()
        self._lock = threading.Lock()

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._available

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._available = min(self.capacity, self._available + elapsed * self.refill_rate)
        self._last = now

    def acquire(self, tokens: float = 1.0) -> bool:
        """非阻塞尝试获取 tokens。够则扣减返回 True，否则返回 False。"""
        with self._lock:
            self._refill()
            if self._available >= tokens:
                self._available -= tokens
                return True
            return False

    def wait_acquire(self, tokens: float = 1.0, timeout: float | None = None,
                     poll_interval: float = 0.01, _sleep=time.sleep) -> bool:
        """阻塞等待直到拿到令牌或超时。

        Args:
            timeout: 最长等待秒数（None 表示一直等）
            poll_interval: 轮询间隔
            _sleep: 可注入的等待函数（测试用推进假时钟）
        """
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            if self.acquire(tokens):
                return True
            if deadline is not None and self._clock() >= deadline:
                return False
            _sleep(poll_interval)


# ======================================================================
# ExponentialBackoff 指数退避重试
# ======================================================================

class ExponentialBackoff:
    """指数退避重试执行器。delay(attempt) = min(base_delay * 2**attempt, max_delay)。"""

    def __init__(self, max_retries: int = 3, base_delay: float = 0.5,
                 max_delay: float = 8.0, clock=time.monotonic):
        self.max_retries = int(max_retries)
        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self._clock = clock

    def delay(self, attempt: int) -> float:
        return min(self.base_delay * (2 ** max(0, attempt)), self.max_delay)

    def execute(self, fn, *args, retryable=None, _sleep=time.sleep, **kwargs):
        """执行 fn，瞬时失败按指数退避重试。

        Args:
            fn: 目标调用（callable）
            retryable: 异常判定函数，接收异常返回 True（可重试）/ False（不重试）。
                       None 表示所有异常都可重试。
            _sleep: 可注入等待函数

        Returns:
            fn 的返回值

        Raises:
            最后一次尝试抛出的异常（重试耗尽后）
        """
        attempt = 0
        while True:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if retryable is not None and not retryable(e):
                    raise
                if attempt >= self.max_retries:
                    raise
                _sleep(self.delay(attempt))
                attempt += 1


# ======================================================================
# CircuitBreaker 熔断器
# ======================================================================

class CircuitBreaker:
    """熔断器：closed -> open -> half_open -> closed。

    - failure_threshold: 连续失败次数达到即打开
    - reset_timeout: 打开后经过该时长进入 half_open，放行一个探测请求
    - 探测成功 -> closed 复位；探测失败 -> 重新 open
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 30.0,
                 clock=time.monotonic):
        self.failure_threshold = int(failure_threshold)
        self.reset_timeout = float(reset_timeout)
        self._clock = clock
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._probe_inflight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            self._refresh_state()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def _refresh_state(self) -> None:
        """open 状态超过 reset_timeout 后转入 half_open。"""
        if self._state == self.OPEN and self._clock() - self._opened_at >= self.reset_timeout:
            self._state = self.HALF_OPEN
            self._probe_inflight = False

    def allow_request(self) -> bool:
        """是否放行请求。half_open 只放行一个探测请求。"""
        with self._lock:
            self._refresh_state()
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                return False
            # half_open：只放行一个探测
            if self._probe_inflight:
                return False
            self._probe_inflight = True
            return True

    def record_failure(self) -> None:
        with self._lock:
            self._refresh_state()
            if self._state == self.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._state = self.OPEN
                    self._opened_at = self._clock()
            elif self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._opened_at = self._clock()
                self._probe_inflight = False
            # OPEN 状态下持续失败保持打开

    def record_success(self) -> None:
        with self._lock:
            self._refresh_state()
            if self._state == self.HALF_OPEN:
                self._state = self.CLOSED
                self._probe_inflight = False
                self._failure_count = 0
            elif self._state == self.CLOSED:
                self._failure_count = 0

    def call(self, fn, *args, **kwargs):
        """受熔断保护的调用：open 时抛 CircuitOpenError，不执行 fn。"""
        if not self.allow_request():
            raise CircuitOpenError("circuit breaker open")
        try:
            return fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise


# ======================================================================
# BoundedTaskQueue 有界任务队列
# ======================================================================

class BoundedTaskQueue:
    """有界任务队列（线程安全）。

    - submit: 有可用槽位则占用并返回 True；满则返回 False（上层转"排队中"，不挂死）
    - done: 任务完成后释放槽位
    """

    def __init__(self, maxsize: int = 100):
        self.maxsize = int(maxsize)
        self._sem = threading.BoundedSemaphore(self.maxsize)
        self._pending = 0
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def full(self) -> bool:
        return self.pending >= self.maxsize

    def submit(self, job) -> bool:
        if not self._sem.acquire(blocking=False):
            return False
        with self._lock:
            self._pending += 1
        return True

    def done(self) -> None:
        with self._lock:
            if self._pending <= 0:
                return
            self._pending -= 1
        self._sem.release()


# ======================================================================
# LLMGuard 组合防护
# ======================================================================

@dataclass
class GuardResult:
    """submit 的返回：result 为执行结果或降级文案，status 为 ok/queued/degraded。"""
    result: object
    status: str = "ok"


class LLMGuard:
    """组合防护：有界队列(排队中) -> 令牌桶限流 -> 熔断快速失败 -> 指数退避重试。

    submit() 返回 (result, status)：
        - status="ok":      正常执行完成，result 为 fn 返回值
        - status="queued":  任务队列已满，未执行（上层可返回"排队中"）
        - status="degraded": 限流超时 / 熔断打开 / 执行失败，result 为降级文案
    """

    def __init__(self, bucket: TokenBucket, backoff: ExponentialBackoff,
                 breaker: CircuitBreaker, queue: BoundedTaskQueue,
                 degraded_response: str = "知识库中未找到相关信息",
                 enabled: bool = True):
        self.bucket = bucket
        self.backoff = backoff
        self.breaker = breaker
        self.queue = queue
        self.degraded_response = degraded_response
        self.enabled = bool(enabled)

    def _retryable(self, e: Exception) -> bool:
        """瞬时错误可重试：超时 / 上游 5xx / 网络类异常。"""
        return isinstance(e, (TimeoutError, ConnectionError, OSError)) or (
            "429" in str(e) or "5" in str(getattr(e, "status_code", ""))
        )

    def submit(self, fn, *args, wait_timeout: float = 30.0, wait_poll: float = 0.05,
               _sleep=time.sleep, **kwargs):
        """执行受保护调用，返回 (result, status)。"""
        if not self.enabled:
            return (fn(*args, **kwargs), "ok")

        if not self.queue.submit("task"):
            return (None, "queued")
        try:
            # 1) 令牌桶限流：拿不到令牌且超时 -> 降级（不产生错误）
            if not self.bucket.wait_acquire(1.0, timeout=wait_timeout,
                                            poll_interval=wait_poll, _sleep=_sleep):
                return (self.degraded_response, "degraded")
            # 2) 熔断检查：open 快速失败 -> 降级
            if not self.breaker.allow_request():
                return (self.degraded_response, "degraded")
            # 3) 指数退避重试执行
            try:
                result = self.backoff.execute(fn, *args, retryable=self._retryable,
                                              _sleep=_sleep, **kwargs)
            except Exception:
                self.breaker.record_failure()
                return (self.degraded_response, "degraded")
            self.breaker.record_success()
            return (result, "ok")
        finally:
            self.queue.done()


# 进程级默认防护实例（按需由 config 开关启用；默认 enabled=False 保持现行为）
_LLM_GUARD: LLMGuard | None = None


def guarded_llm_invoke(guard, fn, fallback="知识库中未找到相关信息"):
    """受 guard 保护的 LLM 调用。

    Args:
        guard: LLMGuard 实例或 None（None 时直接执行，保持现行为）
        fn: 实际 LLM 调用（callable，无参）
        fallback: status 为 queued/degraded 时返回的降级文案

    Returns:
        fn 的返回值；限流排队/熔断降级时返回 fallback，不向上抛错。
    """
    if guard is None:
        return fn()
    result, status = guard.submit(fn)
    if status != "ok":
        return fallback
    return result


def get_llm_guard(enabled: bool = True) -> LLMGuard | None:
    """进程内 LLMGuard 单例。enabled=False 时返回 None（上层跳过防护，保持现行为）。"""
    global _LLM_GUARD
    if not enabled:
        return None
    if _LLM_GUARD is None:
        from src.config import (
            LLM_CIRCUIT_FAILURE_THRESHOLD,
            LLM_CIRCUIT_RESET_TIMEOUT,
            LLM_QUEUE_MAXSIZE,
            LLM_RATE_LIMIT_CAPACITY,
            LLM_RATE_LIMIT_RPM,
            LLM_RETRY_BASE_DELAY,
            LLM_RETRY_MAX,
        )
        _LLM_GUARD = LLMGuard(
            bucket=TokenBucket(capacity=LLM_RATE_LIMIT_CAPACITY,
                               refill_rate=LLM_RATE_LIMIT_RPM / 60.0),
            backoff=ExponentialBackoff(max_retries=LLM_RETRY_MAX,
                                       base_delay=LLM_RETRY_BASE_DELAY),
            breaker=CircuitBreaker(failure_threshold=LLM_CIRCUIT_FAILURE_THRESHOLD,
                                   reset_timeout=LLM_CIRCUIT_RESET_TIMEOUT),
            queue=BoundedTaskQueue(maxsize=LLM_QUEUE_MAXSIZE),
        )
    return _LLM_GUARD
