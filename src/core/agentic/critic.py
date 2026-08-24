"""Critic 验证与纠错节点（Agentic RAG 设计文档 §3.6）。

M3 里程碑：从 M1 的确定性验证升级为真正的验证纠错节点，基于证据与
草稿答案做结构化评审，输出 pass/retry 判定与改进意见：

1. 证据充分性（coverage）：每个子问题是否都有带真实来源的证据覆盖；
2. 引用可靠性（citation）：引用来源是否真实存在于知识库（tools 可校验）、
   证据内容是否与其声明的来源文件一致；
3. 答案一致性（consistency）：草稿答案内部是否矛盾、是否与证据冲突
   （有 LLM 时做语义评审，无 LLM 时做确定性轻量检查）。

决策：``pass`` 进入 Summarizer；``retry`` 输出 retry_target（派回对应子
Agent）与 refine_instructions（改进意见）。重试次数 < max_retry 且未超
全局 deadline 前可循环；达上限/超时强制收敛（返回 pass + 收敛说明）。
"""
from __future__ import annotations

import logging
import re
import time

from langchain_core.runnables.config import RunnableConfig

from src.config import AGENT_MAX_RETRY
from src.core.agentic import llm as llm_util

logger = logging.getLogger(__name__)

# issue 严重级别：error 触发 retry；warning 仅记录不阻断
_SEV_ERROR = "error"
_SEV_WARNING = "warning"

_CONSISTENCY_SYSTEM_PROMPT = """你是 RAG 系统的验证评审（Critic）。
请基于检索到的证据与草稿答案，检查草稿答案是否：
1) 与任何证据的内容冲突或矛盾；
2) 内部存在自相矛盾；
3) 存在证据中完全没有依据的陈述（幻觉）。
只输出严格 JSON：
{"consistent": true, "reason": "一句话说明"}   # consistent=false 表示存在需修正的问题
证据与草稿如下："""


class Critic:
    """验证与纠错节点。"""

    def __init__(self, max_retry: int = AGENT_MAX_RETRY,
                 llm_client=None, model: str | None = None,
                 tools=None):
        self.max_retry = max_retry
        self.llm_client = llm_client
        self.model = model or llm_util.default_model()
        self.tools = tools  # KBTools 实例，用于校验引用是否真实存在于知识库

    # ---------------------------------------------------------------- 结构化评审
    def evaluate(self, question: str, sub_questions: list[str],
                 evidences: list[dict], retry_count: int,
                 draft_answer: str = "") -> dict:
        """返回结构化评审结果：
        {"decision", "reflection", "issues", "retry_target", "refine_instructions"}
        """
        evidences = evidences or []
        sub_questions = sub_questions or [question]
        issues: list[dict] = []

        # 1) 证据充分性
        if not evidences:
            issues.append({
                "type": "coverage", "severity": _SEV_ERROR,
                "detail": "未检索到任何证据，请重新检索并扩大召回。",
            })
        else:
            issues.extend(self._check_coverage(sub_questions, evidences))

        # 2) 引用可靠性
        issues.extend(self._check_citation(evidences))

        # 3) 答案一致性
        issues.extend(self._check_consistency(
            question, sub_questions, evidences, draft_answer))

        # 收集 error 级问题 → retry；仅 warning → pass
        errors = [i for i in issues if i.get("severity") == _SEV_ERROR]
        if errors:
            reflection = "；".join(f"{i['detail']}" for i in errors)
            return self._decide(retry_count, reflection, issues, question)

        warning_hint = ""
        if any(i.get("severity") == _SEV_WARNING for i in issues):
            warning_hint = "（存在轻微提示，不阻断）"
        return {
            "decision": "pass",
            "reflection": f"证据充分，引用来源完整，答案一致。{warning_hint}".strip(),
            "issues": issues,
            "retry_target": "retriever_agent",
            "refine_instructions": "",
        }

    def _check_coverage(self, sub_questions: list[str],
                        evidences: list[dict]) -> list[dict]:
        """证据充分性：每个子问题至少一条带来源的证据覆盖。"""
        covered = {
            sq for sq in sub_questions
            if any(self._evidence_source(e) for e in evidences
                   if self._covers(e, sq))
        }
        # 兜底：无 sub_question 标注时，只要证据整体带来源即视为覆盖（保持 M1 语义）
        if covered != set(sub_questions) and len(sub_questions) == 1:
            if any(self._evidence_source(e) for e in evidences):
                covered = set(sub_questions)
        missing = [sq for sq in sub_questions if sq not in covered]
        if not missing:
            return []
        return [{
            "type": "coverage", "severity": _SEV_ERROR,
            "detail": f"以下子问题缺少带来源的证据：{missing}，请补充检索。",
        }]

    def _check_citation(self, evidences: list[dict]) -> list[dict]:
        """引用可靠性：来源字段真实存在；有 tools 时校验文件确实在知识库中、
        且证据内容与其声明的来源文件一致。

        联网来源（source 以 ``web:`` 开头）不参与知识库清单校验：其真实性
        体现在 ``source_url`` 必须存在（校验交给搜索服务本身）。
        """
        issues: list[dict] = []
        for i, e in enumerate(evidences):
            meta = e.get("metadata", {}) or {}
            source = self._evidence_source(e)
            if source.startswith("web:"):
                # 联网来源：必须带有效 source_url，否则视为引用不可靠
                if not (meta.get("source_url") or "").strip():
                    issues.append({
                        "type": "citation", "severity": _SEV_ERROR,
                        "detail": f"联网证据 {i + 1} 缺少真实来源 URL（source_url 为空）。",
                    })
                continue
            if not source or source == "未知":
                issues.append({
                    "type": "citation", "severity": _SEV_ERROR,
                    "detail": f"证据 {i + 1} 缺少真实来源文件（source_file/source 为空）。",
                })
        if not issues and self.tools is not None:
            issues.extend(self._verify_citations_against_kb(evidences))
        return issues

    def _verify_citations_against_kb(self, evidences: list[dict]) -> list[dict]:
        """用工具集校验引用真实性：来源文件在知识库清单中 + 内容与来源一致。"""
        issues: list[dict] = []
        try:
            known = set(self.tools.kb_list_documents().get("documents", []))
        except Exception as e:  # noqa: BLE001
            logger.warning("Critic 引用校验：获取知识库清单失败 %s", e)
            return issues
        if not known:
            # 无法获取清单（无 vector_store），仅做字段校验
            return issues
        # 按来源文件聚合内容，用于内容一致性校验
        file_contents: dict[str, list[str]] = {}
        for c in self.tools._all_chunks() or []:
            meta = c.get("metadata") or {}
            sf = meta.get("source_file", "") or meta.get("source", "")
            base = sf.replace("\\", "/").rsplit("/", 1)[-1] if sf else ""
            if base:
                file_contents.setdefault(base, []).append(c.get("content", ""))

        for i, e in enumerate(evidences):
            meta = e.get("metadata", {}) or {}
            source = self._evidence_source(e)
            if source.startswith("web:"):
                # 联网来源不参与知识库清单校验（其真实性由 source_url 保证）
                continue
            base = source.replace("\\", "/").rsplit("/", 1)[-1] if source else ""
            if base and base not in known:
                issues.append({
                    "type": "citation", "severity": _SEV_ERROR,
                    "detail": f"引用来源「{base}」不在知识库文档清单中，引用不可靠。",
                })
                continue
            content = (e.get("content") or "").strip()
            contents = file_contents.get(base)
            if base and contents and content:
                # 证据内容应与其声明来源文件中的某个片段一致（允许片段比证据更长）
                if not any(content in cc for cc in contents):
                    issues.append({
                        "type": "citation", "severity": _SEV_ERROR,
                        "detail": f"证据 {i + 1} 的内容与其声明的来源「{base}」不一致。",
                    })
        return issues

    def _check_consistency(self, question: str, sub_questions: list[str],
                           evidences: list[dict], draft_answer: str) -> list[dict]:
        """答案一致性：草稿内部矛盾 / 与证据冲突。

        有 LLM 时做语义评审；无 LLM 时做确定性轻量检查（引用标记对齐）。
        无草稿时仅跳过，不阻断。
        """
        issues: list[dict] = []
        if not draft_answer:
            return issues

        # 确定性：引用标记 [n] 不应超出证据数量
        refs = [int(r) for r in re.findall(r"\[(\d+)\]", draft_answer)]
        if refs:
            max_ref = max(refs)
            if max_ref > len(evidences):
                issues.append({
                    "type": "consistency", "severity": _SEV_ERROR,
                    "detail": f"草稿答案引用 [max={max_ref}] 超出证据数量 {len(evidences)}，引用无效。",
                })

        # LLM 语义评审（可选）
        if self.llm_client is not None:
            try:
                consistent, reason = self._llm_consistency(
                    question, sub_questions, evidences, draft_answer)
                if not consistent:
                    issues.append({
                        "type": "consistency", "severity": _SEV_ERROR,
                        "detail": f"答案一致性问题：{reason}",
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("Critic LLM 一致性评审失败，跳过: %s", e)
        return issues

    def _llm_consistency(self, question: str, sub_questions: list[str],
                         evidences: list[dict], draft_answer: str) -> tuple[bool, str]:
        """调用 LLM 判断草稿答案与证据的一致性。返回 (consistent, reason)。"""
        evidence_text = "\n".join(
            f"- [{i + 1}] (来源:{self._evidence_source(e)}) {e.get('content', '')}"
            for i, e in enumerate(evidences[:10])
        )
        user_prompt = (
            f"问题：{question}\n"
            f"子问题：{sub_questions}\n"
            f"证据：\n{evidence_text}\n"
            f"草稿答案：\n{draft_answer}"
        )
        data = llm_util.chat_json(
            self.llm_client,
            [
                {"role": "system", "content": _CONSISTENCY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=self.model,
        )
        consistent = bool(data.get("consistent", True))
        reason = str(data.get("reason", "") or "")
        return consistent, reason

    # ---------------------------------------------------------------- 决策收敛
    def _decide(self, retry_count: int, reflection: str, issues: list[dict],
                question: str) -> dict:
        if retry_count < self.max_retry:
            return {
                "decision": "retry",
                "reflection": reflection,
                "issues": issues,
                "retry_target": self._retry_target(question),
                "refine_instructions": self._refine_instructions(reflection),
            }
        return {
            "decision": "pass",
            "reflection": f"已达重试上限({self.max_retry})，强制收敛到当前证据：{reflection}",
            "issues": issues,
            "retry_target": "retriever_agent",
            "refine_instructions": "",
        }

    @staticmethod
    def _retry_target(question: str) -> str:
        """依据问题与评审意见决定派回哪个子 Agent（缺省回检索 Agent）。"""
        q = (question or "").lower()
        if any(w in q for w in ("最新", "新闻", "实时", "今天", "昨天", "股票", "天气", "热搜")):
            return "web_agent"
        return "retriever_agent"

    @staticmethod
    def _refine_instructions(reflection: str) -> str:
        """把评审意见转成对子 Agent 的补检指令。"""
        if not reflection:
            return "请扩大召回范围并补充检索。"
        return f"根据评审意见补检：{reflection}"

    # ---------------------------------------------------------------- 工具方法
    @staticmethod
    def _evidence_source(e: dict) -> str:
        meta = e.get("metadata", {}) or {}
        sf = meta.get("source_file", "") or meta.get("source", "")
        return str(sf).strip() if sf else ""

    def _covers(self, e: dict, sq: str) -> bool:
        """证据是否覆盖某子问题。

        优先按 retriever_agent 打的 sub_question 标签精确匹配；标签缺失时
        回退到 M1 语义：来源字段存在即视为覆盖。
        """
        tag = e.get("sub_question")
        if tag:
            return str(tag) == sq or str(tag) in sq or sq in str(tag)
        return bool(self._evidence_source(e))

    # ---------------------------------------------------------------- 图节点
    def run(self, state: dict, config: RunnableConfig | None = None) -> dict:
        question = state.get("question", "")
        sub_questions = state.get("sub_questions") or [question]
        evidences = state.get("evidences") or []
        retry_count = int(state.get("retry_count", 0))
        draft_answer = state.get("draft_answer", "") or ""
        result = self.evaluate(question, sub_questions, evidences, retry_count,
                               draft_answer=draft_answer)

        # 派回目标以原始意图为准：仅 web 意图派回 web_agent，其余回检索 Agent
        route = state.get("route") or "retrieve"
        if result.get("decision") == "retry" and route != "web":
            result = {**result, "retry_target": "retriever_agent"}

        # 全局超时硬约束：超过 deadline 强制收敛（不再重试）
        deadline = ((config or {}).get("configurable") or {}).get("deadline")
        if deadline is not None and time.time() >= float(deadline):
            result = {
                **result,
                "decision": "pass",
                "reflection": "已达全局超时，强制收敛到当前证据。",
                "retry_target": "retriever_agent",
                "refine_instructions": "",
            }

        next_retry = retry_count + 1 if result["decision"] == "retry" else retry_count
        return {
            "critic_decision": result["decision"],
            "reflection": result["reflection"],
            "issues": result.get("issues", []),
            "retry_target": result.get("retry_target", "retriever_agent"),
            "refine_instructions": result.get("refine_instructions", ""),
            "retry_count": next_retry,
            "trace": [{
                "node": "critic",
                "event": "agent_reflect",
                "decision": result["decision"],
                "reflection": result["reflection"],
                "issues": result.get("issues", []),
                "retry_target": result.get("retry_target", ""),
                "retry_count": next_retry,
            }],
        }
