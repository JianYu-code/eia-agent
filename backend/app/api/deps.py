"""管理员口令访问控制 — 内网轻量鉴权：X-Admin-Token 请求头校验"""
import hmac
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

from app.config import BASE_DIR, DATA_DIR

TOKEN_PATH = DATA_DIR / "admin_token.txt"

_token_cache: str | None = None


def get_admin_token() -> str:
    """三级来源：环境变量 ADMIN_TOKEN → admin_token.txt → 自动生成并写盘"""
    global _token_cache
    env = os.getenv("ADMIN_TOKEN", "").strip()
    if env:
        return env
    if _token_cache:
        return _token_cache
    if TOKEN_PATH.exists():
        t = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if t:
            _token_cache = t
            return t
    t = secrets.token_urlsafe(12)
    TOKEN_PATH.write_text(t, encoding="utf-8")
    _token_cache = t
    return t


def set_admin_token(new_token: str) -> str:
    """修改口令：写文件 + 更新缓存（环境变量优先时不生效于请求校验）"""
    global _token_cache
    new_token = new_token.strip()
    if len(new_token) < 6:
        raise ValueError("口令长度至少 6 位")
    TOKEN_PATH.write_text(new_token, encoding="utf-8")
    _token_cache = new_token
    return new_token


def verify_token(candidate: str) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate, get_admin_token())


async def require_admin(x_admin_token: str = Header(default="")):
    """FastAPI 依赖：受保护路由挂载。校验失败一律 401。"""
    if not verify_token(x_admin_token):
        raise HTTPException(status_code=401, detail="无管理权限，请输入管理员口令")
    return True
