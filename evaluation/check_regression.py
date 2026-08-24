"""P1-5 CI 回归检查脚本

对比当前 eval_baseline.py 聚合结果与 golden 基线，超容差则退出非 0（阻断 PR/提交）。
- 精度类指标（context_recall/context_precision/hit_at_k/chunk_hit_at_k/mrr）：
  当前值 < golden 值 - metric_tol 即失败。
- 延迟（avg_time_s）：默认仅告警不阻断（CI 环境时间受机器/网络影响不可靠）；
  显式传 --check-time 才阻断。
"""
import argparse
import json
import os
import sys

_METRICS = ("context_recall", "context_precision", "hit_at_k", "chunk_hit_at_k", "mrr")
_DEFAULT_GOLDEN = os.path.join(os.path.dirname(__file__), "golden_baseline.json")


def main() -> int:
    ap = argparse.ArgumentParser(description="对比当前评测结果与 golden 基线，超容差退出非 0")
    ap.add_argument("--current", required=True, help="eval_baseline.py 输出的 JSON 路径")
    ap.add_argument("--golden", default=_DEFAULT_GOLDEN, help="golden 基线 JSON 路径")
    ap.add_argument("--metric-tol", type=float, default=0.02,
                    help="精度指标允许下降幅度（绝对值），默认 0.02")
    ap.add_argument("--time-ratio", type=float, default=1.25,
                    help="延迟劣化允许倍数，默认 1.25 倍（仅告警）")
    ap.add_argument("--check-time", action="store_true", help="延迟超限也作为阻断条件")
    args = ap.parse_args()

    with open(args.current, encoding="utf-8") as f:
        cur = json.load(f)
    if not cur.get("aggregates"):
        print("[check_regression] FAIL: current 无 aggregates 字段", file=sys.stderr)
        return 2
    agg = cur["aggregates"][-1]  # 单引擎运行时仅一项

    with open(args.golden, encoding="utf-8") as f:
        golden = json.load(f)

    failed: list[str] = []
    warns: list[str] = []

    for m in _METRICS:
        g = golden.get(m)
        if g is None:
            continue
        c = agg.get(m, 0.0)
        if c < g - args.metric_tol:
            failed.append(f"{m}: current={c:.4f} < golden={g:.4f} - tol={args.metric_tol}")

    g_time = golden.get("avg_time_s") or 0.0
    c_time = agg.get("avg_time_s") or 0.0
    if g_time > 0 and c_time > g_time * args.time_ratio:
        msg = (f"avg_time_s: current={c_time:.2f}s > golden={g_time:.2f}s "
               f"* {args.time_ratio}")
        if args.check_time:
            failed.append(msg)
        else:
            warns.append(msg)

    for w in warns:
        print(f"[check_regression] WARN: {w}")
    if failed:
        print("[check_regression] FAIL")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("[check_regression] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
