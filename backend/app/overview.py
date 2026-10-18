"""总览：GPU + 内存 + 服务统计"""
from fastapi import APIRouter, Depends

from .auth import get_current_user
from . import system

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview")
def overview(user: str = Depends(get_current_user)):
    gpu = system.gpu_total()
    mem = system.memory_total()
    cpu = system.cpu_total()
    # 统计服务数
    return {
        "gpu": gpu,
        "memory": mem,
        "cpu": cpu,
        "server": {
            "hostname": _hostname(),
            "uptime": _uptime(),
        },
    }


def _hostname():
    ok, out = system._run("hostname")
    return out if ok else "unknown"


def _uptime():
    ok, out = system._run("uptime -p")
    return out if ok else ""
