"""应用列表、状态、启停、增删改、导出"""
import csv
import io
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth import get_current_user
from .db import get_conn, log_operation
from . import system
from . import system_remote

router = APIRouter(prefix="/api", tags=["apps"])


def _get_host_row(host_id: int | None):
    """根据 host_id 拿主机配置；host_id=None 返回 None（表示本机）"""
    if host_id is None:
        return None
    conn = get_conn()
    row = conn.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"主机 id={host_id} 不存在")
    return dict(row)


def _collect_status(app: dict, host_row):
    """统一入口：host_row=None 走本机，否则走 SSH 远程"""
    if host_row is None:
        return system.app_status(app)
    return system_remote.app_status(host_row, app)


def _all_apps(host_id: int | None = None):
    conn = get_conn()
    if host_id is None:
        rows = conn.execute("SELECT * FROM apps WHERE host_id IS NULL ORDER BY sort, id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM apps WHERE host_id=? ORDER BY sort, id", (host_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _running_first(apps):
    def key(a):
        st = a.get("status", "")
        if st == "running":
            return 0
        if st == "stopped":
            return 1
        return 2
    return sorted(apps, key=key)


def _get_app(app_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="应用不存在")
    return dict(row)


@router.get("/apps")
def list_apps(host_id: int | None = None, user: str = Depends(get_current_user)):
    host_row = _get_host_row(host_id)
    apps = _all_apps(host_id)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda a: _collect_status(a, host_row), apps))
    return _running_first(results)


@router.get("/apps/export")
def export_apps(host_id: int | None = None, user: str = Depends(get_current_user)):
    host_row = _get_host_row(host_id)
    apps = _all_apps(host_id)
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(lambda a: _collect_status(a, host_row), apps))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["名称", "分组", "类型", "unit_name", "端口", "部署方式", "部署路径", "web地址",
                "账号", "密码", "状态", "监听", "显存MiB", "内存MiB", "健康", "自启", "备注"])
    for a in rows:
        w.writerow([a["name"], a["group_name"], a["type"], a["unit_name"], a["port"],
                    a["deploy_type"], a["path"], a["web_url"],
                    a["web_user"], a["web_password"],
                    a["status"], "是" if a["listening"] else "否",
                    a.get("gpu_mib", 0), a["mem_mb"], a["health"], a["enabled"], a["remark"]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=apps.csv"},
    )


@router.get("/apps/{app_id}/status")
def single_status(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    host_row = _get_host_row(app.get("host_id"))
    return _collect_status(app, host_row)


@router.post("/apps/{app_id}/start")
def start_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    if app.get("host_id"):
        raise HTTPException(status_code=400, detail="远程主机的启停暂未开放（安全考虑），请直接登录目标主机操作")
    ok, out = system.app_action(app, "start")
    log_operation(user, "start", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/stop")
def stop_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    if app.get("host_id"):
        raise HTTPException(status_code=400, detail="远程主机的启停暂未开放（安全考虑），请直接登录目标主机操作")
    ok, out = system.app_action(app, "stop")
    log_operation(user, "stop", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/restart")
def restart_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    if app.get("host_id"):
        raise HTTPException(status_code=400, detail="远程主机的启停暂未开放（安全考虑），请直接登录目标主机操作")
    ok, out = system.app_action(app, "restart")
    log_operation(user, "restart", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/enable")
def enable_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    if app.get("host_id"):
        raise HTTPException(status_code=400, detail="远程主机不支持开机自启开关")
    ok, out = system.app_enable(app, True)
    log_operation(user, "enable", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/disable")
def disable_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    if app.get("host_id"):
        raise HTTPException(status_code=400, detail="远程主机不支持开机自启开关")
    ok, out = system.app_enable(app, False)
    log_operation(user, "disable", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


# ---------- 应用增删改 ----------
class AppIn(BaseModel):
    name: str
    type: str = "systemd"
    unit_name: str
    port: int | None = None
    deploy_type: str = "venv"
    path: str = ""
    web_url: str = ""
    group_name: str = "其他"
    icon: str = "📦"
    remark: str = ""
    sort: int = 0
    web_user: str = ""
    web_password: str = ""
    parent_id: int | None = None
    host_id: int | None = None


@router.post("/apps")
def add_app(data: AppIn, user: str = Depends(get_current_user)):
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO apps (name,type,unit_name,port,deploy_type,path,web_url,group_name,icon,remark,sort,web_user,web_password,parent_id,host_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (data.name, data.type, data.unit_name, data.port, data.deploy_type,
              data.path, data.web_url, data.group_name, data.icon, data.remark, data.sort,
              data.web_user, data.web_password, data.parent_id, data.host_id))
        conn.commit()
        new_id = cur.lastrowid
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"添加失败: {e}")
    conn.close()
    log_operation(user, "add_app", data.name)
    return {"ok": True, "id": new_id}


@router.delete("/apps/{app_id}")
def delete_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    conn = get_conn()
    conn.execute("DELETE FROM apps WHERE id=?", (app_id,))
    conn.commit()
    conn.close()
    log_operation(user, "delete_app", app["name"])
    return {"ok": True}


@router.put("/apps/{app_id}")
def update_app(app_id: int, data: AppIn, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE apps SET name=?, type=?, unit_name=?, port=?, deploy_type=?, path=?,
                web_url=?, group_name=?, icon=?, remark=?, sort=?, web_user=?, web_password=?, parent_id=?, host_id=?
            WHERE id=?
        """, (data.name, data.type, data.unit_name, data.port, data.deploy_type,
              data.path, data.web_url, data.group_name, data.icon, data.remark, data.sort,
              data.web_user, data.web_password, data.parent_id, data.host_id, app_id))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"更新失败: {e}")
    conn.close()
    log_operation(user, "update_app", data.name)
    return {"ok": True}


@router.get("/ops")
def list_ops(limit: int = 100, user: str = Depends(get_current_user)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM operation_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
