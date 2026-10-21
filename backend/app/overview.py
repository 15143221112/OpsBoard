"""总览：GPU + 内存 + 服务统计。支持 host_id 查询远程主机"""
from fastapi import APIRouter, Depends

from .auth import get_current_user
from . import system
from . import system_remote
from .db import get_conn

router = APIRouter(prefix="/api", tags=["overview"])


def _get_host_row(host_id: int | None):
    if host_id is None:
        return None
    conn = get_conn()
    row = conn.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@router.get("/overview")
def overview(host_id: int | None = None, user: str = Depends(get_current_user)):
    host_row = _get_host_row(host_id)
    if host_row is None:
        # 本机
        return {
            "gpu": system.gpu_total(),
            "memory": system.memory_total(),
            "cpu": system.cpu_total(),
            "disk": system.disk_total(),
            "server": {"hostname": _local_hostname(), "uptime": system._run("uptime -p")[1]},
            "is_remote": False,
        }
    else:
        # 远程 SSH
        return {
            "gpu": system_remote.gpu_total(host_row),
            "memory": system_remote.memory_total(host_row),
            "cpu": system_remote.cpu_total(host_row),
            "disk": system_remote.disk_total(host_row),
            "server": {
                "hostname": host_row["name"] + " (" + host_row["ip"] + ")",
                "uptime": system_remote.uptime(host_row),
            },
            "is_remote": True,
        }


def _local_hostname():
    ok, out = system._run("hostname")
    return out if ok else "unknown"
