# RAG项目代码审查 — 需要修复的问题

> 审查日期：2026-06-09
> 审查结论：7.5/10，架构好，有几个关键问题需要修复

---

## 必须修复（不修不能上线）

### Fix 1：build_index.py 只扫描根目录，遗漏子目录文件

**问题：** `DATA_DIR.glob("*.md")` 只找 data/*.md，不会递归找 data/AI应用开发实战/*.md 和 data/Python实战项目-AI开发助手/*.md。23个文件只索引了10个。

**修复：** 改为递归搜索 `DATA_DIR.rglob("*.md")`

**文件：** build_index.py 第26行

---

### Fix 2：BM25中文分词太粗糙

**问题：** `list(doc)` 按单个字符分词，"什么是RAG"变成["什","么","是","R","A","G"]，BM25检索效果极差。

**修复：** 用jieba分词。
- requirements.txt 加 `jieba`
- retriever.py 的 `_bm25_search` 和 `build_bm25_index` 中用 `jieba.cut()` 代替 `list()`

**文件：** src/core/retriever.py 第64行、第104行

---

### Fix 3：LLM调用没有错误处理

**问题：** generator.py 和 query_understander.py 里的LLM调用没有任何try-catch。API超时、限流、key失效时直接抛异常，服务崩溃。

**修复：** 在以下位置加try-except：
- generator.py 的 generate() 方法
- query_understander.py 的 expand_query() 和 generate_hyde() 方法
- 捕获 Exception，返回降级结果（如"AI服务暂时不可用"），不能让服务崩

**文件：** src/core/generator.py、src/core/query_understander.py

---

## 应该修复（影响功能正确性）

### Fix 4：检索结果去重用前100字符不可靠

**问题：** rag_engine.py 第75行 `key = result.content[:100]`，两个不同chunk如果前100字符相同会被误判重复。

**修复：** 用hash去重：
```python
import hashlib
key = hashlib.md5(result.content.encode()).hexdigest()
```

**文件：** src/core/rag_engine.py 第71-78行

---

### Fix 5：HyDE实现方式有误

**问题：** query_understander.py 把假设回答作为查询词追加到 expanded_queries 里。但HyDE的正确做法是：用假设回答做Embedding向量，然后用这个向量去检索（而不是当文本查询）。

**修复：** 在 rag_engine.py 中，当 use_hyde=True 时：
1. 调用 generate_hyde 得到假设回答文本
2. 用 embedder 把假设回答转成向量
3. 用这个向量直接去 vector_store.query() 检索
4. 把检索结果合并到 all_results 中
而不是把假设回答文本塞进 expanded_queries。

**文件：** src/core/rag_engine.py 第57-59行、src/core/query_understander.py

---

### Fix 6：API路由 stats 接口硬编码

**问题：** routes.py 第79行 `total_documents=8` 是写死的。

**修复：** 从data目录动态统计文件数：
```python
from src.config import DATA_DIR
total_documents = len(list(DATA_DIR.rglob("*.md")))
```

**文件：** src/api/routes.py 第79行

---

## 建议修复（代码规范）

### Fix 7：query_understander.py import位置不规范

**问题：** 第71-72行在函数内部 `import json` 和 `import re`，应该在文件顶部。

**修复：** 移到文件顶部和其他import放在一起。

**文件：** src/core/query_understander.py 第71-72行

---

### Fix 8：Session管理需要持久化或清理

**问题：** session.py 会话历史存在内存里，服务重启丢失，也没有超时清理机制。

**修复：** 两种方案选一个：
- 方案A：用SQLite持久化会话（和database.py整合）
- 方案B：至少加一个定时清理过期session的逻辑（config里已有 SESSION_TIMEOUT_MINUTES=30）

**文件：** src/core/session.py

---

## 修复顺序

1. Fix 1（rglob） — 最紧急，不然知识库文件没被全部索引
2. Fix 2（jieba分词） — BM25效果直接翻倍
3. Fix 3（LLM错误处理） — 上线必须
4. Fix 5（HyDE） — 功能正确性
5. Fix 4（去重hash） — 小改动
6. Fix 6（stats动态统计） — 小改动
7. Fix 7（import位置） — 小改动
8. Fix 8（Session持久化） — 可以后做

每个Fix完成后git commit。
