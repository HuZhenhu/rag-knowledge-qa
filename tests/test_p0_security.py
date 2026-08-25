"""Phase 0 安全修复测试 — T0.1 CORS 白名单 / T0.2 JWT 密钥启动校验 / T0.3 移除硬编码 admin API Key"""
import sys
from unittest.mock import MagicMock

import pytest

# ─── 在导入项目代码之前 mock 掉重量级依赖，避免 collection 时加载模型 ───
_mock_st = MagicMock()
_mock_st.SentenceTransformer = MagicMock()
sys.modules.setdefault("sentence_transformers", _mock_st)

_DEFAULT_SECRET = "rag-knowledge-qa-dev-secret-key-change-in-production"


def _main_src() -> str:
    """读取 main.py 源码（用于断言关键接线，非行为测试）"""
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent.joinpath("main.py").read_text(encoding="utf-8")


class TestCorsWhitelist:
    """T0.1 CORS 白名单：allow_origins 从 env 读取，默认仅 localhost，禁止 * + credentials"""

    def test_default_origins_are_localhost_only(self):
        from src.config import parse_cors_allow_origins, DEFAULT_CORS_ORIGINS
        origins = parse_cors_allow_origins("")
        assert "*" not in origins
        assert set(origins) == set(DEFAULT_CORS_ORIGINS)
        for o in origins:
            assert o.startswith("http://localhost") or o.startswith("http://127.0.0.1")

    def test_env_origins_parsed_and_trimmed(self):
        from src.config import parse_cors_allow_origins
        origins = parse_cors_allow_origins(" https://a.example.com , https://b.example.com ")
        assert origins == ["https://a.example.com", "https://b.example.com"]

    def test_credentials_disallowed_when_star_present(self):
        from src.config import cors_allow_credentials_allowed
        assert cors_allow_credentials_allowed(["*"]) is False
        assert cors_allow_credentials_allowed(["*", "http://localhost:5173"]) is False

    def test_credentials_allowed_with_explicit_whitelist(self):
        from src.config import cors_allow_credentials_allowed
        assert cors_allow_credentials_allowed(["http://localhost:5173"]) is True

    def test_main_uses_whitelist_and_credentials_derived(self):
        src = _main_src()
        assert 'allow_origins=["*"]' not in src
        assert "allow_origins=CORS_ALLOW_ORIGINS" in src
        assert "allow_credentials=CORS_ALLOW_CREDENTIALS" in src


class TestJwtSecretStartupValidation:
    """T0.2 生产模式（APP_ENV=production）下默认密钥启动报错退出"""

    def test_production_default_secret_raises(self):
        from src.config import validate_security_config
        with pytest.raises(RuntimeError):
            validate_security_config(app_env="production", jwt_secret=_DEFAULT_SECRET)

    def test_production_custom_secret_passes(self):
        from src.config import validate_security_config
        validate_security_config(app_env="production", jwt_secret="a-strong-32byte-secret-value-abcdef123456")

    def test_development_default_secret_passes(self):
        from src.config import validate_security_config
        validate_security_config(app_env="development", jwt_secret=_DEFAULT_SECRET)

    def test_app_env_defaults_to_development(self):
        import src.config as c
        assert c.APP_ENV in ("development", "production", "test", "staging")
        # 默认值必须是 development，保证测试/开发不受影响
        assert c.APP_ENV != "production"

    def test_main_invokes_startup_validation(self):
        src = _main_src()
        assert "validate_security_config()" in src


class TestNoHardcodedAdminKey:
    """T0.3 移除硬编码 admin API Key，改为 LEGACY_API_KEY env 可选注入"""

    def test_default_no_legacy_key_registered(self, monkeypatch):
        monkeypatch.delenv("LEGACY_API_KEY", raising=False)
        monkeypatch.delenv("LEGACY_API_KEY_ROLE", raising=False)
        from src.api import jwt_auth
        from src.api.auth import API_KEYS
        jwt_auth._LEGACY_API_KEYS.clear()
        API_KEYS.clear()
        result = jwt_auth.register_legacy_key_from_env()
        assert result is None
        assert not jwt_auth._LEGACY_API_KEYS
        assert not API_KEYS

    def test_env_key_registered_with_default_role(self, monkeypatch):
        monkeypatch.setenv("LEGACY_API_KEY", "sk-env-test-abcdef")
        monkeypatch.delenv("LEGACY_API_KEY_ROLE", raising=False)
        from src.api import jwt_auth
        from src.api.auth import API_KEYS
        jwt_auth._LEGACY_API_KEYS.clear()
        API_KEYS.clear()
        key = jwt_auth.register_legacy_key_from_env()
        assert key == "sk-env-test-abcdef"
        assert jwt_auth._LEGACY_API_KEYS["sk-env-test-abcdef"]["role"] == "viewer"
        assert API_KEYS["sk-env-test-abcdef"]["role"] == "viewer"

    def test_env_key_role_override(self, monkeypatch):
        monkeypatch.setenv("LEGACY_API_KEY", "sk-env-test-xyz")
        monkeypatch.setenv("LEGACY_API_KEY_ROLE", "admin")
        from src.api import jwt_auth
        from src.api.auth import API_KEYS
        jwt_auth._LEGACY_API_KEYS.clear()
        API_KEYS.clear()
        jwt_auth.register_legacy_key_from_env()
        assert jwt_auth._LEGACY_API_KEYS["sk-env-test-xyz"]["role"] == "admin"

    def test_main_has_no_hardcoded_key(self):
        src = _main_src()
        assert "sk-rag-dev-key-12345" not in src
        assert "register_legacy_key_from_env()" in src
