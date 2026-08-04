"""评测数据生成脚本 — 从知识库 chunks 自动生成评测用例

原理：
  从 ChromaDB 取所有 chunks，按文档分层抽样，对每个 chunk 用 LLM 生成
  "该 chunk 能回答的问题 + 标准答案 + 关键词 + 来源"。

产出：
  evaluation/generated_cases.json — 生成的评测用例列表

用法：
  python scripts/generate_eval_data.py --count 150 --sample 3
    --count   生成总数（默认150）
    --sample  每个文档最多抽几个chunk（默认3）
"""
import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from collections import defaultdict
from src.core.langchain_rag import LangChainRAGEngine


def _get_llm():
    """获取 LLM 客户端"""
    from openai import OpenAI
    from src.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL), OPENAI_MODEL


def _norm_filename(f: str) -> str:
    """规范化文件名（去掉路径）"""
    if not f:
        return "未知"
    if "/" in f or "\\" in f:
        return f.replace("\\", "/").split("/")[-1]
    return f


# 模糊/泛泛的问法模式（生成的问题若匹配则丢弃）
VAGUE_PATTERNS = [
    "主要介绍了什么", "介绍了什么内容", "是什么内容", "讲了什么",
    "有什么内容", "这是什么", "指的是什么", "大概内容",
    "关于什么", "涉及什么", "总结了什么",
]


def _classify_domain(filename: str) -> str:
    """根据文件名归类领域"""
    fn = filename.lower()
    if any(k in fn for k in ["刑法", "治安", "处罚法", "法律"]):
        return "legal"
    if any(k in fn for k in ["论文", "毕业设计", "开题", "答辩", "记录本", "审批"]):
        return "thesis"
    if any(k in fn for k in ["python", "代码", "fastapi"]):
        return "programming"
    if any(k in fn for k in ["面试", "运营", "简历", "harness", "openclaw", "mcp"]):
        return "ai_tech"
    if any(k in fn for k in ["rag", "知识手册", "学习路线", "ai应用"]):
        return "ai_tech"
    return "other"


def _is_vague(question: str) -> bool:
    """判断问题是否过于泛泛"""
    return any(p in question for p in VAGUE_PATTERNS)


def generate_case_for_chunk(client, model, chunk_text: str, source_file: str,
                            section: str, domain: str) -> dict | None:
    """对一个 chunk 生成评测用例

    Returns:
        dict 或 None（生成失败）
    """
    # 过滤太短/太碎的 chunk（不适合作为答案来源）
    if len(chunk_text) < 30:
        return None

    prompt = f"""基于以下知识库片段，生成一个用户可能会问的问题及标准答案。

要求：
1. 问题必须是用户会真实问的、且该片段能回答的问题
2. 标准答案必须完全来自该片段内容，不要编造
3. 问题要具体，不要泛泛而问
4. 输出 JSON 格式，不要有其他内容

知识库片段：
{chunk_text[:800]}

输出格式：
{{
    "question": "用户问题",
    "expected_answer": "标准答案（来自片段）",
    "expected_keywords": ["关键词1", "关键词2", "关键词3"]
}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是评测数据生成专家，严格基于给定内容生成问题与答案。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=300,
        )
        text = resp.choices[0].message.content or ""
        # 提取 JSON
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group())
        question = data.get("question", "").strip()
        answer = data.get("expected_answer", "").strip()
        keywords = data.get("expected_keywords", [])
        if not question or not answer:
            return None
        return {
            "id": "",
            "question": question,
            "expected_answer": answer,
            "expected_keywords": keywords,
            "source_files": [source_file],
            "category": "simple_fact",
            "domain": domain,
            "source_chunk": chunk_text[:80],  # 溯源用
        }
    except Exception as e:
        print(f"  生成失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=250)
    parser.add_argument("--sample", type=int, default=3, help="每个文档最多抽几个chunk")
    parser.add_argument("--output", type=str, default="generated_cases.json",
                        help="输出文件名")
    args = parser.parse_args()

    print(f"加载知识库向量...")
    engine = LangChainRAGEngine()
    data = engine.vectorstore.get(include=["metadatas", "documents"])
    print(f"共 {len(data['ids'])} 个 chunks")

    # 过滤图片/表格类 chunk（不适合做问答评测）
    candidates = []
    for i, m in enumerate(data["metadatas"]):
        ctype = m.get("content_type", "")
        ftype = m.get("file_type", "")
        text = data["documents"][i]
        if ctype in ("image_description", "ocr_image") or ftype in ("png", "jpg", "jpeg"):
            continue
        if len(text) < 50:  # 过滤太短的chunk
            continue
        candidates.append({
            "text": text,
            "meta": m,
        })

    print(f"可用 chunks: {len(candidates)}")

    # 按文件分组（保留 metadata 用于过滤和归类）
    by_file = defaultdict(list)
    for c in candidates:
        f = _norm_filename(c["meta"].get("source_file", ""))
        by_file[f].append(c)

    # 分层抽样：每个文档随机抽 sample 个不同位置的 chunk，最大化覆盖
    random.seed(42)
    sampled = []
    for f, items in by_file.items():
        if len(items) > 1:
            n = min(args.sample, len(items))
            # 随机抽 n 个不同索引
            idxs = random.sample(range(len(items)), n)
            for idx in idxs:
                sampled.append((f, items[idx]))

    # 按需要数量随机抽样
    if len(sampled) > args.count:
        sampled = random.sample(sampled, args.count)

    filtered = [(f, item) for f, item in sampled]
    for f, item in sampled:
        ctype = item["meta"].get("content_type", "")
        ftype = item["meta"].get("file_type", "")
        if ctype in ("image_description", "ocr_image") or ftype in ("png", "jpg", "jpeg"):
            continue
        filtered.append((f, item))
    print(f"过滤图片类后剩 {len(filtered)}/{len(sampled)} 个 chunks，开始生成...")
    sampled = filtered

    client, model = _get_llm()

    generated = []
    seen_questions = set()
    for idx, (f, item) in enumerate(sampled):
        chunk_text = item["text"]
        domain = _classify_domain(f)
        print(f"  [{idx+1}/{len(sampled)}] {f[:30]}...", end=" ")
        case = generate_case_for_chunk(client, model, chunk_text, f, "", domain)
        if case and not _is_vague(case["question"]):
            # 去重：跳过相似问题
            q_key = case["question"][:20]
            if q_key in seen_questions:
                print("DUP")
                continue
            seen_questions.add(q_key)
            case_id = f"g{idx+1:04d}"
            case["id"] = case_id
            generated.append(case)
            print("OK")
        else:
            print("SKIP")

    # 保存
    out_path = PROJECT_ROOT / "evaluation" / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(generated, f, ensure_ascii=False, indent=2)
    print(f"\n生成完成: {len(generated)} 条，保存到 {out_path}")


if __name__ == "__main__":
    main()
