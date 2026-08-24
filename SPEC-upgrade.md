# RAG智能问答系统 — 企业级升级计划

> 日期：2026-06-09
> 基于代码审查后的当前状态，规划9个模块的升级
> 预计总时间：3-5天（借助Claude Code）

---

## 模块总览

| 序号 | 模块 | 优先级 | 预计时间 | 依赖 | 状态 |
|------|------|--------|---------|------|------|
| M1 | 多格式文档处理 | P0 | 6-8小时 | 无 | ✅ 完成 |
| M2 | 增量更新 | P0 | 4-6小时 | M1 | ✅ 完成 |
| M3 | 多用户权限 | P0 | 4-6小时 | 无 | ✅ 完成 |
| M4 | 生产监控 | P0 | 3-4小时 | 无 | ✅ 完成 |
| M5 | 检索质量优化 | P1 | 4-5小时 | M1 | ✅ 完成 |
| M6 | 大规模数据支撑 | P1 | 4-6小时 | M1 | ✅ 完成 |
| M7 | 多模态能力 | P1 | 5-7小时 | M1 | ✅ 完成 |
| M8 | 对话能力增强 | P1 | 3-4小时 | 无 | ✅ 完成 |
| M9 | 回答质量自动评测 | P1 | 2-3小时 | 无 | ✅ 完成 |

---

## M1：多格式文档处理 ✅

### 目标
支持md/txt/docx/pdf/xlsx/图片六种格式，loader插件化，新增格式不改核心代码。

### 当前状态
- 只有 MarkdownLoader
- build_index.py 只处理 .md 文件
- splitter.py 只有 MarkdownSplitter

### 改造内容

#### M1.1：Loader插件机制

改造 src/core/loaders/base.py，定义统一接口：

```
BaseLoader（抽象基类）
├── can_handle(file_path) → bool  # 能否处理该文件
├── load(file_path) → list[DocumentElement]  # 加载文件
└── supported_extensions → list[str]  # 支持的扩展名

DocumentElement（数据类）
├── content: str  # 文本内容
├── metadata: dict  # 元数据（source、page、content_type等）
└── element_type: str  # text/table/image/list
```

每种格式实现这个接口。配置文件或自动检测决定用哪个loader。

#### M1.2：MarkdownLoader 改造

当前实现基本可用，需要增强：
- 保留标题层级信息到metadata（h1/h2/h3）
- 识别代码块，标记 content_type="code"
- 识别列表，标记 content_type="list"
- 识别图片链接，提取图片路径

#### M1.3：TxtLoader

纯文本加载，简单实现：
- 按段落分隔（双换行）
- 检测编码（utf-8/gbk/gb2312）
- metadata标记 content_type="text"

#### M1.4：DocxLoader

用 python-docx 实现：
- 提取段落文本，保留段落分隔
- 提取表格，转成markdown格式，整体作为一个DocumentElement，标记 element_type="table"
- 提取图片，保存到images/目录，生成描述文本（用多模态大模型或标记为待处理）
- 提取标题样式信息到metadata

关键原则：表格不切分，整体保留。

#### M1.5：PDFLoader

用 pdfplumber 实现：
- 按页提取文本
- 按页提取表格（pdfplumber的extract_tables），转成markdown格式
- 提取图片（pdfplumber的images属性）
- 处理多栏排版（检测页面是否有两栏布局，分别提取）
- 页眉页脚过滤（通过y坐标判断，顶部和底部的文字不提取）

关键原则：表格不切分，跨页表格尝试合并。

#### M1.6：ImageLoader

图片处理：
- OCR路线：用 PaddleOCR 或 pytesseract 提取文字
- 多模态路线：用DeepSeek-VL或类似模型生成图片描述
- 两种路线可配置切换
- 生成的描述文本作为一个DocumentElement，标记 element_type="image_description"
- metadata记录原始图片路径

#### M1.7：Splitter改造

当前的 MarkdownSplitter 需要升级为通用的 SmartSplitter：
- 根据DocumentElement的element_type选择切片策略
- text类型：按标题切+字数兜底（当前逻辑）
- table类型：不切分，整体作为一个chunk
- image_description类型：不切分，整体作为一个chunk
- code类型：按函数/类切分（可选，先不实现）
- list类型：按列表项切分

#### M1.8：build_index.py 改造

改造为通用的索引构建脚本：
- 自动扫描data/目录下所有支持格式的文件
- 根据文件扩展名选择对应的loader
- 统计每种格式的文件数和chunk数
- 支持指定目录构建（如只索引某个子目录）

#### M1.9：依赖更新

requirements.txt 需要新增：
- python-docx（docx支持）
- pdfplumber（pdf支持）
- paddleocr 或 pytesseract（OCR，可选）
- chardet（编码检测）

### 验收标准
- data/目录下的md、docx、pdf文件都能被索引
- 表格内容被完整保留（不被切断）
- build_index.py 输出每种格式的统计信息
- 问一个只有docx/pdf里才有的信息，能正确回答

### Git commit
`feat(core): M1 多格式文档处理 — loader插件化 + docx/pdf/图片支持`

---

## M2：增量更新 ✅

### 目标
新文档上传后自动索引，不重建整个向量库。支持文档删除和更新。

### 当前状态
- build_index.py 每次都清空重建
- 没有文档注册表
- 没有文件变化检测

### 改造内容

#### M2.1：文档注册表

在SQLite中建一张 document_registry 表：

```
document_registry
├── id TEXT PRIMARY KEY  # 文档ID
├── filename TEXT  # 文件名
├── file_path TEXT  # 文件路径
├── file_hash TEXT  # 文件内容的MD5
├── file_type TEXT  # 文件类型（md/docx/pdf等）
├── file_size INTEGER  # 文件大小
├── chunk_count INTEGER  # 切片数量
├── status TEXT  # indexed/updating/deleted/error
├── indexed_at TIMESTAMP  # 索引时间
├── updated_at TIMESTAMP  # 更新时间
├── error_message TEXT  # 错误信息（如有）
```

#### M2.2：文件变化检测

实现一个 DocumentScanner：
- 扫描data/目录下所有支持格式的文件
- 计算每个文件的MD5 hash
- 和document_registry对比：
  - 新文件（registry中没有）→ 标记为待索引
  - 已修改文件（hash变化）→ 标记为待更新
  - 已删除文件（磁盘上没有但registry有）→ 标记为待删除

#### M2.3：增量索引引擎

实现 IncrementalIndexer：
- 待索引文件：解析→切片→Embedding→追加到向量库
- 待更新文件：先删除旧chunk→重新解析→切片→Embedding→插入新chunk
- 待删除文件：从向量库中删除所有chunk→从registry中删除记录
- 每次操作都更新registry的状态

#### M2.4：build_index.py 改造

增加两种模式：
- 全量模式：`python build_index.py --full`（清空重建，当前逻辑）
- 增量模式：`python build_index.py`（默认，只处理变化的文件）

增量模式流程：
1. DocumentScanner扫描文件变化
2. IncrementalIndexer处理变化
3. 输出统计：新增X个、更新X个、删除X个

#### M2.5：API接口

新增REST接口：
- POST /api/v1/index/scan — 手动触发文件扫描
- POST /api/v1/index/sync — 手动触发增量同步
- GET /api/v1/index/status — 查看索引状态（待处理X个文件）
- POST /api/v1/documents/upload — 上传文件后自动触发增量索引

#### M2.6：自动触发

- 文件上传到data/目录后，自动触发增量索引
- 可选：定时扫描（每5分钟检查一次文件变化）
- Webhook触发（文件系统变化时通知）

### 验收标准
- 在data/目录新增一个md文件，运行 build_index.py，只索引新文件（不重建旧的）
- 删除一个已索引的文件，运行 build_index.py，对应chunk从向量库中删除
- 修改一个已索引的文件，运行 build_index.py，旧chunk被替换为新chunk
- API接口 /index/status 能返回当前索引状态

### Git commit
`feat(core): M2 增量更新 — 文档注册表 + 变化检测 + 增量索引`

---

## M3：多用户权限 ✅

### 目标
支持多用户、多知识库、文档级权限控制。

### 当前状态
- 只有简单的API Key鉴权
- 没有用户体系
- 没有知识库隔离

### 改造内容

#### M3.1：用户体系

SQLite建 users 表：

```
users
├── id TEXT PRIMARY KEY
├── username TEXT UNIQUE
├── password_hash TEXT  # bcrypt加密
├── role TEXT  # admin/editor/viewer
├── created_at TIMESTAMP
├── last_login TIMESTAMP
├── is_active BOOLEAN
```

角色权限：
- admin：全部权限（管理用户、管理知识库、上传删除文档、查询）
- editor：上传删除文档、查询
- viewer：只能查询

#### M3.2：JWT认证

- 登录接口：POST /api/v1/auth/login → 返回JWT token
- 注册接口：POST /api/v1/auth/register（admin可关闭开放注册）
- token过期时间：24小时
- 刷新接口：POST /api/v1/auth/refresh

#### M3.3：多知识库

SQLite建 knowledge_bases 表：

```
knowledge_bases
├── id TEXT PRIMARY KEY
├── name TEXT  # 知识库名称
├── description TEXT
├── owner_id TEXT  # 创建者
├── created_at TIMESTAMP
├── document_count INTEGER
├── chunk_count INTEGER
```

API支持指定知识库：
- POST /api/v1/query → body中加 kb_id 字段
- 不指定则用默认知识库

#### M3.4：文档级权限

SQLite建 document_permissions 表：

```
document_permissions
├── id TEXT PRIMARY KEY
├── document_id TEXT
├── user_id TEXT  # 或 role
├── permission TEXT  # read/write/admin
```

检索时过滤：只返回用户有权限的文档的chunk。

#### M3.5：操作审计日志

SQLite建 audit_logs 表：

```
audit_logs
├── id INTEGER PRIMARY KEY AUTOINCREMENT
├── user_id TEXT
├── action TEXT  # query/upload/delete/login
├── resource_type TEXT  # document/knowledge_base/user
├── resource_id TEXT
├── details TEXT  # JSON格式的详情
├── ip_address TEXT
├── created_at TIMESTAMP
```

每次API调用自动记录。

### 验收标准
- 注册→登录→获取token→用token调用API，流程通
- 不同角色权限正确（viewer不能上传，editor不能管理用户）
- 多个知识库之间数据隔离
- audit_logs表有完整的操作记录

### Git commit
`feat(auth): M3 多用户权限 — 用户体系 + JWT + 多知识库 + 审计日志`

---

## M4：生产监控 ✅

### 目标
结构化日志、指标监控、告警、调用链追踪。

### 当前状态
- 有基础的logger.py
- 没有指标统计
- 没有告警

### 改造内容

#### M4.1：结构化日志改造

改造 src/api/logging_config.py：
- 日志格式统一为JSON
- 每条日志包含：timestamp、level、message、module、request_id
- 请求日志：method、path、status_code、latency_ms
- LLM调用日志：model、prompt_tokens、completion_tokens、latency_ms
- 检索日志：query、top_k、results_count、retrieval_ms

#### M4.2：指标收集

实现 MetricsCollector：
- 计数器：total_queries、total_errors、total_tokens_used
- 直方图：query_latency_ms、retrieval_latency_ms、generation_latency_ms
- 仪表盘：active_sessions、vector_store_size
- 每个指标按时间窗口（1分钟/5分钟/1小时）聚合

#### M4.3：告警规则

实现 AlertManager：
- 错误率 > 5%（1分钟内）→ 告警
- 平均延迟 > 3000ms（5分钟内）→ 告警
- token消耗 > 日预算80% → 预警
- token消耗 > 日预算100% → 告警
- 向量库为空 → 告警
- 告警方式：日志 + 可选的webhook通知

#### M4.4：调用链追踪

每次问答生成一个trace_id，贯穿整个链路：
- 查询理解阶段：扩展了哪些子查询、识别了哪些实体
- 检索阶段：每路检索返回了多少结果、RRF融合后的排序
- 重排序阶段：ReRanker的打分变化
- 生成阶段：prompt内容、LLM返回、token用量

trace数据存SQLite的 traces 表，可通过API查询：
- GET /api/v1/traces/{trace_id} — 查看完整链路

#### M4.5：监控API

新增接口：
- GET /api/v1/metrics — 返回当前指标（JSON格式）
- GET /api/v1/metrics/prometheus — Prometheus格式（可选）
- GET /api/v1/alerts — 返回最近的告警记录

### 验收标准
- 日志文件是JSON格式，每条包含request_id和latency
- /api/v1/metrics 返回实时指标
- 模拟高错误率时告警触发
- /api/v1/traces/{id} 能看到完整调用链路

### Git commit
`feat(ops): M4 生产监控 — 结构化日志 + 指标 + 告警 + 链路追踪`

---

## M5：检索质量优化 ✅

### 目标
提升检索精准度，支持缓存、权重调优、反馈闭环。

### 改造内容

#### M5.1：混合检索权重可调

当前向量检索和BM25是等权重的RRF融合。改为可配置：
- config中加 HYBRID_VECTOR_WEIGHT 和 HYBRID_BM25_WEIGHT
- RRF公式改为加权版：weight_vector/(k+rank_vector) + weight_bm25/(k+rank_bm25)
- 不同场景可调：技术文档偏BM25（关键词精确匹配重要）、通用问答偏向量

#### M5.2：检索结果缓存

实现 QueryCache：
- 用LRU缓存（cachetools库）存储最近1000个查询的结果
- 缓存key：查询文本的hash + top_k
- 缓存过期：1小时
- 命中缓存时跳过检索和重排序，直接返回

#### M5.3：ReRanker模型可切换

当前写死了bge-reranker-base。改为可配置：
- config中加 RERANKER_MODEL
- 支持本地模型（sentence-transformers）和API模型（Cohere Rerank）
- 本地模型适合低延迟场景，API模型效果更好

#### M5.4：检索阈值过滤

当前返回top_k个结果，不管相关度多低都返回。改为：
- 加 RELEVANCE_THRESHOLD 配置（如0.3）
- 分数低于阈值的chunk不塞进prompt
- 全部低于阈值时返回"知识库中未找到相关信息"

#### M5.5：反馈闭环

利用用户的赞/踩反馈优化检索：
- 用户点"没用"的问答记录到 feedback 表
- 定期分析踩得多的查询，找出检索失败的模式
- 可选：用反馈数据微调ReRanker（进阶）

### 验收标准
- 相同问题第二次查询明显更快（缓存命中）
- 调低BM25权重后，精确关键词检索的排名下降（权重生效）
- 问一个知识库里没有的问题，返回"未找到相关信息"而不是胡说

### Git commit
`feat(search): M5 检索优化 — 权重调优 + 缓存 + 阈值过滤 + 反馈闭环`

---

## M6：大规模数据支撑 ✅

### 目标
支撑万级文档、百万级chunk的检索。

### 改造内容

#### M6.1：向量数据库抽象层

在 vector_store.py 上加一层抽象，支持多种后端：
- ChromaDB（当前，适合开发和小规模）
- Milvus（生产级，支持分布式）
- FAISS（高性能，适合单机大规模）

通过 config 的 VECTOR_STORE_BACKEND 切换。

#### M6.2：批量索引优化

当前build_index是一个文件一个文件处理的。改为：
- 多文件并行加载（ThreadPoolExecutor）
- 批量Embedding（一次encode 100个chunk，比一个一个encode快10倍）
- 批量写入向量库（ChromaDB的add支持批量）

#### M6.3：文档去重

索引前检测重复文档：
- 文件hash去重（完全相同的文件不重复索引）
- 内容相似度去重（Embedding相似度>0.95的文档标记为疑似重复）
- 去重报告（列出疑似重复的文档对）

#### M6.4：索引分片

大量数据时按知识库分片：
- 每个知识库一个独立的ChromaDB collection
- 检索时只查指定知识库的collection
- 跨知识库检索时并行查多个collection合并结果

### 验收标准
- 批量索引100个文件比逐个处理快5倍以上
- 重复文件不会被重复索引
- 向量库后端可通过配置切换

### Git commit
`feat(scale): M6 大规模支撑 — 向量库抽象 + 批量索引 + 去重 + 分片`

---

## M7：多模态能力 ✅

### 目标
支持图片检索、图表理解、表格问答。

### 改造内容

#### M7.1：图片索引

图片存入知识库后能被检索到：
- 图片存images/目录
- 用多模态大模型生成描述文本
- 描述文本Embedding存入向量库
- metadata记录原始图片路径
- 检索到图片chunk时，返回描述+图片URL

#### M7.2：图表理解

识别图表类型和数据：
- 柱状图：提取x轴标签、y轴数值
- 折线图：提取趋势描述
- 饼图：提取各部分占比
- 用多模态大模型生成结构化描述

#### M7.3：表格问答

表格chunk支持自然语言查询：
- 存入时同时生成自然语言描述（"张三在技术部，薪资15000"）
- 检索时自然语言描述和原始表格数据都参与匹配
- 回答时可以引用表格中的具体数据

#### M7.4：多模态Embedding（可选）

用CLIP等模型实现图文统一向量空间：
- 文字和图片都能转成向量
- 支持"以图搜图"和"以文搜图"
- 需要换Embedding模型，影响已有索引

### 验收标准
- 上传一张包含文字的图片，能通过OCR提取并索引
- 问"有哪些表格"，能返回表格chunk
- 图表类型的图片能被正确描述

### Git commit
`feat(multimodal): M7 多模态 — 图片索引 + 图表理解 + 表格问答`

---

## M8：对话能力增强 ✅

### 目标
多轮对话更智能，支持摘要、追问、导出。

### 改造内容

#### M8.1：对话摘要

当对话历史超过5轮时，用LLM压缩：
- 把前N轮对话压缩成一段摘要
- 摘要作为一个system message注入prompt
- 保留最近3轮完整对话 + 前面的摘要

#### M8.2：主动追问

当检索结果不够好时，主动问用户：
- 检测条件：所有chunk的ReRank分数都低于阈值
- 用LLM生成追问："您能再具体描述一下您想了解的方面吗？"
- 追问不触发RAG流程，等用户补充后重新检索

#### M8.3：意图识别

判断用户当前轮的意图：
- 查询：正常RAG流程
- 追问：基于上一轮回答继续深入
- 闲聊：不走RAG，直接LLM回答
- 反馈："这个回答不对"→记录反馈，重新检索

#### M8.4：对话导出

- GET /api/v1/sessions/{session_id}/export
- 返回格式：Markdown / JSON / PDF（可选）
- 包含完整对话记录和引用来源

### 验收标准
- 连续问10个问题后，回答仍然有上下文连贯性
- 问一个知识库没有的模糊问题时，系统主动追问
- 导出的对话记录格式完整可读

### Git commit
`feat(chat): M8 对话增强 — 摘要 + 追问 + 意图识别 + 导出`

---

## M9：回答质量自动评测 ✅

### 目标
定期自动评测回答质量，质量下降时告警。

### 改造内容

#### M9.1：评测集扩展

evaluation/test_qa.json 从10个扩展到30个：
- 10个简单事实查询（"什么是RAG"）
- 10个多文档综合查询（"RAG和Fine-tuning的区别"）
- 5个边界情况（知识库里没有的）
- 5个模糊查询（"帮我总结一下"）

#### M9.2：自动评测脚本

evaluate.py 增强：
- 自动跑完评测集的每个问题
- 对比生成答案和标准答案的语义相似度
- 计算指标：检索命中率（标准答案来源是否被检索到）、回答准确率、引用正确率
- 输出评测报告（Markdown格式）

#### M9.3：评测定时任务

- 可通过cron或APScheduler定时运行
- 每天凌晨跑一次评测
- 结果存SQLite的 evaluations 表
- 准确率下降超过5%时告警

#### M9.4：评测对比

- 对比不同版本的评测结果
- 记录每次模型/prompt/参数变更前后的评测分数
- 生成趋势图（可选）

### 验收标准
- 评测集30个问题全部跑完
- 输出准确率数字和详细报告
- 故意改坏prompt后，评测分数明显下降

### Git commit
`feat(eval): M9 自动评测 — 评测集扩展 + 定时评测 + 质量告警`

---

## 执行顺序建议

```
Day 1：M1（多格式文档）— 工作量最大，优先做        ✅ 已完成
Day 2：M2（增量更新）+ M3（多用户权限）            ✅ 已完成
Day 3：M4（生产监控）+ M8（对话增强）              ✅ 已完成
Day 4：M5（检索优化）+ M6（大规模支撑）            ✅ 已完成
Day 5：M7（多模态）+ M9（自动评测）                ✅ 已完成
```

每个模块独立可测，不依赖其他模块的完成。可以按优先级跳着做。

### 完成记录
- Phase 0：修复关键缺陷（多轮对话历史注入 + HybridRetriever接入 + 可配置开关）
- M1-M5：2026-06-11 通过三代理工作流（推进Agent+验收Agent）完成实施和测试
- M6-M9：2026-06-11 通过三代理工作流完成实施和测试
- 测试结果：385/386通过（99.74%），1个跳过
- Git提交：14个commit，按模块独立提交

---

## M10：Agentic RAG 升级 ✅

> 日期：2026-08-23
> 设计文档：`docs/superpowers/specs/2026-08-23-agentic-rag-design.md`
> 备份：`D:\rag-knowledge-qa_backup_20260823`

### 目标

在现有 `langchain` / `original` 双引擎基础上，新增第三条引擎 `RAG_ENGINE=agentic`：
基于 LangGraph 实现 Supervisor 多 Agent 编排（规划拆解 + 知识库/联网工具调用 + Critic 验证纠错 + Summarizer 汇总），
前端可视化 Agent 推理过程，评测兼容。

### M10.1：Agentic 引擎（src/core/agentic/）

- `state.py` — `AgentState`：trace / tool_calls / retry_count / citations 等字段
- `graph.py` — LangGraph 图编排：`start → supervisor → planner → retriever_agent → critic → summarizer`（含 retry 回路）
- `supervisor_agent.py` — 路由决策（知识库检索 / 联网搜索 / 直接回答）
- `planner_agent.py` — 复杂问题拆分为子问题，制定检索计划
- `retriever_agent.py` — 知识库混合检索工具 + 证据带回 `file` + `section`
- `web_agent.py` — Tavily 联网搜索工具（受 `AGENT_WEB_SEARCH` 开关控制）
- `critic_agent.py` — 反思校验：证据充分性 / 相关性 / 覆盖度，不足时 retry（上限 `AGENT_CRITIC_MAX_RETRY`）
- `summarizer_agent.py` — 汇总生成带引用标注（citations 含 file / section / source_url）
- `AgenticEngine` — 统一入口 `query()` / `query_stream()`，返回 `AgenticRAGResponse`
  （answer / sources / timing / agent_trace，字段对齐既有 `LangChainRAGResponse` / `RAGResponse`）

### M10.2：推理过程可视化

- 后端 `main.py` 通过 WebSocket 推送五类 agent 事件：`agent_plan / agent_tool_call / agent_evidence / agent_reflect / agent_final / user_question`
- 前端新增 `AgentTraceTimeline.vue` 时间线面板，集成于 `ChatMessage.vue` / `Chat.vue`
- `useWebSocket.ts` / `useChat.ts` / `types/index.ts` 扩展五类事件类型
- 普通引擎（无 `_last_agent_trace`）不推送 agent 事件，前端无回归

### M10.3：评测兼容与专项维度（evaluate.py / eval_metrics.py）

- 250 例评测集（`evaluation/generated_cases_250.json`）在 `RAG_ENGINE=agentic` 下复用 `evaluate.py` 跑通
- `eval_metrics.py` 新增 `evaluate_agentic_dimensions`，基于 `agent_trace` 计算四个专项维度：
  - **规划正确率**（planning_accuracy）：子问题 / 计划与最终答案的相关性
  - **工具选择合理性**（tool_choice_score）：知识库 / 联网工具选择是否匹配问题类型
  - **平均反思次数**（retry_count）：critic 重试次数
  - **最终引用正确率**（final_citation_accuracy）：citations 与最终证据来源的一致性
- `evaluate.py` 新增 `--test-cases` / `--limit` / `--no-four-dim` 参数，报告新增「Agentic 专项评测维度」章节

### M10.4：配置与用法

```bash
# 启用 Agentic 引擎
RAG_ENGINE=agentic python main.py

# 关闭联网搜索（仅知识库）
AGENT_WEB_SEARCH=false RAG_ENGINE=agentic python main.py

# 评测（250 例全量）
RAG_ENGINE=agentic python evaluate.py --test-cases evaluation/generated_cases_250.json --no-four-dim
```

关键配置（`.env` / `src/config.py`）：`RAG_ENGINE=agentic`、`AGENT_WEB_SEARCH`、`TAVILY_API_KEY`、`AGENT_CRITIC_MAX_RETRY`

### 验收标准

- [x] Agentic 引擎真实装配跑通（真实 LLM / 检索 / 生成，含 Tavily 联网配置）
- [x] 250 例评测集在 agentic 下对齐输出格式可跑，四个专项维度产出
- [x] 普通引擎（langchain）回归无异常，agentic 维度仅对 agentic 引擎生效
- [x] README.md / SPEC-upgrade.md 新增 Agentic 章节
- [x] 修复 Dashboard.vue getStatusType 未使用 TS6133 基线问题

### Git commit

`feat(agentic): M10 Agentic RAG 升级 — LangGraph 多Agent引擎 + 前端可视化 + 评测专项维度 + 文档`

---

## 版本控制规范

### 阶段提交约定

- 每完成一个阶段（里程碑 / M 模块 / 缺陷修复 / 文档改动）即 git 提交，并推送远程 `origin`（GitHub）
- 每个 commit 语义单一、可独立回滚，禁止攒多个改动一起提交

### commit message 约定

采用 `feat(scope)` / `fix(scope)` / `docs` / `chore` 前缀 + 中文描述：

| 前缀 | 适用场景 | 示例 |
|---|---|---|
| `feat(scope)` | 新功能 | `feat(agentic): M10 Agentic RAG 升级` |
| `fix(scope)` | 缺陷修复 | `fix(retrieval): 修复混合检索权重` |
| `docs` | 文档改动 | `docs: 版本控制规范` |
| `chore` | 构建 / 配置 / 杂项 | `chore: 更新 .gitignore` |

### .gitignore 即时更新

- 新增的测试残留 / 临时产物（如 `.playwright-cli/`、`csdn_rag.html`、`milvus_*.html` 等）即时写入 `.gitignore`
- 每次提交前执行 `git status` 检查，确保工作区无未跟踪的临时文件

### 里程碑示例：2026-08-24 六 commit 拆分提交

将未提交改动按语义拆分为 6 个 commit 并推送 `origin` 成功：

| commit | 语义归类 | 文件数 |
|---|---|---|
| `1ce1bc2` | feat(agentic)：Agentic 引擎 | 25 |
| `52ac712` | feat(retrieval)：检索优化 | 3 |
| `1cb4350` | feat(frontend)：前端可视化 | 10 |
| `0781aae` | feat(eval)：评测 | 23 |
| `838c527` | docs：文档 | 7 |
| `bb53a84` | chore：更新 .gitignore | 1 |

拆分提交后工作区干净，无未提交改动，`origin/master` 与本地一致。
