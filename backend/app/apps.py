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

router = APIRouter(prefix="/api", tags=["apps"])


def _all_apps():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM apps ORDER BY sort, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _running_first(apps):
    """运行中在前，同状态保持原 sort 顺序（稳定排序）"""
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
def list_apps(user: str = Depends(get_current_user)):
    apps = _all_apps()
    # 并发采集状态，避免串行拖慢
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(system.app_status, apps))
    return _running_first(results)


@router.get("/apps/export")
def export_apps(user: str = Depends(get_current_user)):
    """导出全部应用信息为 CSV（含账号密码、部署、资源占用）"""
    apps = _all_apps()
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(system.app_status, apps))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["名称", "分组", "类型", "unit_name", "端口", "部署方式", "部署路径", "web地址",
                "账号", "密码", "状态", "监听", "显存MiB", "内存MiB", "健康", "自启", "备注"])
    for a in rows:
        w.writerow([a["name"], a["group_name"], a["type"], a["unit_name"], a["port"],
                    a["deploy_type"], a["path"], a["web_url"],
                    a["web_user"], a["web_password"],
                    a["status"], "是" if a["listening"] else "否",
                    a["gpu_mib"], a["mem_mb"], a["health"], a["enabled"], a["remark"]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=apps.csv"},
    )


@router.get("/apps/{app_id}/status")
def single_status(app_id: int, user: str = Depends(get_current_user)):
    return system.app_status(_get_app(app_id))


@router.post("/apps/{app_id}/start")
def start_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    ok, out = system.app_action(app, "start")
    log_operation(user, "start", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/stop")
def stop_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    ok, out = system.app_action(app, "stop")
    log_operation(user, "stop", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/restart")
def restart_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    ok, out = system.app_action(app, "restart")
    log_operation(user, "restart", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/enable")
def enable_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    ok, out = system.app_enable(app, True)
    log_operation(user, "enable", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


@router.post("/apps/{app_id}/disable")
def disable_app(app_id: int, user: str = Depends(get_current_user)):
    app = _get_app(app_id)
    ok, out = system.app_enable(app, False)
    log_operation(user, "disable", app["name"], out[-200:] if out else "")
    return {"ok": ok, "detail": out[-300:] if out else ""}


# ---------- 应用增删改（P1，先预留） ----------
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


@router.post("/apps")
def add_app(data: AppIn, user: str = Depends(get_current_user)):
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO apps (name,type,unit_name,port,deploy_type,path,web_url,group_name,icon,remark,sort,web_user,web_password)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (data.name, data.type, data.unit_name, data.port, data.deploy_type,
              data.path, data.web_url, data.group_name, data.icon, data.remark, data.sort,
              data.web_user, data.web_password))
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
                web_url=?, group_name=?, icon=?, remark=?, sort=?, web_user=?, web_password=?
            WHERE id=?
        """, (data.name, data.type, data.unit_name, data.port, data.deploy_type,
              data.path, data.web_url, data.group_name, data.icon, data.remark, data.sort,
              data.web_user, data.web_password, app_id))
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
