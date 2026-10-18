"""实时日志：历史 + SSE 跟随"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .auth import get_current_user
from .db import get_conn
from . import system

router = APIRouter(prefix="/api", tags=["logs"])


def _get_app(app_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="应用不存在")
    return dict(row)


@router.get("/apps/{app_id}/logs")
def get_logs(app_id: int, tail: int = 200, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    ok, out = system.log_lines(app, tail=min(tail, 2000))
    if not ok:
        return {"ok": False, "logs": f"(获取日志失败) {out[-300:]}"}
    return {"ok": True, "logs": out}


@router.get("/apps/{app_id}/logs/follow")
def follow_logs(app_id: int, user: str = Depends(get_current_user)):
    """SSE 实时日志推送"""
    app = _get_app(app_id)
    cmd = system.log_follow_cmd(app)

    async def event_stream():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield f"data: {json.dumps({'line': line.decode(errors='replace')}, ensure_ascii=False)}\n\n"
        finally:
            if proc and proc.returncode is None:
                proc.kill()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
