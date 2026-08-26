# docs/archive — 历史文档归档区

> 归档原则（对照《enterprise-rag-scale-plan.md》T3.5 文档收敛）：
> 已执行完成的阶段性计划、一次性审计/评审报告、早期缓存/模型实验方案统一归档至此，
> 不参与"当前状态"文档的维护基线。
> **当前状态请以 `../CURRENT_STATUS.md` 为准，架构与配置决策见 `../adr/`。**

## 归档清单与原因

| 文件 | 内容 | 归档原因 |
|------|------|----------|
| `enterprise-upgrade-plan.md` | Phase 0-2 升级计划 | 计划已由 T0-T2 全部落地，被 CURRENT_STATUS 取代 |
| `system-audit-report.md` | 基线安全/架构审计报告 | 一次性审计结论，基线已修复（Phase 0） |
| `code-review-fixes.md` | 代码评审修复记录 | 历史修复项已合入代码 |
| `multi-model-plan.md` | 多模型规划 | 已由 T3.1 模型分级（model_router）落地 |
| `RAG链路分析与优化方案.docx` | 早期链路优化分析 | 一次性分析方案，优化已合入 langchain 引擎 |

## 恢复方式

若需将某归档文档移回 docs/ 根目录参与文档基线，执行：

```bash
git mv docs/archive/<file> docs/<file>
git commit -m "docs: 恢复 <file> 至当前文档基线"
```

## 说明

- 归档仅移动位置、保留 git 历史，不删除任何内容。
- 标记为"已由 XX 落地"的文档，对应实现请查阅 CURRENT_STATUS.md 与 `src/adr/`。
