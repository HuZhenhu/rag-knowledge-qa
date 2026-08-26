"""T1.7 压测脚本：20 并发提问（混合简单/多跳/超纲），默认 15 分钟。

基于 pytest + httpx（不依赖 locust 服务），可作为脚本直接运行，也可被
pytest 收集其纯逻辑函数（tests/test_p1_t17_loadtest.py）。

用法：
    python scripts/loadtest.py --base-url http://127.0.0.1:8123 \
        --token <JWT> --concurrency 20 --duration 900
    # 无 token 时可用用户名/密码登录获取：
    python scripts/loadtest.py --username admin --password xxx ...

验收指标（enterprise-rag-scale-plan.md T1.7）：
    P95 延迟 < 3s，错误率 < 1%，15 分钟内存稳定。
输出：Markdown 压测报告存档到 docs/loadtest/ 下（带时间戳）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8123"
REPORT_DIR = Path(__file__).resolve().parent.parent / "docs" / "loadtest"

# ---------------------------------------------------------------------------
# 场景问题集：混合 简单 / 多跳 / 超纲（与 evaluation/ 用例风格一致）
# ---------------------------------------------------------------------------

_SIMPLE_QUESTIONS = [
    "什么是RAG？",
    "知识库支持哪些文件格式？",
    "如何配置JWT密钥？",
    "bge-m3 模型的作用是什么？",
    "系统支持哪些向量库后端？",
    "如何启动本地服务？",
    "什么是检索增强生成？",
    "上传文档的接口路径是什么？",
]

_MULTI_HOP_QUESTIONS = [
    "先说明什么是语义缓存，再解释它如何降低检索延迟？",
    "查询流程中会先做混合检索再做重排，请结合这两步说明为什么要先召回再精排？",
    "如果使用了 ACL 租户隔离，那么向量检索时需要额外带上什么过滤条件，原因是什么？",
    "上传文档后需要触发索引同步，请问这两个步骤分别由哪个接口完成，它们之间有什么关系？",
    "系统支持父子切片，请问父切片和子切片分别承担什么检索角色，为什么这样设计？",
]

_OUT_OF_SCOPE_QUESTIONS = [
    "今天北京天气怎么样？",
    "请推荐一部好看的电影。",
    "如何做一道红烧肉？",
    "明天股票会涨吗？",
    "世界上最高的山峰是哪座？请给出登顶路线攻略。",
]


def build_scenarios(
    total: int = 100,
    simple_ratio: float = 0.5,
    multi_hop_ratio: float = 0.3,
    out_of_scope_ratio: float = 0.2,
) -> list[dict]:
    """按比例生成混合问题集。比例之和应为 1.0。"""
    n_simple = int(total * simple_ratio)
    n_multi = int(total * multi_hop_ratio)
    n_ooo = total - n_simple - n_multi

    def _cycle(pool, n):
        return [pool[i % len(pool)] for i in range(n)]

    scenarios = (
        [{"kind": "simple", "question": q} for q in _cycle(_SIMPLE_QUESTIONS, n_simple)]
        + [{"kind": "multi_hop", "question": q} for q in _cycle(_MULTI_HOP_QUESTIONS, n_multi)]
        + [{"kind": "out_of_scope", "question": q} for q in _cycle(_OUT_OF_SCOPE_QUESTIONS, n_ooo)]
    )
    return scenarios


# ---------------------------------------------------------------------------
# 统计聚合
# ---------------------------------------------------------------------------

def compute_stats(latencies: list[float], errors: list[bool], wall_seconds: float) -> dict:
    """聚合延迟/错误统计。errors 与请求一一对应（True 表示失败）。"""
    count = len(latencies)
    if count == 0:
        return {"count": 0, "p95": 0.0, "mean": 0.0, "error_rate": 0.0, "qps": 0.0,
                "wall_seconds": wall_seconds}
    sorted_lat = sorted(latencies)
    # P95：取 95% 分位（上取整定位，100 样本时第 95 位 → 前 95 条 1s、后 5 条 4s 时为 4s）
    idx = min(count - 1, int((0.95 * (count - 1)) + 0.999999))
    p95 = sorted_lat[idx]
    err_rate = sum(errors) / count
    qps = count / wall_seconds if wall_seconds > 0 else 0.0
    return {
        "count": count,
        "p95": round(p95, 4),
        "mean": round(statistics.mean(latencies), 4),
        "error_rate": round(err_rate, 6),
        "qps": round(qps, 4),
        "wall_seconds": round(wall_seconds, 2),
    }


# ---------------------------------------------------------------------------
# 验收判定
# ---------------------------------------------------------------------------

def check_acceptance(stats: dict) -> tuple[bool, list[str]]:
    """按任务书验收指标判定：P95 < 3s，错误率 < 1%。返回 (是否达标, 问题列表)。"""
    problems = []
    if stats.get("count", 0) == 0:
        return False, ["无有效请求，压测未产生数据"]
    if stats["p95"] >= 3.0:
        problems.append(f"P95 延迟 {stats['p95']:.2f}s >= 3s 未达标")
    if stats["error_rate"] >= 0.01:
        problems.append(f"错误率 {stats['error_rate']*100:.2f}% >= 1% 未达标")
    return len(problems) == 0, problems


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------

def render_report(stats: dict, report_path: str) -> str:
    """渲染 Markdown 压测报告并写入 report_path。返回报告文本。"""
    ok, problems = check_acceptance(stats)
    lines = [
        "# RAG 系统并发压测报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 并发数：{stats.get('concurrency', '-')}",
        f"- 持续时长：{stats.get('wall_seconds', '-')}s",
        f"- 请求总数：{stats.get('count', '-')}",
        f"- QPS：{stats.get('qps', '-')}",
        "",
        "## 关键指标",
        "",
        "| 指标 | 实测 | 验收线 | 是否达标 |",
        "|------|------|--------|----------|",
        f"| P95 延迟 | {stats.get('p95', '-')}s | < 3s | {'是' if ok or stats.get('p95', 0) < 3 else '否'} |",
        f"| 平均延迟 | {stats.get('mean', '-')}s | - | - |",
        f"| 错误率 | {stats.get('error_rate', '-')} | < 1% | {'是' if ok or stats.get('error_rate', 0) < 0.01 else '否'} |",
        "",
    ]
    if problems:
        lines.append("## 验收结论：未达标")
        for p in problems:
            lines.append(f"- {p}")
    else:
        lines.append("## 验收结论：达标（P95 < 3s，错误率 < 1%）")
    lines.append("")
    md = "\n".join(lines)

    out = Path(report_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    return md


# ---------------------------------------------------------------------------
# 实际压测：httpx 并发请求 /query
# ---------------------------------------------------------------------------

async def _worker(client: httpx.AsyncClient, scenarios: list[dict], duration: float,
                  results: list[tuple[float, bool]], stop: asyncio.Event):
    """单个并发 worker：循环取场景问题发 /query，直到停止。"""
    i = 0
    while not stop.is_set():
        sc = scenarios[i % len(scenarios)]
        i += 1
        t0 = time.monotonic()
        try:
            r = await client.post(
                "/api/v1/query",
                json={"question": sc["question"], "top_k": 5},
            )
            ok = r.status_code == 200
        except httpx.HTTPError:
            ok = False
        except Exception:  # noqa: BLE001
            ok = False
        lat = time.monotonic() - t0
        results.append((lat, not ok))
        # 轻微退避，避免纯空转占用过多 CPU
        await asyncio.sleep(0.01)


async def _run(client: httpx.AsyncClient, scenarios, concurrency, duration,
               results: list[tuple[float, bool]]):
    stop = asyncio.Event()
    workers = [
        asyncio.create_task(_worker(client, scenarios, duration, results, stop))
        for _ in range(concurrency)
    ]
    await asyncio.sleep(duration)
    stop.set()
    await asyncio.gather(*workers, return_exceptions=True)


async def run_loadtest(base_url: str, token: str | None, username: str | None,
                       password: str | None, concurrency: int, duration: float,
                       report_path: str | None = None) -> dict:
    """执行压测并返回统计结果（含报告路径）。"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif username and password:
        async with httpx.AsyncClient(base_url=base_url) as c:
            r = await c.post("/api/v1/auth/login", json={"username": username, "password": password})
            r.raise_for_status()
            token = r.json()["access_token"]
            headers["Authorization"] = f"Bearer {token}"
    if not headers:
        raise SystemExit("缺少认证：请传 --token 或 --username/--password")

    scenarios = build_scenarios(total=30)
    results: list[tuple[float, bool]] = []
    t0 = time.monotonic()
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=60.0) as client:
        await _run(client, scenarios, concurrency, duration, results)
    wall = time.monotonic() - t0

    latencies = [r[0] for r in results]
    errors = [r[1] for r in results]
    stats = compute_stats(latencies, errors, wall)
    stats["concurrency"] = concurrency

    report_path = report_path or str(REPORT_DIR / f"loadtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    stats["report_path"] = report_path
    render_report(stats, report_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 系统并发压测（T1.7）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=None, help="JWT token（优先）")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=900.0, help="压测时长（秒），默认 900=15 分钟")
    parser.add_argument("--report", default=None, help="报告输出路径（默认 docs/loadtest/ 带时间戳）")
    args = parser.parse_args()

    stats = asyncio.run(run_loadtest(
        base_url=args.base_url, token=args.token, username=args.username,
        password=args.password, concurrency=args.concurrency,
        duration=args.duration, report_path=args.report,
    ))
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    ok, problems = check_acceptance(stats)
    print(f"\n验收结论: {'达标' if ok else '未达标'}")
    for p in problems:
        print(f"  - {p}")
    print(f"报告已存档: {stats['report_path']}")


if __name__ == "__main__":
    main()
