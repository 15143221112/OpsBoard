"""应用部署文档：上传到 minio（桶 document），元数据存 SQLite"""
import os
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth import get_current_user
from .db import get_conn, log_operation

router = APIRouter(prefix="/api", tags=["docs"])

# MinIO 凭据必须通过环境变量注入（生产必填；未配置时文档功能不可用）
MINIO_ENDPOINT = os.environ.get("APP_PANEL_MINIO_ENDPOINT", "")
MINIO_ACCESS_KEY = os.environ.get("APP_PANEL_MINIO_ACCESS", "")
MINIO_SECRET_KEY = os.environ.get("APP_PANEL_MINIO_SECRET", "")
MINIO_BUCKET = os.environ.get("APP_PANEL_MINIO_BUCKET", "document")
MINIO_PUBLIC_BASE = os.environ.get("APP_PANEL_MINIO_PUBLIC", "")

_client = None
_bucket_checked = False


def _get_client():
    global _client, _bucket_checked
    if not MINIO_ENDPOINT or not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        raise HTTPException(status_code=503, detail="MinIO 未配置：请设置 APP_PANEL_MINIO_* 环境变量")
    if _client is None:
        from minio import Minio
        _client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                        secret_key=MINIO_SECRET_KEY, secure=False)
    if not _bucket_checked:
        try:
            if not _client.bucket_exists(MINIO_BUCKET):
                _client.make_bucket(MINIO_BUCKET)
        except Exception:
            pass
        _bucket_checked = True
    return _client


def _docs_of(app_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, app_id, filename, key, url, size, created_at FROM app_docs WHERE app_id=? ORDER BY id",
        (app_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/apps/{app_id}/docs")
def list_docs(app_id: int, user: str = Depends(get_current_user)):
    return _docs_of(app_id)


@router.get("/apps/{app_id}/docs/{doc_id}/download")
def download_doc(app_id: int, doc_id: int, user: str = Depends(get_current_user)):
    """通过面板代理下载文档，避免跳转到 MinIO 登录页"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM app_docs WHERE id=? AND app_id=?", (doc_id, app_id)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        c = _get_client()
        obj = c.get_object(MINIO_BUCKET, row["key"])
        import urllib.parse
        fname = urllib.parse.quote(row["filename"])
        return StreamingResponse(
            obj.stream(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename*=UTF-8\'\'{fname}'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取失败: {e}")


@router.post("/apps/{app_id}/docs")
async def upload_doc(app_id: int, file: UploadFile = File(...),
                     user: str = Depends(get_current_user)):
    try:
        c = _get_client()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"minio 连接失败: {e}")
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    # 安全文件名：只保留基础名，避免路径穿越
    import re
    fname = os.path.basename(file.filename).replace(" ", "_")
    key = f"app-{app_id}/{int(time.time())}_{fname}"
    data = await file.read()
    import io
    try:
        c.put_object(MINIO_BUCKET, key, io.BytesIO(data), length=len(data),
                     content_type=file.content_type or "application/octet-stream")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")
    url = f"{MINIO_PUBLIC_BASE}/{MINIO_BUCKET}/{key}"
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO app_docs (app_id, filename, key, url, size) VALUES (?,?,?,?,?)",
        (app_id, fname, key, url, len(data)),
    )
    conn.commit()
    doc_id = cur.lastrowid
    conn.close()
    log_operation(user, "upload_doc", f"app-{app_id}", fname)
    return {"ok": True, "id": doc_id, "url": url, "filename": fname}


@router.delete("/apps/{app_id}/docs/{doc_id}")
def delete_doc(app_id: int, doc_id: int, user: str = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM app_docs WHERE id=? AND app_id=?", (doc_id, app_id)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        c = _get_client()
        c.remove_object(MINIO_BUCKET, row["key"])
    except Exception:
        pass
    conn.execute("DELETE FROM app_docs WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()
    log_operation(user, "delete_doc", f"app-{app_id}", row["filename"])
    return {"ok": True}
