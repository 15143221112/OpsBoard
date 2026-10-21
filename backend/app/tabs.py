"""Tabs（顶栏 tab）和 Hosts（远程主机）的 CRUD + SSH 连通性测试"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import get_current_user
from .db import get_conn, log_operation
from . import system_remote

router = APIRouter(prefix="/api", tags=["tabs/hosts"])


# ---------- Hosts ----------
class HostIn(BaseModel):
    name: str
    ip: str
    ssh_port: int = 22
    ssh_user: str = "ubuntu"
    ssh_password: str = ""
    remark: str = ""
    sort: int = 0
    enabled: int = 1


def _row_host(row) -> dict:
    d = dict(row)
    d.pop("ssh_password", None)  # 密码不返回前端
    return d


@router.get("/hosts")
def list_hosts(user: str = Depends(get_current_user)):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM hosts ORDER BY sort, id").fetchall()
    conn.close()
    return [_row_host(r) for r in rows]


@router.post("/hosts")
def add_host(data: HostIn, user: str = Depends(get_current_user)):
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO hosts (name,ip,ssh_port,ssh_user,ssh_password,remark,sort,enabled)
            VALUES (?,?,?,?,?,?,?,?)
        """, (data.name, data.ip, data.ssh_port, data.ssh_user, data.ssh_password,
              data.remark, data.sort, data.enabled))
        conn.commit()
        new_id = cur.lastrowid
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"添加主机失败: {e}")
    conn.close()
    log_operation(user, "add_host", data.name)
    return {"ok": True, "id": new_id}


@router.put("/hosts/{host_id}")
def update_host(host_id: int, data: HostIn, user: str = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="主机不存在")
    try:
        conn.execute("""
            UPDATE hosts SET name=?,ip=?,ssh_port=?,ssh_user=?,ssh_password=?,remark=?,sort=?,enabled=?
            WHERE id=?
        """, (data.name, data.ip, data.ssh_port, data.ssh_user, data.ssh_password,
              data.remark, data.sort, data.enabled, host_id))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"更新失败: {e}")
    conn.close()
    # 清掉连接缓存，让下次请求重连
    system_remote._conn_cache.pop(host_id, None)
    log_operation(user, "update_host", data.name)
    return {"ok": True}


@router.delete("/hosts/{host_id}")
def delete_host(host_id: int, user: str = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="主机不存在")
    # 检查是否有 apps/tabs 引用
    ref_apps = conn.execute("SELECT COUNT(*) FROM apps WHERE host_id=?", (host_id,)).fetchone()[0]
    ref_tabs = conn.execute("SELECT COUNT(*) FROM tabs WHERE host_id=?", (host_id,)).fetchone()[0]
    if ref_apps or ref_tabs:
        conn.close()
        raise HTTPException(status_code=400,
                            detail=f"该主机下还有 {ref_apps} 个应用、{ref_tabs} 个 tab，请先迁移或删除")
    conn.execute("DELETE FROM hosts WHERE id=?", (host_id,))
    conn.commit()
    conn.close()
    system_remote._conn_cache.pop(host_id, None)
    log_operation(user, "delete_host", row["name"])
    return {"ok": True}


@router.post("/hosts/{host_id}/test")
def test_host(host_id: int, user: str = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="主机不存在")
    hr = dict(row)
    system_remote._conn_cache.pop(host_id, None)
    hn = system_remote.hostname(hr)
    cpu = system_remote.cpu_total(hr)
    mem = system_remote.memory_total(hr)
    disk = system_remote.disk_total(hr)
    gpu = system_remote.gpu_total(hr)
    return {
        "ok": hn != "unknown",
        "hostname": hn,
        "cpu": cpu, "memory": mem, "disk": disk, "gpu": gpu,
    }


# ---------- Tabs ----------
class TabIn(BaseModel):
    name: str
    icon: str = "🖥️"
    host_id: int | None = None
    sort: int = 0
    remark: str = ""
    enabled: int = 1


@router.get("/tabs")
def list_tabs(user: str = Depends(get_current_user)):
    conn = get_conn()
    rows = conn.execute("""
        SELECT t.*, h.name AS host_name, h.ip AS host_ip
        FROM tabs t LEFT JOIN hosts h ON t.host_id = h.id
        WHERE t.enabled=1
        ORDER BY t.sort, t.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/tabs")
def add_tab(data: TabIn, user: str = Depends(get_current_user)):
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO tabs (name,icon,host_id,sort,remark,enabled)
            VALUES (?,?,?,?,?,?)
        """, (data.name, data.icon, data.host_id, data.sort, data.remark, data.enabled))
        conn.commit()
        new_id = cur.lastrowid
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"新建 tab 失败: {e}")
    conn.close()
    log_operation(user, "add_tab", data.name)
    return {"ok": True, "id": new_id}


@router.put("/tabs/{tab_id}")
def update_tab(tab_id: int, data: TabIn, user: str = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM tabs WHERE id=?", (tab_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="tab 不存在")
    try:
        conn.execute("""
            UPDATE tabs SET name=?,icon=?,host_id=?,sort=?,remark=?,enabled=? WHERE id=?
        """, (data.name, data.icon, data.host_id, data.sort, data.remark, data.enabled, tab_id))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"更新失败: {e}")
    conn.close()
    log_operation(user, "update_tab", data.name)
    return {"ok": True}


@router.delete("/tabs/{tab_id}")
def delete_tab(tab_id: int, user: str = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM tabs WHERE id=?", (tab_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="tab 不存在")
    conn.execute("DELETE FROM tabs WHERE id=?", (tab_id,))
    conn.commit()
    conn.close()
    log_operation(user, "delete_tab", row["name"])
    return {"ok": True}
