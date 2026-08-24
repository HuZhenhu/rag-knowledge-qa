---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 12c4ab0be885f937bdaeebb9f27bfe85_e35a08e09efb11f1a65b525400826444
    ReservedCode1: 9RKiltvZ6DmFHDWzYsqCuqrQm/Fn9mXipoVS+XI7gvWKVtjgSFJSyuVbzMP5cO4WoOr0es6FIs8JNH/cXB/yYcn/E0tEgw22egj1MPLlHM2+1N8AFL707/TikakuNGqDTD/uTq60AR4J53kT8ZXVjc3HmFzT0YGQXiqDMfphI6CiB34M9TmJYd54U+c=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 12c4ab0be885f937bdaeebb9f27bfe85_e35a08e09efb11f1a65b525400826444
    ReservedCode2: 9RKiltvZ6DmFHDWzYsqCuqrQm/Fn9mXipoVS+XI7gvWKVtjgSFJSyuVbzMP5cO4WoOr0es6FIs8JNH/cXB/yYcn/E0tEgw22egj1MPLlHM2+1N8AFL707/TikakuNGqDTD/uTq60AR4J53kT8ZXVjc3HmFzT0YGQXiqDMfphI6CiB34M9TmJYd54U+c=
---



# Agentic RAG 升级设计文档

> 日期：2026-08-23
> 状态：已批准，待实施
> 目标代码库：H:\rag-knowledge-qa
> 前置分析：docs 下既有《rag-knowledge-qa 项目分析报告》及 SPEC.md / SPEC-upgrade.md

---

## 1. 背景与目标

### 1.1 现状

rag-knowledge-qa 是一套功能完备的企业级 RAG 系统，已实现：

- 双引擎可切换（`RAG_ENGINE=langchain|original`），LangChain 引擎（LCEL 管道）为默认
- 混合检索（向量 + BM25 + RRF）+ ReRanker + 带引用生成
- M1~M9 九大企业级模块（多格式文档、增量更新、多用户权限、监控、检索优化、大规模、多模态、对话增强、自动评测）
- Vue3 管理后台（8 页面 + WebSocket 实时推送）+ 250 例评测集闭环

### 1.2 升级目标

在保留现有能力与零回归的前提下，新增 **Agentic RAG 能力**，使系统具备：

- **A. 复杂问题拆解规划**：自主把多跳问题拆成子问题，多次检索、交叉验证再回答
- **B. 工具调用**：知识库检索工具集拆细 + 联网搜索（可配置、可降级）
- **C. 验证与自我纠错**：检索/生成后自动验证证据充分性与引用可靠性，发现不足则反思重试

### 1.3 非目标（YAGNI）

- 不引入代码执行/计算沙箱（后续单独迭代）
- 不替换现有双引擎与评测体系，Agentic 为第三条可选路径
- 不做多模态 Agent 能力扩展

---

## 2. 总体架构

### 2.1 架构图

```
RAG_ENGINE=agentic 激活 ──────────────────────────────────────
用户提问
  │
  ▼
[Supervisor 总调度] ──意图判断：直接回答 / 知识库检索 / 联网 / 需要拆解
  │                       │（复用 M8 意图分类做快速通道）
  │     复杂问题
  ▼                       ▼
[Planner]            [Retriever-Agent]     [Web-Agent]
拆解子问题      知识库检索工具集        联网搜索（可配置）
                ① 按类别检索  ② 按章节检索
                ③ 元数据过滤  ④ 列出知识库
                ⑤ 多文档对比检索
  │                       │                 │
  └──────────┬────────────┴─────────────────┘
             ▼
      [Critic 验证] ── 证据充分吗？引用可靠吗？子问题都答了吗？
             │ 不足（≤3 轮反思重试，派回对应子 Agent）
             │ 充分
             ▼
      [Summarizer] ── 汇总多路证据，生成带引用最终答案
             │
             ▼
  带 [文件+章节] 引用答案 + 完整推理过程（WebSocket 实时推送）
```

### 2.2 引擎接入方式

- `config.py` 新增 `RAG_ENGINE` 取值 `agentic`，默认仍为 `langchain`，老功能零回归
- `api/routes.py` 的 query 路由按引擎分发；agentic 走新管线
- Agentic 引擎输出格式与现有引擎对齐（答案 + `[文件+章节]` 引用），保证前端与评测体系兼容

---

## 3. 模块设计

### 3.1 新增目录 `src/core/agentic/`

| 文件 | 职责 |
|------|------|
| `__init__.py` | 导出 `AgenticEngine` 入口类 |
| `graph.py` | LangGraph `StateGraph` 定义：Supervisor 节点、子 Agent 节点、Critic、Summarizer，含循环边与终止条件 |
| `state.py` | `AgentState`（TypedDict + reducer）：问题、计划、子问题列表、证据集、工具调用记录、推理日志、最终答案、重试计数 |
| `supervisor.py` | 总调度 LLM：意图判断、任务路由、决定继续/重试/结束 |
| `planner.py` | 复杂问题拆解为子问题清单 |
| `retriever_agent.py` | 知识库检索工具集（多工具，复用现有 retriever/reranker） |
| `web_agent.py` | 联网搜索工具封装（`AGENT_WEB_SEARCH=true` 才启用） |
| `critic.py` | 验证节点：证据充分性、引用一致性、子问题覆盖度 |
| `summarizer.py` | 汇总生成带引用答案（复用 generator 引用格式与安全指令） |
| `tools/` | `__init__.py`、`kb_tools.py`（知识库检索工具）、`web_search_tool.py`（联网工具） |

### 3.2 AgentState 定义

```python
class AgentState(TypedDict):
    question: str                 # 原始问题
    sub_questions: list[str]      # Planner 拆解出的子问题
    plan: list[dict]              # 计划项（含目标、负责 agent、状态）
    evidences: Annotated[list[dict], operator.add]  # 证据集（reducer 追加）
    tool_calls: Annotated[list[dict], operator.add] # 工具调用记录
    trace: Annotated[list[dict], operator.add]      # 推理日志（前端可视化用）
    reflection: str               # Critic 的反馈意见
    retry_count: int              # 反思重试计数
    final_answer: str             # 最终答案
    citations: list[dict]         # 最终引用列表
```

### 3.3 Supervisor

- 输入：用户问题（+ 多轮会话上下文）
- 输出：路由决策 —— `direct_answer`（直接回答，走快速通道）/ `retrieve`（知识库检索）/ `web`（联网）/ `decompose`（拆解后执行）
- 复用现有 M8 `intent_classifier` 作为第一层快速判断，Supervisor 作为兜底决策层
- 决策理由写入 `trace`，供前端展示

### 3.4 知识库检索工具集（Retriever-Agent）

| 工具名 | 能力 |
|--------|------|
| `kb_search_category` | 按文档类别检索（复用现有类别字段/目录结构） |
| `kb_search_keyword` | 关键词/语义混合检索（复用 retriever + reranker） |
| `kb_filter_metadata` | 按元数据过滤检索（来源、标签等） |
| `kb_list_documents` | 列出知识库现有文档，供 Agent 判断检索方向 |
| `kb_compare_documents` | 多文档对比检索（针对"对比 X 与 Y 的差异"类问题） |

- 每个工具返回结构化结果（文本片段 + 来源 + 章节 + 相关度），统一 schema
- 工具结果计入 `evidences` 与 `trace`

### 3.5 Web-Agent（联网搜索）

- 封装联网搜索 API（优先国内可用：博查 Bocha / Tavily 等），通过 `AGENT_WEB_SEARCH_API_KEY` 配置
- `AGENT_WEB_SEARCH=false` 或未配置 key 时：Web-Agent 节点自动跳过，Supervisor 不路由到 web
- 联网结果需过现有内容防泄露 + Prompt 注入过滤后进入 `evidences`

### 3.6 Critic（验证与纠错）

Critic 基于收集到的证据与草稿答案，输出结构化评审：

1. 证据充分性：每个子问题是否都有对应证据覆盖
2. 引用可靠性：每条引用是否真实存在于知识库、与答案内容一致
3. 一致性：答案是否存在内部矛盾 / 与证据冲突
4. 决策：`pass`（进入 Summarizer）/ `retry`（附改进意见，重试计数 +1）

重试规则：
- `retry_count < 3` 时，Supervisor 依据 Critic 意见派回对应子 Agent 补检
- `retry_count >= 3` 或全局超时（默认 60s）时，强制收敛到 Summarizer 输出当前最优答案

### 3.7 Summarizer

- 汇总 `evidences` 与子问题结论，生成带 `[文件+章节]` 引用的最终答案
- 复用现有 `generator.py` 的引用格式、安全指令与流式能力
- 答案与引用写入 `final_answer` / `citations`

### 3.8 图结构与终止条件

```
START → supervisor ──→ planner ──→ (retriever_agent | web_agent) ──→ critic
   ▲                                                                    │
   └──────────────────────── 反思重试（<3 次）──────────────────────────┘
critic(pass) → summarizer → END
```

- 终止：Critic 判定 `pass`、重试达上限、全局超时、用户中断 四者之一

---

## 4. 前端可视化

### 4.1 WebSocket 事件扩展

现有 `useWebSocket` 管道上新增事件类型：

| 事件 | 内容 |
|------|------|
| `agent_plan` | Supervisor 路由决策 / Planner 子问题清单 |
| `agent_tool_call` | 子 Agent 调用了哪个工具、参数摘要 |
| `agent_evidence` | 检索/联网到的证据片段（来源+章节+摘要） |
| `agent_reflect` | Critic 评审结论与改进意见 |
| `agent_final` | 最终答案与引用 |

### 4.2 Chat 页面推理过程时间线面板

- 在现有 Chat 页面新增"Agent 推理过程"折叠面板，按时间线渲染上述事件
- 展示要素：当前活动节点标识、工具调用、证据卡片、反思次数、最终依据链路
- 消息对象扩展 `agent_trace` 字段（数组，兼容无该字段的旧消息渲染）

### 4.3 兼容性

- 普通引擎（langchain/original）下不产生 agent 事件，前端按现状渲染，无回归

---

## 5. 配置项（config.py 新增）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `RAG_ENGINE` | `langchain` | 新增取值 `agentic` |
| `AGENT_MAX_RETRY` | `3` | Critic 反思重试上限 |
| `AGENT_TIMEOUT` | `60` | 全局执行超时（秒） |
| `AGENT_WEB_SEARCH` | `false` | 是否启用联网搜索 |
| `AGENT_WEB_SEARCH_PROVIDER` | `bocha` | 联网服务商（bocha/tavily） |
| `AGENT_WEB_SEARCH_API_KEY` | 空 | 联网服务 API Key |

---

## 6. 安全与约束

- 复用现有：JWT 鉴权、Prompt 注入防护（30+ 模式）、内容防泄露、输出清洗、限流
- 联网搜索结果必须过内容防泄露 + 注入过滤后才进入证据集
- 工具调用与 Agent 决策全过程写审计日志（复用现有日志/审计机制）
- 超时与重试上限是硬约束，防止 Agent 无限循环与资源耗尽

---

## 7. 测试与评测

### 7.1 单元/集成测试（新增 `tests/test_agentic/`）

| 测试文件 | 覆盖 |
|----------|------|
| `test_state.py` | AgentState 结构与 reducer 行为 |
| `test_supervisor.py` | 路由决策（mock LLM） |
| `test_planner.py` | 复杂问题拆解 |
| `test_kb_tools.py` | 各检索工具返回 schema 与结果 |
| `test_critic.py` | pass/retry 判定、重试计数 |
| `test_graph.py` | 端到端图执行、终止条件（含超时/重试上限） |

### 7.2 评测兼容

- 现有 250 例评测集在 `RAG_ENGINE=agentic` 下对齐输出格式后可跑（复用 evaluate.py）
- 新增 Agentic 专项评测维度：规划正确率、工具选择合理性、平均反思次数、最终引用正确率

### 7.3 回归保障

- 默认引擎仍为 `langchain`，现有 433 个 pytest 用例不受影响
- 已知问题：现有测试存在跨文件全局状态污染（52 失败为隔离性问题），本轮不修复，但新增 agentic 测试文件需自带独立 fixture，避免加剧污染

---

## 8. 实施里程碑

| 里程碑 | 内容 | 验收标准 |
|--------|------|----------|
| M1 | LangGraph 骨架 + Supervisor + 基础检索 Agent | `RAG_ENGINE=agentic` 下简单单跳问题闭环可答，带引用 |
| M2 | 知识库检索工具拆细 + Planner | 多跳/对比问题能拆解并分步检索 |
| M3 | Critic + 反思重试循环 | 证据不足时自动补检，重试 ≤ 3 次收敛 |
| M4 | Web-Agent 联网搜索 | 配置 key 后能联网补充；无 key 自动降级 |
| M5 | 前端推理过程时间线可视化 | WebSocket agent 事件在前端正确渲染 |
| M6 | 评测适配 + 端到端测试 + 文档 | 250 例评测集在 agentic 引擎可跑；新增测试通过；README/SPEC 更新 |

每个里程碑独立可验证，全部完成后统一收尾。

---

## 9. 风险与应对

| 风险 | 应对 |
|------|------|
| LangGraph 引入新依赖导致现有环境受影响 | 隔离新增依赖，先在 M1 验证兼容后再全量启用 |
| Agent 检索产生过多轮次导致延迟升高 | 重试/超时硬上限 + 前端展示过程（延迟可解释） |
| 联网搜索质量参差/幻觉 | 结果过过滤 + Critic 验证 + 标注来源 |
| 测试污染加剧 | 新增测试独立 fixture，不碰现有全局状态 |
| 大模型能力不足导致拆解/验证不稳定 | 提供确定性兜底（意图分类快速通道 + 超限强制收敛） |

---

## 10. 交付物清单

- 新增 `src/core/agentic/` 全套模块
- `config.py` 新增配置项与 `RAG_ENGINE=agentic` 支持
- `api/routes.py` 引擎分发改造
- 前端 Chat 推理过程时间线面板 + WebSocket 事件扩展
- 新增 `tests/test_agentic/` 测试套件
- 评测兼容验证 + 专项评测维度
- README / SPEC-upgrade 更新（记录 M10 升级）
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
