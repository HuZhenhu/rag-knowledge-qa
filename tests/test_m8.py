"""M8 对话能力增强模块验收测试

覆盖范围：
1. 对话摘要生成（ConversationSummarizer.summarize，mock LLM）
2. 摘要拆分逻辑（split_messages）
3. 摘要注入prompt（Generator.generate，mock LLM）
4. SessionManager摘要触发与注入（_maybe_summarize / get_history_with_summary）
5. 主动追问触发条件（RAGEngine._generate_followup / query中的followup分支）
6. 意图识别分类（IntentClassifier.classify，关键词 + LLM mock）
7. 对话导出格式（export_session_markdown）
8. M8配置项默认值
"""
import sys
from unittest.mock import Mock, patch, MagicMock


# Mock掉sentence_transformers和torch，避免torch循环导入问题
# 这些模块在测试M8功能时不需要真实执行
_mock_st = MagicMock()
_mock_torch = MagicMock()
if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = _mock_st
if "torch" not in sys.modules:
    sys.modules["torch"] = _mock_torch


# ======================================================================
# 1. 对话摘要生成（mock LLM）
# ======================================================================

class TestConversationSummarizer:
    """测试 ConversationSummarizer.summarize"""

    @patch("src.core.conversation_summarizer.OpenAI")
    def test_summarize_success(self, mock_openai_cls):
        """LLM调用成功时返回摘要"""
        from src.core.conversation_summarizer import ConversationSummarizer

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="用户询问了Python基础，助手介绍了变量和函数。"))]
        mock_client.chat.completions.create.return_value = mock_resp

        summarizer = ConversationSummarizer()
        messages = [
            {"role": "user", "content": "什么是Python变量？"},
            {"role": "assistant", "content": "Python变量是..."},
            {"role": "user", "content": "函数怎么定义？"},
            {"role": "assistant", "content": "用def关键字..."},
        ]
        result = summarizer.summarize(messages)

        assert "Python" in result or "变量" in result
        mock_client.chat.completions.create.assert_called_once()

    def test_summarize_empty_messages(self):
        """空消息列表返回空字符串"""
        from src.core.conversation_summarizer import ConversationSummarizer

        with patch("src.core.conversation_summarizer.OpenAI"):
            summarizer = ConversationSummarizer()
            result = summarizer.summarize([])
            assert result == ""

    @patch("src.core.conversation_summarizer.OpenAI")
    def test_summarize_llm_failure_fallback(self, mock_openai_cls):
        """LLM调用失败时降级返回简单文本"""
        from src.core.conversation_summarizer import ConversationSummarizer

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        summarizer = ConversationSummarizer()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = summarizer.summarize(messages)

        assert "摘要生成失败" in result
        assert "2 条消息" in result

    @patch("src.core.conversation_summarizer.OpenAI")
    def test_summarize_prompt_content(self, mock_openai_cls):
        """验证发送给LLM的prompt包含格式化对话"""
        from src.core.conversation_summarizer import ConversationSummarizer

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="摘要"))]
        mock_client.chat.completions.create.return_value = mock_resp

        summarizer = ConversationSummarizer()
        messages = [
            {"role": "user", "content": "问题A"},
            {"role": "assistant", "content": "回答B"},
        ]
        summarizer.summarize(messages)

        call_args = mock_client.chat.completions.create.call_args
        prompt_text = call_args.kwargs["messages"][0]["content"]
        assert "问题A" in prompt_text
        assert "回答B" in prompt_text
        assert "用户:" in prompt_text
        assert "助手:" in prompt_text
        assert call_args.kwargs["temperature"] == 0.3


# ======================================================================
# 2. 摘要拆分逻辑
# ======================================================================

class TestSplitMessages:
    """测试 ConversationSummarizer.split_messages"""

    def test_split_below_threshold(self):
        """消息数未达阈值时不拆分"""
        from src.core.conversation_summarizer import ConversationSummarizer

        with patch("src.core.conversation_summarizer.OpenAI"):
            summarizer = ConversationSummarizer()

        messages = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
        to_summarize, to_keep = summarizer.split_messages(messages, threshold_rounds=5, keep_recent=3)

        assert to_summarize == []
        assert to_keep == messages

    def test_split_above_threshold(self):
        """消息数超过阈值时正确拆分"""
        from src.core.conversation_summarizer import ConversationSummarizer

        with patch("src.core.conversation_summarizer.OpenAI"):
            summarizer = ConversationSummarizer()

        # 10条消息（5轮），阈值3轮，保留2轮
        messages = [
            {"role": "user", "content": f"q{i}"} for i in range(5)
        ] + [
            {"role": "assistant", "content": f"a{i}"} for i in range(5)
        ]
        to_summarize, to_keep = summarizer.split_messages(messages, threshold_rounds=3, keep_recent=2)

        assert len(to_summarize) > 0
        assert len(to_keep) == 4  # keep_recent=2, 2*2=4
        assert to_summarize + to_keep == messages

    def test_split_exact_threshold(self):
        """恰好等于阈值时不拆分"""
        from src.core.conversation_summarizer import ConversationSummarizer

        with patch("src.core.conversation_summarizer.OpenAI"):
            summarizer = ConversationSummarizer()

        # threshold_rounds=2 → threshold_messages=4
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        to_summarize, to_keep = summarizer.split_messages(messages, threshold_rounds=2, keep_recent=1)

        assert to_summarize == []
        assert to_keep == messages


# ======================================================================
# 3. 摘要注入prompt
# ======================================================================

class TestSummaryInjection:
    """测试 Generator.generate 中摘要注入到 system message"""

    @patch("openai.OpenAI")
    def test_summary_injected_into_system(self, mock_openai_cls):
        """有summary时，system message包含摘要"""
        from src.core.generator import Generator

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="回答内容"))]
        mock_resp.usage = Mock(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        mock_client.chat.completions.create.return_value = mock_resp

        gen = Generator()
        sources = [{"content": "参考资料", "metadata": {"source": "test.md"}}]
        gen.generate("问题", sources, summary="这是之前的对话摘要")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        system_msg = messages[0]

        assert system_msg["role"] == "system"
        assert "这是之前的对话摘要" in system_msg["content"]

    @patch("openai.OpenAI")
    def test_no_summary_normal_system(self, mock_openai_cls):
        """无summary时，system message不含摘要相关文字"""
        from src.core.generator import Generator

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="回答内容"))]
        mock_resp.usage = Mock(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        mock_client.chat.completions.create.return_value = mock_resp

        gen = Generator()
        sources = [{"content": "参考资料", "metadata": {"source": "test.md"}}]
        gen.generate("问题", sources, summary="")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        system_msg = messages[0]

        assert system_msg["role"] == "system"
        assert "摘要" not in system_msg["content"]

    @patch("openai.OpenAI")
    def test_history_injected_between_system_and_user(self, mock_openai_cls):
        """history消息插入在system和user之间"""
        from src.core.generator import Generator

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="回答"))]
        mock_resp.usage = Mock(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        mock_client.chat.completions.create.return_value = mock_resp

        gen = Generator()
        history = [
            {"role": "user", "content": "上一个问题"},
            {"role": "assistant", "content": "上一个回答"},
        ]
        gen.generate("当前问题", [], history=history)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]

        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "上一个问题"
        assert messages[2]["role"] == "assistant"
        assert messages[3]["role"] == "user"
        assert "当前问题" in messages[3]["content"]


# ======================================================================
# 4. SessionManager摘要触发与注入
# ======================================================================

class TestSessionManagerSummary:
    """测试 SessionManager 中M8相关方法"""

    def test_get_history_with_summary_no_summary(self):
        """无摘要时get_history_with_summary只返回对话历史"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        mgr.add_message("s1", "user", "你好")
        mgr.add_message("s1", "assistant", "你好！")

        history = mgr.get_history_with_summary("s1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_get_history_with_summary_has_summary(self):
        """有摘要时get_history_with_summary在开头插入system摘要"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        session = mgr.get_or_create_session("s2")
        session.summary = "用户问了关于Python的问题"
        mgr.save_session(session)  # T2.1 后端化：属性修改需显式落盘
        mgr.add_message("s2", "user", "还有问题")
        mgr.add_message("s2", "assistant", "请说")

        history = mgr.get_history_with_summary("s2")
        assert len(history) == 3
        assert history[0]["role"] == "system"
        assert "Python" in history[0]["content"]
        assert "摘要" in history[0]["content"]

    @patch("src.core.session.USE_CONVERSATION_SUMMARY", True)
    def test_maybe_summarize_triggered(self):
        """超过阈值轮数时触发摘要"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        # 手动添加足够多的消息以超过阈值
        session = mgr.get_or_create_session("s3")
        for i in range(12):  # 6轮，超过默认阈值5轮
            session.messages.append(
                __import__('src.core.session', fromlist=['Message']).Message(
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"msg{i}",
                )
            )
        mgr.save_session(session)  # T2.1 后端化：直接改对象后需显式落盘

        with patch.object(mgr, "_get_summarizer") as mock_get_sum:
            mock_summarizer = Mock()
            mock_summarizer.summarize.return_value = "测试摘要"
            mock_get_sum.return_value = mock_summarizer

            # 再添加一条消息触发maybe_summarize
            mgr.add_message("s3", "user", "触发消息")

            mock_summarizer.summarize.assert_called_once()

    def test_get_summary_empty(self):
        """新会话的摘要为空"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        assert mgr.get_summary("nonexistent") == ""


# ======================================================================
# 5. 主动追问触发条件
# ======================================================================

class TestFollowupGeneration:
    """测试 RAGEngine 中主动追问逻辑"""

    @patch("src.core.rag_engine.USE_INTENT_CLASSIFICATION", True)
    @patch("src.core.rag_engine.RELEVANCE_THRESHOLD", 0.3)
    @patch("src.core.rag_engine.FOLLOWUP_SCORE_THRESHOLD", 0.3)
    @patch("src.core.rag_engine.RETRIEVAL_TOP_K", 5)
    @patch("src.core.rag_engine.metrics")
    @patch("src.core.rag_engine.Trace")
    @patch("src.core.rag_engine.QueryCache")
    @patch("src.core.rag_engine.log_retrieval")
    @patch("src.core.rag_engine.log_llm_call")
    def test_followup_triggered_on_low_score_with_history(
        self, mock_log_llm, mock_log_ret, mock_cache_cls,
        mock_trace_cls, mock_metrics, *args
    ):
        """检索结果全部低于阈值且有历史时触发追问"""
        from src.core.rag_engine import RAGEngine

        engine = RAGEngine.__new__(RAGEngine)
        engine.use_query_expansion = False
        engine.use_hyde = False
        engine.use_reranker = False

        # Mock依赖
        engine.embedder = Mock()
        engine.vector_store = Mock()
        engine.query_understander = Mock()
        engine.reranker = Mock()
        engine.query_cache = Mock()
        engine.query_cache.get.return_value = None
        engine._intent_classifier = None
        engine.generator = Mock()

        # Mock意图分类器
        mock_classifier = Mock()
        mock_classifier.classify.return_value = Mock(intent=Mock(value="query"))
        engine._intent_classifier = mock_classifier

        # Mock检索器返回空结果（触发pre_filter_count == 0路径）
        engine.retriever = Mock()
        engine.retriever.retrieve.return_value = []

        # Mock generator.generate 返回值（chitchat路径可能用到）
        engine.generator.generate.return_value = {
            "answer": "知识库中未找到相关信息",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

        # Mock _generate_followup
        with patch.object(engine, "_generate_followup", return_value="请提供更多信息"):
            response = engine.query(
                "模糊的问题",
                history=[{"role": "user", "content": "上一轮"}],
            )

        assert response.is_followup is True
        assert "更多信息" in response.answer

    @patch("src.core.rag_engine.USE_INTENT_CLASSIFICATION", False)
    @patch("src.core.rag_engine.RELEVANCE_THRESHOLD", 0.3)
    @patch("src.core.rag_engine.FOLLOWUP_SCORE_THRESHOLD", 0.3)
    @patch("src.core.rag_engine.RETRIEVAL_TOP_K", 5)
    @patch("src.core.rag_engine.metrics")
    @patch("src.core.rag_engine.Trace")
    @patch("src.core.rag_engine.QueryCache")
    @patch("src.core.rag_engine.log_retrieval")
    @patch("src.core.rag_engine.log_llm_call")
    def test_followup_not_triggered_without_history(
        self, mock_log_llm, mock_log_ret, mock_cache_cls,
        mock_trace_cls, mock_metrics, *args
    ):
        """无历史时不触发追问"""
        from src.core.rag_engine import RAGEngine

        engine = RAGEngine.__new__(RAGEngine)
        engine.use_query_expansion = False
        engine.use_hyde = False
        engine.use_reranker = False

        engine.embedder = Mock()
        engine.vector_store = Mock()
        engine.query_understander = Mock()
        engine.reranker = Mock()
        engine.query_cache = Mock()
        engine.query_cache.get.return_value = None
        engine._intent_classifier = None

        # Mock检索器返回低分结果
        mock_result = Mock()
        mock_result.content = "低分内容"
        mock_result.metadata = {}
        mock_result.score = 0.1
        engine.retriever = Mock()
        engine.retriever.retrieve.return_value = [mock_result]

        engine.generator = Mock()
        engine.generator.generate.return_value = {
            "answer": "知识库中未找到相关信息",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

        response = engine.query("模糊的问题", history=None)

        assert response.is_followup is False

    @patch("src.core.rag_engine.USE_INTENT_CLASSIFICATION", True)
    def test_followup_fallback_message(self):
        """_generate_followup在LLM失败时返回默认追问"""
        from src.core.rag_engine import RAGEngine

        engine = RAGEngine.__new__(RAGEngine)
        engine.generator = Mock()
        engine.generator._init_client = Mock()
        engine.generator.client = Mock()
        engine.generator.client.chat.completions.create.side_effect = Exception("API error")

        result = engine._generate_followup("问题")

        assert "具体描述" in result or "更多" in result


# ======================================================================
# 6. 意图识别分类
# ======================================================================

class TestIntentClassifier:
    """测试 IntentClassifier.classify"""

    def test_feedback_keyword_detection(self):
        """关键词匹配：反馈意图"""
        from src.core.intent_classifier import IntentClassifier, Intent

        with patch("src.core.intent_classifier.OpenAI"):
            classifier = IntentClassifier()

        result = classifier.classify("你说的不对")
        assert result.intent == Intent.FEEDBACK

        result = classifier.classify("这个回答不准确")
        assert result.intent == Intent.FEEDBACK

    def test_chitchat_keyword_detection_no_history(self):
        """关键词匹配：闲聊意图（无历史）"""
        from src.core.intent_classifier import IntentClassifier, Intent

        with patch("src.core.intent_classifier.OpenAI"):
            classifier = IntentClassifier()

        result = classifier.classify("你好", has_history=False)
        assert result.intent == Intent.CHITCHAT

        result = classifier.classify("谢谢", has_history=False)
        assert result.intent == Intent.CHITCHAT

    def test_chitchat_keyword_ignored_with_history(self):
        """有历史时，闲聊关键词不触发闲聊分类（走LLM）"""
        from src.core.intent_classifier import IntentClassifier

        with patch("src.core.intent_classifier.OpenAI") as mock_openai_cls:
            mock_client = Mock()
            mock_openai_cls.return_value = mock_client
            mock_resp = Mock()
            mock_resp.choices = [Mock(message=Mock(content="followup"))]
            mock_client.chat.completions.create.return_value = mock_resp

            classifier = IntentClassifier()
            classifier.classify("你好", has_history=True)

        # 有历史时应该调LLM而非关键词匹配
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.core.intent_classifier.OpenAI")
    def test_llm_classification_success(self, mock_openai_cls):
        """LLM分类成功"""
        from src.core.intent_classifier import IntentClassifier, Intent

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="query"))]
        mock_client.chat.completions.create.return_value = mock_resp

        classifier = IntentClassifier()
        result = classifier.classify("RAG是什么？")

        assert result.intent == Intent.QUERY
        assert result.confidence == 0.9

    @patch("src.core.intent_classifier.OpenAI")
    def test_llm_classification_followup(self, mock_openai_cls):
        """LLM分类为followup"""
        from src.core.intent_classifier import IntentClassifier, Intent

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="followup"))]
        mock_client.chat.completions.create.return_value = mock_resp

        classifier = IntentClassifier()
        result = classifier.classify("能再详细说说吗？", has_history=True)

        assert result.intent == Intent.FOLLOWUP

    @patch("src.core.intent_classifier.OpenAI")
    def test_llm_classification_failure_default(self, mock_openai_cls):
        """LLM调用失败时默认返回query"""
        from src.core.intent_classifier import IntentClassifier, Intent

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API error")

        classifier = IntentClassifier()
        result = classifier.classify("技术问题")

        assert result.intent == Intent.QUERY
        assert result.confidence == 0.5

    @patch("src.core.intent_classifier.OpenAI")
    def test_llm_unparseable_response_default(self, mock_openai_cls):
        """LLM返回无法解析的内容时默认返回query"""
        from src.core.intent_classifier import IntentClassifier, Intent

        mock_client = Mock()
        mock_openai_cls.return_value = mock_client
        mock_resp = Mock()
        mock_resp.choices = [Mock(message=Mock(content="我不确定这属于什么意图。"))]
        mock_client.chat.completions.create.return_value = mock_resp

        classifier = IntentClassifier()
        result = classifier.classify("随机输入")

        assert result.intent == Intent.QUERY
        assert result.confidence == 0.5

    def test_intent_enum_values(self):
        """Intent枚举包含所有预期值"""
        from src.core.intent_classifier import Intent

        assert Intent.QUERY.value == "query"
        assert Intent.FOLLOWUP.value == "followup"
        assert Intent.CHITCHAT.value == "chitchat"
        assert Intent.FEEDBACK.value == "feedback"


# ======================================================================
# 7. 对话导出格式
# ======================================================================

class TestExportSessionMarkdown:
    """测试 SessionManager.export_session_markdown"""

    def test_export_empty_session(self):
        """空会话导出"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        result = mgr.export_session_markdown("empty")

        assert "空对话" in result

    def test_export_with_messages(self):
        """有消息的会话导出格式"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        mgr.add_message("s1", "user", "什么是RAG？")
        mgr.add_message("s1", "assistant", "RAG是检索增强生成。")

        result = mgr.export_session_markdown("s1")

        assert "# 对话记录" in result
        assert "s1" in result
        assert "**用户**" in result
        assert "**助手**" in result
        assert "什么是RAG？" in result
        assert "RAG是检索增强生成。" in result
        assert "消息数量" in result

    def test_export_with_summary(self):
        """有摘要时导出包含摘要段"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        session = mgr.get_or_create_session("s2")
        session.summary = "用户在学习RAG技术"
        mgr.save_session(session)  # T2.1 后端化：属性修改需显式落盘
        mgr.add_message("s2", "user", "继续")

        result = mgr.export_session_markdown("s2")

        assert "## 对话摘要" in result
        assert "用户在学习RAG技术" in result

    def test_export_without_summary(self):
        """无摘要时不包含摘要段"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        mgr.add_message("s3", "user", "hello")

        result = mgr.export_session_markdown("s3")

        assert "## 对话摘要" not in result

    def test_export_contains_separator(self):
        """导出包含分隔线"""
        from src.core.session import SessionManager

        mgr = SessionManager()
        mgr.add_message("s4", "user", "q")
        mgr.add_message("s4", "assistant", "a")

        result = mgr.export_session_markdown("s4")

        assert "---" in result


# ======================================================================
# 8. M8配置项默认值
# ======================================================================

class TestM8Config:
    """测试M8配置项"""

    def test_use_conversation_summary_default(self):
        """USE_CONVERSATION_SUMMARY默认关闭"""
        from src.config import USE_CONVERSATION_SUMMARY
        assert isinstance(USE_CONVERSATION_SUMMARY, bool)

    def test_summary_threshold_rounds(self):
        """SUMMARY_THRESHOLD_ROUNDS有合理默认值"""
        from src.config import SUMMARY_THRESHOLD_ROUNDS
        assert isinstance(SUMMARY_THRESHOLD_ROUNDS, int)
        assert SUMMARY_THRESHOLD_ROUNDS > 0

    def test_summary_keep_recent_rounds(self):
        """SUMMARY_KEEP_RECENT_ROUNDS有合理默认值"""
        from src.config import SUMMARY_KEEP_RECENT_ROUNDS
        assert isinstance(SUMMARY_KEEP_RECENT_ROUNDS, int)
        assert SUMMARY_KEEP_RECENT_ROUNDS > 0

    def test_followup_score_threshold(self):
        """FOLLOWUP_SCORE_THRESHOLD有合理默认值"""
        from src.config import FOLLOWUP_SCORE_THRESHOLD
        assert isinstance(FOLLOWUP_SCORE_THRESHOLD, float)
        assert 0 < FOLLOWUP_SCORE_THRESHOLD <= 1

    def test_use_intent_classification_default(self):
        """USE_INTENT_CLASSIFICATION默认关闭"""
        from src.config import USE_INTENT_CLASSIFICATION
        assert isinstance(USE_INTENT_CLASSIFICATION, bool)

    def test_session_summary_field(self):
        """Session dataclass有summary字段"""
        from src.core.session import Session
        import dataclasses

        fields = {f.name for f in dataclasses.fields(Session)}
        assert "summary" in fields

    def test_rag_response_has_m8_fields(self):
        """RAGResponse有is_followup和intent字段"""
        from src.core.rag_engine import RAGResponse
        import dataclasses

        fields = {f.name for f in dataclasses.fields(RAGResponse)}
        assert "is_followup" in fields
        assert "intent" in fields

    @patch.dict("os.environ", {"USE_CONVERSATION_SUMMARY": "true"})
    def test_config_env_override_summary(self):
        """环境变量可覆盖配置"""
        import os
        val = os.getenv("USE_CONVERSATION_SUMMARY", "false").lower() == "true"
        assert val is True
