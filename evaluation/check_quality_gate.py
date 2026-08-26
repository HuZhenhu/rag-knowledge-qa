"""T3.3 发布质量门禁 CLI — CI / 部署脚本调用

用法：
    python evaluation/check_quality_gate.py --current <本次评测JSON> \
        [--baseline <上次评测JSON>] [--min-accuracy 0.6] \
        [--drop-threshold 0.05] [--feedback-bad-ratio 0.5]

退出码：
    0  = 通过（可发布）
    1  = 未通过（阻断发布）
    2  = 参数/文件错误

与 src.core.eval_feedback.quality_gate 共用同一套规则。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.eval_feedback import quality_gate  # noqa: E402


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"评测文件应为 JSON 对象: {path}")
    return data


def normalize_accuracy(d: dict) -> dict:
    """把两类评测文件结构归一化为 quality_gate 可读的摘要

    - evaluate.py 汇总：已含 answer_accuracy
    - eval_baseline.py baseline_*.json：取 optimized 引擎的 context_recall
      （检索质量代理指标，用于发布门禁）
    """
    if "answer_accuracy" in d:
        return d
    aggs = d.get("aggregates") or []
    if not aggs:
        return {"answer_accuracy": 0.0, "results": []}
    agg = next((a for a in aggs if a.get("engine") == "optimized"), aggs[0])
    metric = agg.get("context_recall", agg.get("hit_at_k", 0.0))
    return {"answer_accuracy": float(metric or 0.0), "results": []}


def main() -> int:
    parser = argparse.ArgumentParser(description="发布质量门禁检查")
    parser.add_argument("--current", required=True, help="本次评测汇总 JSON")
    parser.add_argument("--baseline", default=None, help="上次评测汇总 JSON（可选）")
    parser.add_argument("--min-accuracy", type=float, default=None)
    parser.add_argument("--drop-threshold", type=float, default=None)
    parser.add_argument("--feedback-bad-ratio", type=float, default=None)
    args = parser.parse_args()

    try:
        current = load_json(args.current)
        baseline = load_json(args.baseline) if args.baseline else None
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    gate = quality_gate(
        normalize_accuracy(current),
        baseline=normalize_accuracy(baseline) if baseline else None,
        drop_threshold=args.drop_threshold,
        min_accuracy=args.min_accuracy,
        feedback_bad_ratio_limit=args.feedback_bad_ratio,
    )

    print(
        f"质量门禁: {'通过' if gate['ok'] else '未通过'} | "
        f"本次准确率 {gate['current_accuracy']:.1%}"
    )
    if gate["baseline_accuracy"] is not None:
        print(f"  上次准确率 {gate['baseline_accuracy']:.1%} | 下降 {gate['drop']:.1%}")
    print(f"  反馈兜底比例 {gate['feedback_bad_ratio']:.1%}")
    if gate["reason"]:
        print(f"  未通过原因: {gate['reason']}")

    return 0 if gate["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
