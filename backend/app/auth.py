"""登录鉴权：bcrypt + JWT"""
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

from .config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from .db import get_conn, hash_password, verify_password, log_operation

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class TokenResp(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


def create_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_user(username: str, password: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return row


def get_user_from_token(raw_token: str) -> str:
    """校验 token 返回 username，供 header 或 query 两种方式复用"""
    try:
        payload = jwt.decode(raw_token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效令牌")
    if not username:
        raise HTTPException(status_code=401, detail="无效令牌")
    return username


def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ")[1]
    return get_user_from_token(token)


@router.post("/login", response_model=TokenResp)
def login(req: LoginReq):
    user = verify_user(req.username, req.password)
    if not user:
        log_operation(req.username, "login_fail", "auth")
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token = create_token(user["username"], user["role"])
    log_operation(user["username"], "login", "auth")
    return TokenResp(access_token=token, username=user["username"], role=user["role"])


@router.get("/me")
def me(user: str = Depends(get_current_user)):
    return {"username": user}


class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password")
def change_password(req: ChangePwdReq, user: str = Depends(get_current_user)):
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (user,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(req.old_password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=400, detail="旧密码错误")
    new_hash = hash_password(req.new_password)
    conn.execute("UPDATE users SET password_hash=? WHERE username=?", (new_hash, user))
    conn.commit()
    conn.close()
    log_operation(user, "change_password", user)
    return {"ok": True, "msg": "密码已修改"}
