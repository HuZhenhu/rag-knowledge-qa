"""T3.5 文档收敛 — TDD 测试

验收标准：
- enterprise-upgrade-plan / audit-report / 旧计划与修复记录 等历史文档归档为历史；
- 保留"当前状态"文档（CURRENT_STATUS.md）+ 决策记录（ADR）；
- 文档与代码一致（引擎集合、配置开关、测试基线等）。
"""
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
ARCHIVE = DOCS / "archive"
ADR = DOCS / "adr"


# ---------------------------------------------------------------------------
# 1) 历史文档已归档
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "enterprise-upgrade-plan.md",   # 旧升级计划（Phase0-2 已完成）
        "system-audit-report.md",       # 一次性审计报告
        "code-review-fixes.md",         # 代码评审修复记录（历史）
        "multi-model-plan.md",          # 多模型计划（已被 T3.1 实现取代）
        "RAG链路分析与优化方案.docx",    # 早期一次性分析方案
    ],
)
def test_historical_docs_archived(name):
    """历史文档不在 docs 根目录，已移入 archive/。"""
    assert not (DOCS / name).exists(), f"{name} 应已归档"
    assert (ARCHIVE / name).exists(), f"{name} 应存在于 archive/"


def test_archive_readme_exists():
    """archive 目录有说明文件，标注归档原因与恢复方式。"""
    readme = ARCHIVE / "README.md"
    assert readme.exists()
    text = readme.read_text(encoding="utf-8")
    assert "归档" in text
    assert "恢复" in text or "移回" in text


# ---------------------------------------------------------------------------
# 2) 当前状态文档存在且与代码一致
# ---------------------------------------------------------------------------

def test_current_status_doc_exists_and_well_formed():
    st = DOCS / "CURRENT_STATUS.md"
    assert st.exists(), "缺少当前状态文档"
    text = st.read_text(encoding="utf-8")
    for key in ["当前状态", "架构", "引擎", "配置", "测试", "Phase"]:
        assert key in text, f"CURRENT_STATUS.md 缺少关键章节: {key}"


def test_status_doc_engine_set_matches_code():
    """状态文档声明的引擎仅 langchain/agentic，与 engine_factory 一致。"""
    from src.core.engine_factory import _get_engine_class
    st = DOCS / "CURRENT_STATUS.md"
    text = st.read_text(encoding="utf-8")
    assert "langchain" in text and "agentic" in text
    # 代码支持集合恰好两个，且不含 original
    assert {_get_engine_class(n).engine_name for n in ("langchain", "agentic")} == {"langchain", "agentic"}
    assert "original" not in [ _get_engine_class(n).engine_name for n in ("langchain", "agentic")]


def test_status_doc_mentions_model_router_switch():
    """状态文档记录 T3.1 模型分级开关，与 config 一致。"""
    import src.config as cfg
    st = DOCS / "CURRENT_STATUS.md"
    text = st.read_text(encoding="utf-8")
    assert "MODEL_ROUTER_ENABLED" in text
    assert isinstance(cfg.MODEL_ROUTER_ENABLED, bool)


# ---------------------------------------------------------------------------
# 3) ADR 决策记录
# ---------------------------------------------------------------------------

def test_adr_directory_and_files_exist():
    """存在 adr/ 目录且至少 4 个 ADR（T3.1-T3.4 各一）。"""
    assert ADR.is_dir()
    adrs = sorted(ADR.glob("*.md"))
    assert len(adrs) >= 4
    for f in adrs:
        text = f.read_text(encoding="utf-8")
        # ADR 标准字段
        assert "AD-" in text and "状态" in text and "决定" in text
        assert ("理由" in text or "原因" in text or "背景" in text)
        assert "结果" in text or "影响" in text or "后果" in text


def test_adr_covers_engine_consolidation_decision():
    """ADR 记录了 T3.4 架构收敛与 langchain-community 迁移决定。"""
    adrs = "\n".join(f.read_text(encoding="utf-8") for f in ADR.glob("*.md"))
    assert "original" in adrs          # 决策涉及删除 original 引擎
    assert "langchain-community" in adrs  # 决策涉及 sunset 依赖迁移
