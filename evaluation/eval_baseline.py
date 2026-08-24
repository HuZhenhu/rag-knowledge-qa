"""P2: RAGAS 风格检索质量基线（改动前/后对比）

对比对象：
- baseline : 改动前近似——纯向量检索 Top-K（无混合/无重排/无查询增强），阈值硬过滤近似关闭
- optimized: 改动后——混合检索(BM25+向量+RRF, candidate_k) + 查询纠错/扩展 + bge-reranker 精排 → Top-K

指标（本地 embedding 实现，不依赖 LLM 裁判，避免 key 退化影响）：
- Context Recall   : golden answer 切句，被 Top-K 检索上下文覆盖的比例（句-块余弦>阈值）
- Context Precision: AP@K，按 rank 加权的前缀精度
- Source Hit@K      : 检索 Top-K 中命中 golden source_files 对应块的比例

用法: python evaluation/eval_baseline.py [--limit N] [--top-k K] [--sim-threshold T] [--engine baseline|optimized|both]
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.pop("OPENAI_API_KEY", None)

import numpy as np

from src.config import RETRIEVAL_CANDIDATE_K, EMBEDDING_MODEL

_CASE_FILE = os.path.join(os.path.dirname(__file__), "generated_cases_250.json")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?])\s*")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) > 2]


class Evaluator:
    def __init__(self, sim_threshold: float = 0.5, top_k: int = 5):
        self.sim_threshold = sim_threshold
        self.top_k = top_k

    def _embed_batch(self, emb, texts):
        return emb.embed_documents(texts) if len(texts) > 1 else ([emb.embed_query(texts[0])] if texts else [])

    def _cosine(self, a, b):
        a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        return float(a @ b / (na * nb + 1e-9)) if na and nb else 0.0

    def score_case(self, emb, question, expected_answer, retrieved_chunks, golden_chunk=None):
        """retrieved_chunks: list[str]（Top-K 块文本，已按序）
        golden_chunk: str|None 黄金块（generated_cases 的 source_chunk），用于块级 Hit/MRR
        """
        if not retrieved_chunks:
            return {"recall": 0.0, "precision": 0.0, "hit": 0.0, "chunk_hit": 0.0, "mrr": 0.0}
        ans_sents = split_sentences(expected_answer)
        if not ans_sents:
            ans_sents = [expected_answer]

        sent_embs = self._embed_batch(emb, ans_sents)
        chunk_embs = self._embed_batch(emb, retrieved_chunks)

        # Context Recall: 每句被任一 chunk 覆盖
        hit_sents = 0
        for se in sent_embs:
            sims = [self._cosine(se, ce) for ce in chunk_embs]
            if max(sims) >= self.sim_threshold:
                hit_sents += 1
        recall = hit_sents / len(sent_embs)

        # Context Precision: AP@K（chunk 与整条 golden 相关度判定）
        golden_emb = emb.embed_query(expected_answer)
        rel = [1 if self._cosine(golden_emb, ce) >= self.sim_threshold else 0 for ce in chunk_embs]
        prec_sum, rel_count = 0.0, 0
        for k in range(1, len(rel) + 1):
            if rel[k - 1]:
                rel_count += 1
                prec_sum += sum(rel[:k]) / k
        precision = (prec_sum / rel_count) if rel_count else 0.0
        hit = 1.0 if rel_count > 0 else 0.0

        # 块级命中与 MRR：检索 Top-K 是否包含 golden source_chunk（同块判定：cosine > 0.85）
        chunk_hit, mrr = 0.0, 0.0
        if golden_chunk:
            gc_emb = emb.embed_query(golden_chunk)
            for rank, ce in enumerate(chunk_embs, start=1):
                if self._cosine(gc_emb, ce) > 0.85:
                    chunk_hit = 1.0
                    mrr = 1.0 / rank
                    break
        return {"recall": recall, "precision": precision, "hit": hit,
                "chunk_hit": chunk_hit, "mrr": mrr}


def build_engine(engine_type: str, candidate_k: int | None = None):
    from src.core.langchain_rag import LangChainRAGEngine
    ck = candidate_k or RETRIEVAL_CANDIDATE_K
    if engine_type == "baseline":
        return LangChainRAGEngine(
            use_hybrid=False, use_reranker=False,
            use_query_expansion=False, use_query_correction=False,
            candidate_k=ck, top_k=5,
        )
    return LangChainRAGEngine(candidate_k=ck, top_k=5)  # 默认：混合+重排+纠错+扩展（按 .env）


def run(engine_type: str, cases, top_k: int, sim_threshold: float, candidate_k: int | None = None,
        resolve_parent: bool = False):
    eng = build_engine(engine_type, candidate_k)
    emb = eng.embeddings
    ev = Evaluator(sim_threshold=sim_threshold, top_k=top_k)
    rows = []
    for i, case in enumerate(cases):
        q = case["question"]
        t0 = time.time()
        # 检索链路（query 内部会走纠错/扩展/混合/重排，这里只取 sources 不生成）
        scored = eng._retrieve_multi(q, eng.candidate_k)
        sources = eng._build_sources(q, scored, top_k)
        if resolve_parent:
            sources = eng._resolve_parent(sources)
        chunks = [s["content"] for s in sources]
        metric = ev.score_case(emb, q, case.get("expected_answer", ""), chunks,
                               golden_chunk=case.get("source_chunk"))
        metric["time_s"] = round(time.time() - t0, 2)
        rows.append(metric)
        if (i + 1) % 5 == 0:
            print(f"  [{engine_type}] {i+1}/{len(cases)} done", flush=True)
    agg = {
        "engine": engine_type,
        "n": len(rows),
        "context_recall": round(float(np.mean([r["recall"] for r in rows])), 4),
        "context_precision": round(float(np.mean([r["precision"] for r in rows])), 4),
        "hit_at_k": round(float(np.mean([r["hit"] for r in rows])), 4),
        "chunk_hit_at_k": round(float(np.mean([r["chunk_hit"] for r in rows])), 4),
        "mrr": round(float(np.mean([r["mrr"] for r in rows])), 4),
        "avg_time_s": round(float(np.mean([r["time_s"] for r in rows])), 2),
    }
    return agg, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="0 表示全量")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--sim-threshold", type=float, default=0.5)
    ap.add_argument("--engine", default="both", choices=["baseline", "optimized", "both"])
    ap.add_argument("--cases", default=_CASE_FILE, help="评测集 JSON 文件路径")
    ap.add_argument("--candidate-k", type=int, default=0, help="覆盖检索候选数，0 使用默认")
    ap.add_argument("--resolve-parent", action="store_true", help="对 optimized 引擎执行父子回取后评估")
    args = ap.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        all_cases = json.load(f)
    cases = all_cases if args.limit == 0 else all_cases[: args.limit]
    print(f"用例: {len(cases)} (limit={args.limit}) sim_threshold={args.sim_threshold} top_k={args.top_k} cases={os.path.basename(args.cases)}")

    results = {"meta": vars(args), "aggregates": [], "details": {}}
    ck = args.candidate_k or None
    for et in (["baseline", "optimized"] if args.engine == "both" else [args.engine]):
        agg, rows = run(et, cases, args.top_k, args.sim_threshold, candidate_k=ck,
                        resolve_parent=args.resolve_parent)
        results["aggregates"].append(agg)
        results["details"][et] = rows
        print(f"\n=== {et} ===")
        print(json.dumps(agg, ensure_ascii=False, indent=2))

    out = os.path.join(os.path.dirname(__file__), f"baseline_{int(time.time())}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {out}")


if __name__ == "__main__":
    main()
