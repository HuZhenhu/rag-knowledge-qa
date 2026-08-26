# RAG企业级升级 — 总体实施计划

> 日期：2026-07-16  
> 状态：设计阶段  
> 基于：SPEC.md + permission-system-design.md + multi-model-plan.md

---

## 〇、企业级 RAG 能力全景

完整的企业级 RAG 需要覆盖以下 6 大块能力：

### 第一块：基础设施
| 能力 | 现状 | 企业级要求 |
|------|------|-----------|
| 多租户隔离 | ❌ 单租户 | 多企业/团队数据隔离 |
| 权限体系 | ⚠️ admin/viewer | RBAC + 部门 + 文档级 |
| 敏感信息脱敏 | ❌ 无 | 手机号/身份证/薪资脱敏 |
| 审计日志 | ⚠️ 基础 | 完整操作追踪，不可篡改 |

### 第二块：核心RAG链路
| 能力 | 现状 | 企业级要求 |
|------|------|-----------|
| 多模型适配 | ⚠️ 硬编码DeepSeek | 8+厂商适配器热切换 |
| 查询理解 | ✅ 已实现 | 扩展+HyDE+重写 |
| 增量更新 | ⚠️ 有基础 | 文档级/Chunk级精确更新 |
| 缓存 | ❌ 无 | 相同问题不重复计算 |
| 重试/降级 | ⚠️ 简单 | LLM失败自动重试+降级 |
| 检索审查 | ❌ 无 | 过滤无关检索结果 |
| 输出审查Agent | ❌ 无 | 拦截幻觉/不当内容 |

### 第三块：数据与索引
| 能力 | 现状 | 企业级要求 |
|------|------|-----------|
| 多格式支持 | ⚠️ md/docx/pdf | 全格式+图片+表格+扫描 |
| 增量索引 | ⚠️ 基础 | CDC + content hash |
| 知识图谱 | ❌ 无 | 实体关系（GraphRAG） |
| 数据质量管理 | ❌ 无 | 重复/过期/低质检测 |
| 版本管理 | ❌ 无 | 文档多版本回溯 |

### 第四块：安全与合规
| 能力 | 现状 | 企业级要求 |
|------|------|-----------|
| Prompt注入防护 | ⚠️ 有提示词 | 更强检测与拦截 |
| 文件上传安全 | ⚠️ 类型/大小限制 | 病毒扫描+内容检测 |
| API限流 | ✅ 已实现 | 分级限流 |
| 数据加密 | ❌ 无 | 传输+存储加密 |
| 合规审计 | ⚠️ 基础 | 符合GDPR/等保 |

### 第五块：可观测性与运维
| 能力 | 现状 | 企业级要求 |
|------|------|-----------|
| 全链路日志 | ✅ 已实现 | trace贯穿所有环节 |
| 监控告警 | ⚠️ 基础 | 准确率/延迟/错误率 |
| 评测体系 | ✅ 已完成 | 四维+领域+控制变量 |
| 性能分析 | ❌ 无 | 耗时分解 |
| 成本分析 | ❌ 无 | token/API花费统计 |

### 第六块：平台与生态
| 能力 | 现状 | 企业级要求 |
|------|------|-----------|
| Tool/Agent体系 | ❌ 无 | Text-to-SQL/图表等 |
| 多知识库 | ⚠️ 有基础 | 独立管理+权限隔离 |
| 管理后台 | ⚠️ 有 | 可视化配置/监控/审计 |
| 开放API | ✅ 已实现 | SDK+文档+密钥管理 |
| 部署 | ⚠️ 本地 | Docker/K8s/高可用 |
| 扩展插件 | ❌ 无 | loader/tool插件化 |

### 优先级排序
```
第一批（必须）：权限体系 → 数据安全 → 评测调优（进行中）
第二批（核心）：多模型适配 → 增量更新 → 检索/输出审查Agent
第三批（进阶）：多租户 → 知识图谱 → 缓存 → 成本分析
第四批（平台化）：管理后台 → Docker部署 → SDK → 插件化
```

---

## 一、总体路线

```
Phase 1: 权限系统
    │  部门隔离 + 文档权限 + 角色体系
    ▼
Phase 2: 评测数据准备 ✅ 已完成
    │  评测集对齐 + 四维度指标 + 段错误修复
    ▼
Phase 3: 评测调优（进行中）
    │  控制变量法实验 + 四维评测驱动优化
    ▼
Phase 4: 数据安全
    │  敏感信息脱敏 + 上传安全 + 注入防护
    ▼
Phase 5: 审查Agent
    │  输出审查（拦截幻觉）+ 检索审查
    ▼
Phase 6: Tool 体系
    │  Text-to-SQL + 图表生成 + 意图路由
    ▼
Phase 7: 多模型适配
    适配器模式 + 前端配置向导 + 模型效果对比
```

---

## 二、设计决策总览

| 决策点 | 结论 |
|--------|------|
| 文档领域 | 6 个（法律 / AI技术 / 编程开发 / 面试职业 / 学术论文 / 行政表格） |
| 评测维度 | 4 维（文档相关度 / 回答忠实度 / 回答帮助度 / 回答正确度） |
| 评分方式 | LLM-as-Judge（全自动，人工只审核评测集） |
| 领域权重 | 每个领域独立配置，数据驱动优化 |
| 评测集规模 | 300 条自动生成 + 50 条黄金审核 |
| 标准答案 | LLM 自动生成 + 人在审核页面审核 |
| HyDE | 不作为固定开关，作为可对比参数，评测后数据决定 |
| 调优方法 | 控制变量法（每次只改一个参数） |
| 审核方式 | 前端审核页面（不直接看 JSON） |
| 权限范围 | 部门隔离 + 文档级权限（不做租户表、脱敏、chunk级权限） |
| Tool 选择 | LLM 自主判断，不做 if-else 规则 |
| 模型适配 | 适配器模式，每个厂商一个类 |

---

## 三、Phase 1：权限系统

> 基于已有设计文档 `docs/permission-system-design.md`

### 3.1 目标

- 部门数据隔离：同部门用户共享数据，跨部门不可见
- 文档级权限：每个文档可指定可见部门
- 5 级角色体系
- 检索/生成时自动过滤无权限数据

### 3.2 数据模型变更

**users 表（扩展现有）**

```sql
ALTER TABLE users ADD COLUMN department_id TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'viewer';
-- role: super_admin / department_admin / editor / viewer
```

**knowledge_bases 表（扩展现有）**

```sql
ALTER TABLE knowledge_bases ADD COLUMN department_id TEXT DEFAULT '';
ALTER TABLE knowledge_bases ADD COLUMN visibility TEXT DEFAULT 'department';
-- visibility: all / department / private
```

**document_permissions 表（新建）**

```sql
CREATE TABLE IF NOT EXISTS document_permissions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    department_id TEXT DEFAULT '',
    permission TEXT NOT NULL DEFAULT 'read',  -- read / write / admin
    created_at TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES document_registry(id)
);
```

### 3.3 角色体系

```
super_admin         → 全部权限
department_admin    → 本部门 + 下属部门
editor              → 上传文档 + 查询
viewer              → 只查询
```

### 3.4 检索改造

核心改动：查询时带上部门过滤条件

```python
# 原逻辑
results = vector_store.search(query, top_k)

# 新逻辑
results = vector_store.search(
    query, 
    top_k,
    where={
        "department_id": user.department_id
    }
)
```

**改动文件**：
- `src/storage/database.py` — ALTER TABLE + 新建表
- `src/api/routes.py` — 查询接口从 JWT 获取 department_id
- `src/core/retriever.py` — 检索时加权限过滤
- `src/core/langchain_rag.py` — LangChain 引擎同步改造
- `src/api/jwt_auth.py` — JWT payload 加入 department_id
- `frontend/src/views/UserManagement.vue` — 用户管理加部门选择
- `frontend/src/views/DocumentManagement.vue` — 文档管理加权限设置

### 3.5 不做

- ❌ 多租户隔离（tenant 表）
- ❌ 敏感信息脱敏
- ❌ Chunk 级权限标签
- ❌ 审计日志表扩展
- ❌ 部门树（父部门/子部门）

> 以上作为 Phase 1.5 或企业级后续

### 3.6 验证标准

1. A 部门用户检索不到 B 部门的文档
2. 无权限的文档在前端文档列表不显示
3. API 返回结果中不包含其他部门数据

---

## 四、Phase 2：评测数据准备

### 4.1 目标

- 修复现有评测代码的 bug
- 实现四维度 LLM-as-Judge 评测
- 生成 300 条评测数据
- 建立前端审核页面

### 4.2 Bug 修复

**Bug 1：引用检测正则**（`evaluate.py:94`）

```python
# 当前（错误）：匹配 [1][2] 格式
re.search(r"\[\d+\]", answer)

# 修复后：匹配 (文件名.md，章节名) 格式
re.search(r'\([^)]+\.md[，,][^)]*\)', answer)
```

**Bug 2：检索命中检测**（`evaluate.py:83-87`）

```python
# 当前（错误）：在 chunk 文本中搜文件名
retrieved_texts = " ".join(s.get("content", "") for s in sources).lower()
return any(sf.lower() in retrieved_texts for sf in source_files)

# 修复后：检查 metadata 中的 source_file
return any(
    sf.lower() in s.get("metadata", {}).get("source_file", "").lower()
    for s in sources for sf in source_files
)
```

### 4.3 四维度评测指标体系

| 维度 | 评测什么 | LLM 做法 | 调优指向 |
|------|---------|---------|---------|
| ① 文档相关度 | 检索回来的文档有没有关系 | 标记每个 chunk 中相关/无关句子数 | 检索策略、chunk_size |
| ② 回答忠实度 | 回答是否基于检索结果（无幻觉） | 拆解为原子论断 → 逐条在上下文中验证 | Prompt 约束、temperature |
| ③ 回答帮助度 | 回答是否解决了用户问题 | 评估具体性+完整性+冗余度，1-5 分 | 检索质量、生成 Prompt |
| ④ 回答正确度 | 与标准答案对比是否正确 | 对比事实一致性+完整性+无误，1-5 分 | 整个 RAG 链路 |

**综合评分公式**

```
领域总分 = W1×相关度 + W2×忠实度 + W3×帮助度 + W4×正确度
```

### 4.4 文档领域与权重

| 领域 | 文档示例 | 权重配置 |
|------|---------|---------|
| 法律 | 刑法、治安管理处罚法 | W={0.05, 0.25, 0.10, 0.60} |
| AI技术 | 路线图、知识手册、RAG、MCP、HarnessEngineering | W={0.20, 0.10, 0.40, 0.30} |
| 编程开发 | Python学习路线、实战项目 | W={0.20, 0.10, 0.45, 0.25} |
| 面试职业 | 面试知识点、AI简历、框架对比 | W={0.20, 0.10, 0.50, 0.20} |
| 学术论文 | 毕业论文、开题报告、答辩 | W={0.10, 0.30, 0.15, 0.45} |
| 行政表格 | 记录本、审批表、档案袋 | W={0.35, 0.10, 0.25, 0.30} |

> 权重为初始建议值，评测实验室支持实时调整。

### 4.5 评测数据生成

**流程**

```
每个领域随机抽取 50 个 chunks
    ↓
对每个 chunk，LLM 生成 1 个问题 + 标准答案 + 关键词 + 来源文档
    ↓
6 领域 × 50 = 300 条
    ↓
人工在审核页面审查 50 条黄金验证集
    ↓
额外补充 20 条边界测试（知识库没有的问题）
```

**数据结构**

```json
{
  "id": "001",
  "question": "什么是RAG？",
  "expected_answer": "RAG全称Retrieval-Augmented Generation...",
  "expected_keywords": ["检索", "增强", "生成"],
  "source_files": ["AI应用工程师学习路线图.md"],
  "domain": "ai_tech",
  "category": "simple_fact",
  "status": "approved"
}
```

五个 category：
- `simple_fact`：单文档事实查询
- `multi_doc`：跨文档综合查询
- `out_of_scope`：知识库没有的问题
- `vague`：模糊/指代不明的问题
- `reasoning`：需要推理串联的问题

### 4.6 前端审核页面

```
┌─────────────────────────────────────────────┐
│  评测数据审核                                │
│  ┌─────────────────────────────────────────┐│
│  │ 领域：[AI技术 ▼]  状态：[待审核 ▼]        ││
│  │                                         ││
│  │ ┌─────────────────────────────────────┐ ││
│  │ │ #001  simple_fact                   │ ││
│  │ │ Q: 什么是RAG？                      │ ││
│  │ │ A: RAG全称Retrieval-Augmented...    │ ││
│  │ │ 来源：AI应用工程师学习路线图.md       │ ││
│  │ │                                     │ ││
│  │ │ [✏️修改] [✅通过] [❌删除]           │ ││
│  │ └─────────────────────────────────────┘ ││
│  └─────────────────────────────────────────┘│
│                                             │
│  审核进度：12/50   [导出黄金集]              │
└─────────────────────────────────────────────┘
```

### 4.7 文件清单

**新增**：
- `src/core/eval_metrics.py` — 四维度评测函数
- `scripts/generate_eval_data.py` — 评测数据自动生成脚本
- `frontend/src/views/EvalReview.vue` — 审核页面

**修改**：
- `evaluate.py` — Bug 修复 + 集成四维度指标
- `src/api/routes.py` — 审核相关 API
- `frontend/src/router/index.ts` — 新增审核路由

---

## 五、Phase 3：评测调优

### 5.1 目标

- 控制变量法找出每个领域的最优参数组合
- 可视化对比不同配置的效果
- 输出调优报告

### 5.2 调优顺序

```
Step 1: 基线评测（当前参数跑一次，拿 6 领域真实分数）
Step 2: 检索层（chunk_size → chunk_overlap → top_k）
Step 3: 查询层（子查询数 → HyDE → 查询重写）
Step 4: 生成层（Prompt 模板 → temperature）
Step 5: 终局评测（6 领域分别确认最优参数）
```

### 5.3 控制变量法

每次只变一个参数，其他全部固定：

| 参数 | 测试值 |
|------|--------|
| chunk_size | 256, 512, 800, 1024 |
| chunk_overlap | 0, 50, 100, 200 |
| top_k | 3, 5, 7, 10 |
| use_hyde | on, off |
| use_reranker | on, off |
| use_query_expansion | on, off（子查询数量：3, 5） |

### 5.4 HyDE 特殊策略

不设全局开关，按场景测试：

| 场景 | 测试方式 |
|------|---------|
| 短/抽象问题 | HyDE on vs off |
| 关键词明确问题 | HyDE on vs off |
| 法律/学术领域 | HyDE on vs off |
| 各组数据对比后决定各场景开不开 |

### 5.5 评测实验室页面

```
┌─────────────────────────────────────────────────────┐
│  评测实验室                                          │
│                                                   │
│  ┌───────────────────────────────────────────────┐ │
│  │ ① 变量: [chunk_size ▼]                        │ │
│  │ ② 测试值: [256] [512] [800] [+添加]           │ │
│  │ ③ 固定: overlap=50 top_k=5 hyde=off            │ │
│  │ ④ 领域: [全部 ▼]  评测集: [300自动集 ▼]        │ │
│  │ ⑤ 权重: 相关度[20] 忠实度[10] 帮助度[40] 正确度[30]│ │
│  │                                              │ │
│  │ [开始对比评测]  ⏳ 进度: 2/3                   │ │
│  └───────────────────────────────────────────────┘ │
│                                                   │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ │
│  │ 📊 准确率对比 │ │ 📊 检索命中率│ │ ⏱ 延迟对比  │ │
│  │   (柱状图)   │ │   (柱状图)  │ │   (柱状图)  │ │
│  └─────────────┘ └─────────────┘ └─────────────┘ │
│                                                   │
│  ┌─────────────┐ ┌─────────────────────────────┐ │
│  │ 🎯 综合雷达图│ │ 📋 详细对比表               │ │
│  │   (雷达图)   │ │ 指标 | 256 | 512 | 800    │ │
│  │             │ │ 相关度|0.8 |0.85|0.82     │ │
│  │             │ │ 忠实度|0.9 |0.88|0.91     │ │
│  └─────────────┘ └─────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 5.6 文件清单

**新增**：
- `frontend/src/views/EvalLab.vue` — 评测实验室主页
- `frontend/src/components/eval/ParamConfig.vue` — 参数配置组件
- `frontend/src/components/eval/ComparisonCharts.vue` — 图表对比组件
- `frontend/src/composables/useEvalLab.ts` — 评测实验室逻辑

**修改**：
- `src/api/routes.py` — 参数化评测 API + 批量对比 API
- `evaluate.py` — 支持 config_overrides
- `frontend/src/views/Evaluations.vue` — 对比入口
- `frontend/src/router/index.ts` — evallab 路由
- `frontend/package.json` — echarts + vue-echarts

---

## 六、Phase 4：Tool 体系

### 6.1 目标

- 设计统一 Tool 协议
- 实现意图路由（LLM 自主选择 Tool）
- 第一批 Tool：知识检索、数据分析、文档管理
- 为后续扩展预留接口

### 6.2 架构

```
用户提问
    │
    ▼
┌──────────────────────────────────────┐
│  Tool Router                         │
│  可用工具:                           │
│  • search_knowledge — 知识库检索    │
│  • query_data — 数据分析           │
│  • list_documents — 文档管理       │
│  • get_document_info — 文档详情    │
│                                      │
│  LLM 判断意图 → 选择 Tool             │
└──────────────┬───────────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌───────┐ ┌───────┐ ┌─────────┐
│ RAG   │ │ SQL   │ │ 文档    │
│ Tool  │ │ Tool  │ │ Tool    │
└───┬───┘ └───┬───┘ └────┬────┘
    │         │          │
    └─────────┼──────────┘
              ▼
         LLM 汇总回答
```

### 6.3 Tool 协议

```python
class Tool(ABC):
    name: str                    # "query_data"
    description: str             # LLM 用来判断该用哪个工具的描述
    parameters: dict             # JSON Schema 格式的参数定义

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult: ...

class ToolResult:
    content: str                 # 原始结果
    is_error: bool
    metadata: dict               # 耗时、行数等
```

### 6.4 第一批 Tool 清单

| Tool | 场景 | 输入 | 输出 |
|------|------|------|------|
| `search_knowledge` | 知识问答（现有 RAG 包装） | 问题文本 | 检索 chunk 列表 + LLM 生成回答 |
| `query_data` | 数据分析查询 | 自然语言问题 | SQL 查询结果 |
| `list_documents` | 查看文档列表/搜索 | 过滤条件 | 文档列表 |
| `get_document_info` | 文档详情 | 文档 ID | 文档元数据 + chunk 数 |

### 6.5 第二批 Tool（后续扩展）

| Tool | 场景 |
|------|------|
| `generate_chart` | "画个 Q3 销售额柱状图" |
| `web_search` | 知识库没有的信息 |
| `compare_data` | "对比今年和去年的数据" |
| `export_report` | "导出成 Excel" |
| `upload_document` | 上传文档到知识库 |

### 6.6 意图路由实现

```python
ROUTER_SYSTEM_PROMPT = """你是智能助手，可以使用以下工具：

1. search_knowledge: 从知识库文档中检索信息。
   适合"什么是 RAG"、"如何配置 XX"等知识问答。

2. query_data: 从数据库中做数据分析和统计。
   适合"最高/最低/平均/趋势/对比/占比"等需要计算的问题。
   用户问题中包含表格名称或数据关键词时优先使用。

3. list_documents: 列出/搜索文档。
   适合"有哪些文件"、"最近上传了什么"。

4. get_document_info: 查看单个文档的详细信息。

判断用户意图，返回要使用的工具名。不确定时用 search_knowledge。"""


def route(question: str, llm_client) -> tuple[str, dict]:
    """LLM 判断该用哪个 Tool，返回工具名和参数"""
    response = llm_client.chat(
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": question}
        ]
    )
    # 解析 LLM 返回的 tool_name + args
    return tool_name, args
```

### 6.7 关键设计原则

- **Tool 选择交给 LLM，不用 if-else 规则**：关键词匹配不可靠，"帮我分析"可能是分析文档也可能是分析数据
- **支持链式调用**：一个问题可能需要多个 Tool（先查数据再画图）
- **现有 rag_engine 不变**：`search_knowledge` Tool 就是现有 RAG 流程的包装

### 6.8 文件清单

**新增**：
- `src/core/tools/__init__.py`
- `src/core/tools/base.py` — Tool + ToolResult 基类
- `src/core/tools/router.py` — 意图路由
- `src/core/tools/search_knowledge.py` — RAG Tool
- `src/core/tools/query_data.py` — Text-to-SQL Tool
- `src/core/tools/doc_management.py` — 文档管理 Tool
- `src/api/tool_routes.py` — Tool 相关 API

**修改**：
- `src/api/routes.py` — 注册 tool_routes
- `main.py` — 初始化 Tool 注册表

---

## 七、Phase 5：多模型适配

> 基于已有设计文档 `docs/multi-model-plan.md`

### 7.1 目标

- 适配器模式支持主流 LLM 和 Embedding
- 运行时热切换模型
- 前端配置向导
- 用评测实验室对比不同模型效果

### 7.2 LLM 适配器

| 厂商 | 适配方式 |
|------|---------|
| DeepSeek | 继承 OpenAI 适配器，覆盖 base_url |
| OpenAI | 基础适配器，用 `openai` SDK |
| Anthropic Claude | 独立适配器，用 `anthropic` SDK |
| 通义千问 | 继承 OpenAI 适配器 |
| Moonshot/Kimi | 继承 OpenAI 适配器 |
| Google Gemini | 用 `google-genai` SDK |
| Ollama（本地） | 继承 OpenAI 适配器 |

### 7.3 Embedding 适配器

| 方案 | 说明 |
|------|------|
| 本地 sentence-transformers | 免费，384 维 |
| DeepSeek Embedding API | 中文效果好 |
| OpenAI text-embedding-3 | 1536 维 |

### 7.4 核心设计

```python
# 统一接口
class BaseLLM(ABC):
    def chat(self, messages, **kwargs) -> dict: ...
    def chat_stream(self, messages, **kwargs) -> Iterator[str]: ...

class BaseEmbedding(ABC):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def get_dimension(self) -> int: ...

# 单例管理器
class ModelManager:
    def get_llm(self) -> BaseLLM: ...
    def switch_llm(self, provider, model, api_key): ...
    def get_embedding(self) -> BaseEmbedding: ...
```

### 7.5 与评测实验室联动

Phase 5 完成后，评测实验室多一个对比维度——**模型对比**：

```
相同参数配置下，对比 DeepSeek vs OpenAI vs Claude 的四个维度得分
```

---

## 八、验证清单

### Phase 1 验证
- [ ] A 部门用户检索不到 B 部门的文档
- [ ] 无权限文档不在前端列表中显示
- [ ] API 返回结果不包含其他部门数据
- [ ] 用户管理页面可选择部门

### Phase 2 验证
- [ ] 两个 Bug 修复后，引用检测率和检索命中率不再是 0%
- [ ] 四维度评分输出在合理范围（忠实度不应 100%）
- [ ] 300 条评测数据覆盖所有 6 个领域
- [ ] 审核页面可正常通过/修改/删除

### Phase 3 验证
- [ ] 控制变量法实验结果有区分度（不同参数值导致不同分数）
- [ ] 评测实验室图表正常渲染
- [ ] 每个领域选出最优参数

### Phase 4 验证
- [ ] 知识问答问题正常走 RAG Tool
- [ ] 数据分析问题正常走 SQL Tool
- [ ] 意图路由准确率 > 90%

### Phase 5 验证
- [ ] 运行时切换模型，新请求立即生效
- [ ] 前端配置向导可完成模型配置
- [ ] 评测实验室可对比不同模型效果
