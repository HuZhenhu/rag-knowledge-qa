"""Phase 0 T0.4 — refresh token 轮换 + SQLite 吊销表 + 登出吊销"""
import sys
from unittest.mock import MagicMock

import pytest

# ─── mock 重量级依赖 ───
_mock_st = MagicMock()
_mock_st.SentenceTransformer = MagicMock()
sys.modules.setdefault("sentence_transformers", _mock_st)

from fastapi import HTTPException  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """创建临时数据库并 patch DB_PATH"""
    db_file = tmp_path / "test_p0_4.db"
    monkeypatch.setattr("src.storage.database.DB_PATH", db_file)
    from src.storage.database import init_db
    init_db()
    return db_file


class TestRevokedTokensTable:
    """T0.4a 吊销表 CRUD"""

    def test_not_revoked_by_default(self, tmp_db):
        from src.storage.database import is_token_revoked
        assert is_token_revoked("jti-1") is False

    def test_revoke_and_check(self, tmp_db):
        from src.storage.database import revoke_token, is_token_revoked
        revoke_token("jti-1", token_type="refresh", user_id="u1")
        assert is_token_revoked("jti-1") is True

    def test_revoke_is_idempotent(self, tmp_db):
        from src.storage.database import revoke_token, is_token_revoked
        revoke_token("jti-2")
        revoke_token("jti-2")
        assert is_token_revoked("jti-2") is True


class TestRefreshRevocation:
    """T0.4b refresh token 轮换与吊销"""

    def _create_user(self, tmp_db):
        from src.storage.database import create_user
        from src.api.jwt_auth import hash_password
        return create_user("u001", "tester", hash_password("Test1234"), role="viewer")

    def test_refresh_token_has_jti(self, tmp_db):
        from src.api.jwt_auth import create_refresh_token, decode_token
        token = create_refresh_token("u001")
        payload = decode_token(token)
        assert payload["type"] == "refresh"
        assert payload.get("jti")

    def test_valid_refresh_passes_validation(self, tmp_db):
        from src.api.jwt_auth import create_refresh_token, validate_refresh_token_for_use
        token = create_refresh_token("u001")
        payload = validate_refresh_token_for_use(token)
        assert payload["sub"] == "u001"

    def test_revoked_refresh_fails_validation(self, tmp_db):
        from src.api.jwt_auth import (
            create_refresh_token, revoke_refresh_token, validate_refresh_token_for_use,
        )
        token = create_refresh_token("u001")
        revoke_refresh_token(token)
        with pytest.raises(HTTPException) as ei:
            validate_refresh_token_for_use(token)
        assert ei.value.status_code == 401
        assert "吊销" in ei.value.detail

    def test_rotation_old_invalid_new_valid(self, tmp_db):
        """轮换语义：刷新后旧 token 立即失效，新 token 可用"""
        from src.api.jwt_auth import (
            create_refresh_token, create_access_token,
            validate_refresh_token_for_use, revoke_refresh_token,
        )
        old = create_refresh_token("u001")
        # 模拟 refresh 端点轮换：吊销旧 refresh
        revoke_refresh_token(old)
        # 旧 token 再刷新 → 401
        with pytest.raises(HTTPException) as ei:
            validate_refresh_token_for_use(old)
        assert ei.value.status_code == 401
        # 新 refresh 可用
        new = create_refresh_token("u001")
        validate_refresh_token_for_use(new)
        # access token 照常签发
        assert create_access_token("u001", "viewer")

    def test_logout_revokes_refresh(self, tmp_db):
        """登出吊销：吊销后 token 不可用"""
        from src.api.jwt_auth import (
            create_refresh_token, revoke_refresh_token, validate_refresh_token_for_use,
        )
        token = create_refresh_token("u001")
        revoke_refresh_token(token)
        with pytest.raises(HTTPException):
            validate_refresh_token_for_use(token)

    def test_validate_rejects_access_token(self, tmp_db):
        from src.api.jwt_auth import create_access_token, validate_refresh_token_for_use
        access = create_access_token("u001", "viewer")
        with pytest.raises(HTTPException) as ei:
            validate_refresh_token_for_use(access)
        assert ei.value.status_code == 401
