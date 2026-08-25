"""JWT认证 — 支持 JWT Token 和旧 API Key 向后兼容"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_REFRESH_TOKEN_EXPIRE_DAYS,
)
from src.storage.database import (
    get_user_by_id,
    create_audit_log,
)

# Bearer token 解析
bearer_scheme = HTTPBearer(auto_error=False)


# ---- 密码工具 ----

def hash_password(password: str) -> str:
    """生成密码哈希"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---- JWT 工具 ----

def create_access_token(user_id: str, role: str) -> str:
    """创建访问 Token"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """创建刷新 Token"""
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并验证 Token，失败抛 HTTPException"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的Token")


# ---- 旧 API Key 向后兼容 ----

# 旧的内存 API Key 存储（由 main.py 初始化）
_LEGACY_API_KEYS: dict[str, dict] = {}


def register_legacy_key_from_env(key_env: str = "LEGACY_API_KEY",
                                 role_env: str = "LEGACY_API_KEY_ROLE") -> str | None:
    """T0.3 从环境变量可选注入旧体系 API Key。

    未配置 LEGACY_API_KEY 时不注册任何默认 key（杜绝默认 admin 后门）；
    配置后同时写入 jwt_auth 旧 Key 存储与 src.api.auth 内存存储（保持原双写语义）。
    返回注入的 key；未配置时返回 None。
    """
    key = os.getenv(key_env, "").strip()
    if not key:
        return None
    role = os.getenv(role_env, "viewer").strip() or "viewer"
    from src.api.auth import API_KEYS
    info = {
        "key": key,
        "role": role,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    _LEGACY_API_KEYS[key] = info
    API_KEYS[key] = info
    return key


def register_legacy_api_key(key: str, info: dict) -> None:
    """注册旧 API Key（main.py 启动时调用）"""
    _LEGACY_API_KEYS[key] = info


def _try_legacy_api_key(credentials: HTTPAuthorizationCredentials) -> dict | None:
    """尝试用旧 API Key 认证，成功返回 {"id","username","role"} 格式"""
    key = credentials.credentials
    if key not in _LEGACY_API_KEYS:
        return None
    info = _LEGACY_API_KEYS[key]
    if not info.get("active", False):
        return None
    return {
        "id": f"apikey:{key[:12]}",
        "username": f"apikey-{key[:8]}",
        "role": info.get("role", "viewer"),
    }


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


# ---- 统一认证依赖 ----

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    request: Request = None,
) -> dict:
    """
    统一认证：优先尝试 JWT Token，向后兼容旧 API Key。
    返回统一格式: {"id": str, "username": str, "role": str}
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="缺少认证凭据")

    token = credentials.credentials

    # 1) 尝试 JWT Token
    if token.count(".") == 2:
        try:
            payload = decode_token(token)
            user_id = payload.get("sub", "")
            user = get_user_by_id(user_id)
            if user is None:
                raise HTTPException(status_code=401, detail="用户不存在")
            if not user.get("is_active", True):
                raise HTTPException(status_code=401, detail="用户已被禁用")
            return {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
            }
        except HTTPException:
            raise
        except Exception:
            pass  # 不是JWT，继续尝试旧API Key

    # 2) 回退到旧 API Key
    legacy = _try_legacy_api_key(credentials)
    if legacy is not None:
        return legacy

    raise HTTPException(status_code=401, detail="无效的认证凭据")


# ---- 角色检查 ----

def require_role(required_role: str):
    """角色检查依赖工厂"""
    role_hierarchy = {"viewer": 0, "editor": 1, "writer": 1, "admin": 2}

    async def checker(user: dict = Depends(get_current_user)) -> dict:
        user_level = role_hierarchy.get(user["role"], -1)
        required_level = role_hierarchy.get(required_role, 2)
        if user_level < required_level:
            raise HTTPException(status_code=403, detail=f"需要 {required_role} 权限")
        return user
    return checker


# ---- 审计日志工具 ----

def log_audit(user_id: str, action: str, resource_type: str = "",
              resource_id: str = "", details: str = "",
              ip_address: str = "") -> None:
    """记录审计日志（同步，轻量级）"""
    try:
        create_audit_log(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
        )
    except Exception:
        pass  # 审计日志写入失败不应影响主流程
