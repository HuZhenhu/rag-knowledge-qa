"""M9: 自动评测脚本 — 评测集跑批 + 语义相似度 + Markdown报告 + 版本对比
M6: 支持 RAG_ENGINE=agentic 评测（复用同一入口），并新增 Agentic 专项维度
（规划正确率 / 工具选择合理性 / 平均反思次数 / 最终引用正确率）。
"""
import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

from src.config import (
    BASE_DIR,
    EVAL_TEST_CASES_PATH,
    EVAL_RESULTS_DIR,
    EVAL_SIMILARITY_THRESHOLD,
)


# ---------------------------------------------------------------------------
# 评测用例加载
# ---------------------------------------------------------------------------

def load_test_cases(path: str = EVAL_TEST_CASES_PATH) -> list[dict]:
    """加载评测用例"""
    full_path = BASE_DIR / path if not Path(path).is_absolute() else Path(path)
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 评测指标计算
# ---------------------------------------------------------------------------

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """计算余弦相似度"""
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def evaluate_answer(answer: str, expected_keywords: list[str]) -> dict:
    """基于关键词的回答质量评估"""
    answer_lower = answer.lower()

    hits = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
    keyword_score = hits / len(expected_keywords) if expected_keywords else 0

    length_score = min(len(answer) / 100, 1.0)

    has_citation = "[" in answer and "]" in answer
    citation_score = 1.0 if has_citation else 0.5

    total_score = keyword_score * 0.5 + length_score * 0.2 + citation_score * 0.3

    return {
        "keyword_score": round(keyword_score, 2),
        "length_score": round(length_score, 2),
        "citation_score": round(citation_score, 2),
        "total_score": round(total_score, 2),
        "hits": hits,
        "total_keywords": len(expected_keywords),
    }


def compute_semantic_similarity(
    generated_answer: str,
    expected_answer: str,
    embedder,
) -> float:
    """用 Embedding 模型计算生成答案与标准答案的语义相似度"""
    if not generated_answer or not expected_answer:
        return 0.0
    vecs = embedder.embed([generated_answer, expected_answer])
    return _cosine_similarity(vecs[0], vecs[1])


def check_retrieval_hit(sources: list[dict], source_files: list[str]) -> bool:
    """检查检索结果中是否包含预期来源文件"""
    if not source_files:
        return True  # 无来源要求时默认命中
    import re

    def norm(name: str) -> str:
        """规范化文件名：去路径、去扩展名、统一大小写"""
        name = name.replace("\\", "/").split("/")[-1]
        return re.sub(r"\.(pdf|docx|doc|md|txt|png|jpg)$", "", name).lower()

    # 从 metadata 中提取来源文件名
    retrieved_files = []
    for s in sources:
        meta = s.get("metadata", {}) or {}
        f = meta.get("source_file", "") or meta.get("source", "")
        if f:
            retrieved_files.append(norm(str(f)))
    retrieved_texts = " ".join(retrieved_files)
    if any(norm(sf) in retrieved_texts for sf in source_files):
        return True
    # metadata 无来源信息时，回退到正文匹配（兼容无 metadata 的检索结果）
    for s in sources:
        content = str(s.get("content", "") or "")
        if any(norm(sf) in content.lower() for sf in source_files):
            return True
    return False


def check_citation(answer: str) -> bool:
    """检查回答是否包含引用标注

    兼容两种引用格式：RAG 系统常用 [来源X]，通用格式 [X]（如 [1][2]）。
    """
    import re
    return bool(re.search(r"\[(?:来源)?\d+\]", answer))


# ---------------------------------------------------------------------------
# 单条评测
# ---------------------------------------------------------------------------

def run_single_test(case: dict, engine, embedder, use_four_dim: bool = True) -> dict:
    """对单个测试用例执行评测，返回评测结果字典

    Args:
        case: 测试用例
        engine: RAG引擎
        embedder: 语义相似度模型
        use_four_dim: 是否启用四维度LLM评测（False时只做关键词评分，更快）
    """
    case_id = case["id"]
    question = case["question"]
    expected_keywords = case.get("expected_keywords", [])
    expected_answer = case.get("expected_answer", "")
    source_files = case.get("source_files", [])
    category = case.get("category", "unknown")

    start_time = time.time()
    try:
        result = engine.query(question)
        # 兼容dataclass和dict两种返回格式
        if hasattr(result, 'answer'):
            answer = result.answer or ""
            sources = result.sources or []
        else:
            answer = result.get("answer", "")
            sources = result.get("sources", [])
        elapsed_ms = int((time.time() - start_time) * 1000)

        # M6: 提取 Agentic 引擎推理过程（非 agentic 引擎无 agent_trace 字段）
        agent_trace = None
        if hasattr(result, "agent_trace"):
            agent_trace = result.agent_trace or []
        elif isinstance(result, dict):
            agent_trace = result.get("agent_trace") or []

        # 关键词评分
        kw_eval = evaluate_answer(answer, expected_keywords)

        # 语义相似度
        semantic_sim = compute_semantic_similarity(
            answer, expected_answer, embedder
        )

        # 检索命中率
        retrieval_hit = check_retrieval_hit(sources, source_files)

        # 引用正确率
        citation_ok = check_citation(answer)

        # 判断是否为边界情况（知识库中没有的问题）
        is_out_of_scope = category == "out_of_scope"
        if is_out_of_scope:
            # 对于边界情况，正确行为是承认不知道
            correct_refusal = any(
                phrase in answer
                for phrase in ["未找到", "没有找到", "知识库中没有", "无法回答"]
            )
            accuracy = 1.0 if correct_refusal else 0.0
        else:
            accuracy = kw_eval["total_score"]

        # M10: 四维度 LLM-as-Judge 评测（非边界问题，可选）
        four_dims = {}
        if use_four_dim and not is_out_of_scope and answer and len(answer) > 20:
            try:
                from src.core.eval_metrics import evaluate_all_dimensions
                four_dims = evaluate_all_dimensions(
                    question, answer, sources, expected_answer, embedder=embedder
                )
            except Exception as e:
                logger.warning("四维评测失败 %s: %s", case_id, e)
                four_dims = {}

        # M6: Agentic 专项维度评测（仅 agentic 引擎有 agent_trace）
        agentic_dims = {}
        if agent_trace:
            try:
                from src.core.eval_metrics import evaluate_agentic_dimensions
                agentic_dims = evaluate_agentic_dimensions(
                    question, agent_trace, answer, sources
                )
            except Exception as e:
                logger.warning("Agentic 专项评测失败 %s: %s", case_id, e)
                agentic_dims = {}

        return {
            "case_id": case_id,
            "question": question,
            "answer": answer[:300],
            "category": category,
            "status": "success",
            "elapsed_ms": elapsed_ms,
            "keyword_score": kw_eval["keyword_score"],
            "total_score": kw_eval["total_score"],
            "semantic_similarity": round(semantic_sim, 4),
            "retrieval_hit": retrieval_hit,
            "citation_correct": citation_ok,
            "accuracy": round(accuracy, 4),
            "source_files_count": len(source_files),
            "faithfulness": four_dims.get("faithfulness"),
            "answer_relevancy": four_dims.get("answer_relevancy"),
            "context_precision": four_dims.get("context_precision"),
            "context_recall": four_dims.get("context_recall"),
            "four_dim_total": four_dims.get("total_score"),
            # M6: Agentic 专项维度
            "planning_accuracy": agentic_dims.get("planning_accuracy"),
            "tool_choice_score": agentic_dims.get("tool_choice_score"),
            "retry_count": agentic_dims.get("retry_count"),
            "final_citation_accuracy": agentic_dims.get("final_citation_accuracy"),
        }

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "case_id": case_id,
            "question": question,
            "category": category,
            "status": "error",
            "error": str(e),
            "elapsed_ms": elapsed_ms,
            "keyword_score": 0,
            "total_score": 0,
            "semantic_similarity": 0.0,
            "retrieval_hit": False,
            "citation_correct": False,
            "accuracy": 0.0,
            "planning_accuracy": None,
            "tool_choice_score": None,
            "retry_count": None,
            "final_citation_accuracy": None,
        }


# ---------------------------------------------------------------------------
# 批量评测
# ---------------------------------------------------------------------------

def run_evaluation(
    test_cases: list[dict] | None = None,
    version: str = "",
    engine=None,
    use_four_dim: bool = True,
) -> dict:
    """运行完整评测，返回汇总结果

    Args:
        test_cases: 评测用例列表
        version: 版本标识
        engine: RAG引擎（默认按config选择）
        use_four_dim: 是否启用四维评测（False时更快，只做关键词评分）
    """
    if test_cases is None:
        test_cases = load_test_cases()

    if not version:
        version = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print(f"RAG 智能问答系统评测 — 版本: {version}")
    print(f"测试用例数: {len(test_cases)}")
    print("=" * 60)

    try:
        from src.core.embedder import Embedder
        from src.config import RAG_ENGINE

        if engine is None:
            # 使用与实际运行相同的引擎
            if RAG_ENGINE == "langchain":
                from src.core.langchain_rag import LangChainRAGEngine
                engine = LangChainRAGEngine()
            elif RAG_ENGINE == "agentic":
                from src.core.agentic import AgenticEngine
                engine = AgenticEngine()
            else:
                from src.core.rag_engine import RAGEngine
                engine = RAGEngine()

        # 语义相似度对比用 Embedder。
        # 注意：LangChain 引擎内部已加载 bge-m3，若再单独加载 Embedder 会导致
        # 同一模型在进程内加载两次，Windows 下触发段错误。因此复用引擎的 embedding。
        if RAG_ENGINE == "langchain" and hasattr(engine, "embeddings"):
            # LangChain 引擎的 embeddings 是 HuggingFaceEmbeddings，封装一层兼容 embed()
            class _LangChainEmbedder:
                def embed(self, texts):
                    return engine.embeddings.embed_documents(list(texts))
            embedder = _LangChainEmbedder()
        elif RAG_ENGINE == "agentic" and hasattr(engine, "embedder"):
            # Agentic 引擎内部已加载 Embedder，直接复用避免重复加载模型
            embedder = engine.embedder
        else:
            embedder = Embedder()
    except Exception as e:
        print(f"无法初始化引擎: {e}")
        return {"error": str(e)}

    results = []
    for case in test_cases:
        print(f"  [{case['id']}] {case['question'][:40]}...", end=" ")
        evaluation = run_single_test(case, engine, embedder, use_four_dim=use_four_dim)
        results.append(evaluation)
        print(f"score={evaluation['accuracy']:.2f}  sim={evaluation['semantic_similarity']:.3f}  "
              f"retrieval={'Y' if evaluation['retrieval_hit'] else 'N'}  "
              f"cite={'Y' if evaluation['citation_correct'] else 'N'}  "
              f"({evaluation['elapsed_ms']}ms)")

    # 汇总
    summary = compute_summary(results, version)
    summary["results"] = results

    # 输出报告
    report_path = write_report(summary)

    # 保存原始结果
    results_dir = BASE_DIR / EVAL_RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    raw_path = results_dir / f"eval_{version}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n原始结果已保存到 {raw_path}")
    print(f"评测报告已保存到 {report_path}")

    return summary


def compute_summary(results: list[dict], version: str) -> dict:
    """计算评测汇总指标"""
    total = len(results)
    success_results = [r for r in results if r["status"] == "success"]
    success_count = len(success_results)

    if success_count == 0:
        return {
            "version": version,
            "total_cases": total,
            "success_cases": 0,
            "retrieval_hit_rate": 0.0,
            "answer_accuracy": 0.0,
            "citation_accuracy": 0.0,
            "avg_semantic_similarity": 0.0,
            "avg_latency_ms": 0.0,
            "category_accuracy": {},
            "avg_agentic": {},
        }

    retrieval_hits = sum(1 for r in success_results if r["retrieval_hit"])
    citation_correct = sum(1 for r in success_results if r["citation_correct"])

    avg_accuracy = sum(r["accuracy"] for r in success_results) / success_count
    avg_similarity = sum(r["semantic_similarity"] for r in success_results) / success_count
    avg_latency = sum(r["elapsed_ms"] for r in success_results) / success_count

    # M10: 四维度平均值（仅统计有值的用例）
    dim_keys = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "four_dim_total"]
    avg_dims: dict[str, float] = {}
    for key in dim_keys:
        vals = [r[key] for r in success_results if r.get(key) is not None]
        if vals:
            avg_dims[key] = round(sum(vals) / len(vals), 4)

    # M6: Agentic 专项维度平均值（仅统计有值的用例）
    agentic_keys = ["planning_accuracy", "tool_choice_score", "final_citation_accuracy"]
    avg_agentic: dict[str, float] = {}
    for key in agentic_keys:
        vals = [r[key] for r in success_results if r.get(key) is not None]
        if vals:
            avg_agentic[key] = round(sum(vals) / len(vals), 4)
    retry_vals = [r["retry_count"] for r in success_results
                  if r.get("retry_count") is not None]
    if retry_vals:
        avg_agentic["avg_retry_count"] = round(sum(retry_vals) / len(retry_vals), 4)

    # 按类别统计
    category_stats: dict[str, list[float]] = {}
    for r in success_results:
        cat = r.get("category", "unknown")
        category_stats.setdefault(cat, []).append(r["accuracy"])
    category_accuracy = {
        cat: round(sum(scores) / len(scores), 4)
        for cat, scores in category_stats.items()
    }

    return {
        "version": version,
        "total_cases": total,
        "success_cases": success_count,
        "retrieval_hit_rate": round(retrieval_hits / success_count, 4),
        "answer_accuracy": round(avg_accuracy, 4),
        "citation_accuracy": round(citation_correct / success_count, 4),
        "avg_semantic_similarity": round(avg_similarity, 4),
        "avg_latency_ms": round(avg_latency, 2),
        "category_accuracy": category_accuracy,
        "avg_dimensions": avg_dims,
        "avg_agentic": avg_agentic,
    }


# ---------------------------------------------------------------------------
# Markdown 报告输出
# ---------------------------------------------------------------------------

def write_report(summary: dict) -> str:
    """生成 Markdown 格式评测报告"""
    results_dir = BASE_DIR / EVAL_RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)

    version = summary.get("version", "unknown")
    report_path = results_dir / f"report_{version}.md"
    results = summary.get("results", [])

    lines = [
        "# RAG 智能问答系统评测报告",
        "",
        f"**版本**: {version}",
        f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## 总体指标",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 测试用例数 | {summary.get('total_cases', 0)} |",
        f"| 成功数 | {summary.get('success_cases', 0)} |",
        f"| 检索命中率 | {summary.get('retrieval_hit_rate', 0):.1%} |",
        f"| 回答准确率 | {summary.get('answer_accuracy', 0):.1%} |",
        f"| 引用正确率 | {summary.get('citation_accuracy', 0):.1%} |",
        f"| 平均语义相似度 | {summary.get('avg_semantic_similarity', 0):.3f} |",
        f"| 平均延迟 | {summary.get('avg_latency_ms', 0):.0f}ms |",
        "",
    ]

    # M10: 四维度指标
    avg_dims = summary.get("avg_dimensions", {})
    if avg_dims:
        dim_labels = {
            "faithfulness": "忠实度",
            "answer_relevancy": "答案相关性",
            "context_precision": "上下文精准度",
            "context_recall": "上下文召回率",
            "four_dim_total": "RAGAS总分",
        }
        lines += [
            "## 四维度评测（LLM-as-Judge）",
            "",
            "| 维度 | 分数 |",
            "|------|------|",
        ]
        for key, label in dim_labels.items():
            if key in avg_dims:
                lines.append(f"| {label} | {avg_dims[key]:.2f} |")
        lines.append("")

    # M6: Agentic 专项维度指标
    avg_agentic = summary.get("avg_agentic", {})
    if avg_agentic:
        agentic_labels = {
            "planning_accuracy": "规划正确率",
            "tool_choice_score": "工具选择合理性",
            "final_citation_accuracy": "最终引用正确率",
            "avg_retry_count": "平均反思次数",
        }
        lines += [
            "## Agentic 专项评测维度",
            "",
            "| 维度 | 分数 |",
            "|------|------|",
        ]
        for key, label in agentic_labels.items():
            if key in avg_agentic:
                val = avg_agentic[key]
                if key == "avg_retry_count":
                    lines.append(f"| {label} | {val:.2f} |")
                else:
                    lines.append(f"| {label} | {val:.2%} |")
        lines.append("")

    # 分类准确率
    cat_acc = summary.get("category_accuracy", {})
    if cat_acc:
        lines += [
            "## 分类准确率",
            "",
            "| 类别 | 准确率 |",
            "|------|--------|",
        ]
        for cat, acc in cat_acc.items():
            lines.append(f"| {cat} | {acc:.1%} |")
        lines.append("")

    # 与上次评测对比
    comparison = summary.get("comparison", None)
    if comparison:
        lines += [
            "## 与上次评测对比",
            "",
            "| 指标 | 上次 | 本次 | 变化 |",
            "|------|------|------|------|",
        ]
        metrics_compare = [
            ("answer_accuracy", "回答准确率"),
            ("retrieval_hit_rate", "检索命中率"),
            ("citation_accuracy", "引用正确率"),
            ("avg_semantic_similarity", "语义相似度"),
        ]
        for key, label in metrics_compare:
            prev = comparison.get(f"prev_{key}", 0)
            curr = comparison.get(f"curr_{key}", 0)
            delta = curr - prev
            sign = "+" if delta >= 0 else ""
            lines.append(f"| {label} | {prev:.1%} | {curr:.1%} | {sign}{delta:.1%} |")
        lines.append("")

    # 详细结果表
    show_agentic = bool(summary.get("avg_agentic", {}))
    if show_agentic:
        lines += [
            "## 详细结果",
            "",
            "| ID | 问题 | 准确率 | 四维总分 | 检索 | 引用 | 规划 | 工具 | 反思 | 终引 | 耗时 |",
            "|-----|------|--------|---------|------|------|------|------|------|------|------|",
        ]
    else:
        lines += [
            "## 详细结果",
            "",
            "| ID | 问题 | 准确率 | 四维总分 | 检索 | 引用 | 耗时 |",
            "|-----|------|--------|---------|------|------|------|",
        ]
    for r in results:
        q = r.get("question", "")[:25]
        acc = r.get("accuracy", 0)
        four_dim = r.get("four_dim_total", None)
        four_dim_str = f"{four_dim:.2f}" if four_dim is not None else "-"
        hit = "Y" if r.get("retrieval_hit") else "N"
        cite = "Y" if r.get("citation_correct") else "N"
        ms = r.get("elapsed_ms", 0)
        if show_agentic:
            def _fmt(v):
                return "-" if v is None else f"{v:.2f}"
            plan = _fmt(r.get("planning_accuracy"))
            tool = _fmt(r.get("tool_choice_score"))
            retry = "-" if r.get("retry_count") is None else str(r["retry_count"])
            fcite = _fmt(r.get("final_citation_accuracy"))
            lines.append(
                f"| {r.get('case_id', '')} | {q} | {acc:.2f} | {four_dim_str} | {hit} | {cite} "
                f"| {plan} | {tool} | {retry} | {fcite} | {ms}ms |"
            )
        else:
            lines.append(f"| {r.get('case_id', '')} | {q} | {acc:.2f} | {four_dim_str} | {hit} | {cite} | {ms}ms |")
    lines.append("")

    content = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)

    return str(report_path)


# ---------------------------------------------------------------------------
# 版本对比
# ---------------------------------------------------------------------------

def compare_evaluations(version_a: str, version_b: str) -> dict:
    """对比两次评测结果"""
    results_dir = BASE_DIR / EVAL_RESULTS_DIR
    path_a = results_dir / f"eval_{version_a}.json"
    path_b = results_dir / f"eval_{version_b}.json"

    if not path_a.exists() or not path_b.exists():
        raise FileNotFoundError(f"评测结果文件不存在: {path_a} 或 {path_b}")

    with open(path_a, "r", encoding="utf-8") as f:
        data_a = json.load(f)
    with open(path_b, "r", encoding="utf-8") as f:
        data_b = json.load(f)

    comparison = {
        "version_a": version_a,
        "version_b": version_b,
        "prev_answer_accuracy": data_a.get("answer_accuracy", 0),
        "curr_answer_accuracy": data_b.get("answer_accuracy", 0),
        "prev_retrieval_hit_rate": data_a.get("retrieval_hit_rate", 0),
        "curr_retrieval_hit_rate": data_b.get("retrieval_hit_rate", 0),
        "prev_citation_accuracy": data_a.get("citation_accuracy", 0),
        "curr_citation_accuracy": data_b.get("citation_accuracy", 0),
        "prev_avg_semantic_similarity": data_a.get("avg_semantic_similarity", 0),
        "curr_avg_semantic_similarity": data_b.get("avg_semantic_similarity", 0),
    }
    return comparison


def list_evaluation_versions() -> list[str]:
    """列出所有已保存的评测版本"""
    results_dir = BASE_DIR / EVAL_RESULTS_DIR
    if not results_dir.exists():
        return []
    versions = []
    for f in sorted(results_dir.glob("eval_*.json")):
        v = f.stem.replace("eval_", "")
        versions.append(v)
    return versions


# ---------------------------------------------------------------------------
# 保存评测结果到 SQLite
# ---------------------------------------------------------------------------

def save_to_database(summary: dict) -> int | None:
    """将评测汇总保存到 SQLite evaluations 表"""
    try:
        from src.storage.database import save_evaluation
        eval_id = save_evaluation(
            version=summary.get("version", ""),
            total_cases=summary.get("total_cases", 0),
            success_cases=summary.get("success_cases", 0),
            retrieval_hit_rate=summary.get("retrieval_hit_rate", 0),
            answer_accuracy=summary.get("answer_accuracy", 0),
            citation_accuracy=summary.get("citation_accuracy", 0),
            avg_semantic_similarity=summary.get("avg_semantic_similarity", 0),
            avg_latency_ms=summary.get("avg_latency_ms", 0),
            details=json.dumps(
                {k: v for k, v in summary.items() if k != "results"},
                ensure_ascii=False,
            ),
        )
        print(f"评测结果已保存到数据库，ID: {eval_id}")
        return eval_id
    except Exception as e:
        print(f"保存到数据库失败: {e}")
        return None


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RAG 智能问答系统评测")
    parser.add_argument("--version", "-v", type=str, default="",
                        help="本次评测版本标识")
    parser.add_argument("--compare", nargs=2, metavar=("VER_A", "VER_B"),
                        help="对比两个版本的评测结果")
    parser.add_argument("--list", action="store_true",
                        help="列出所有已保存的评测版本")
    parser.add_argument("--save-db", action="store_true",
                        help="将评测结果保存到 SQLite")
    parser.add_argument("--test-cases", type=str, default="",
                        help="评测集 JSON 路径（默认用配置 EVAL_TEST_CASES_PATH）")
    parser.add_argument("--limit", type=int, default=0,
                        help="仅评测前 N 条用例（0=全部）")
    parser.add_argument("--no-four-dim", action="store_true",
                        help="跳过四维度 LLM 评测（更快，只做关键词评分）")
    args = parser.parse_args()

    if args.list:
        versions = list_evaluation_versions()
        if versions:
            print("已保存的评测版本:")
            for v in versions:
                print(f"  {v}")
        else:
            print("暂无已保存的评测版本")
        return

    if args.compare:
        try:
            comp = compare_evaluations(args.compare[0], args.compare[1])
            print(json.dumps(comp, ensure_ascii=False, indent=2))
        except FileNotFoundError as e:
            print(f"错误: {e}")
        return

    # 运行评测
    version = args.version
    test_cases = None
    if args.test_cases:
        test_cases = load_test_cases(args.test_cases)
        if args.limit > 0:
            test_cases = test_cases[:args.limit]
    elif args.limit > 0:
        all_cases = load_test_cases()
        test_cases = all_cases[:args.limit]
    summary = run_evaluation(
        test_cases=test_cases,
        version=version,
        use_four_dim=not args.no_four_dim,
    )

    if args.save_db:
        save_to_database(summary)


if __name__ == "__main__":
    main()
