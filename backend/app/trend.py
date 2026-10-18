"""GPU/内存历史采样与趋势接口"""
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from .auth import get_current_user
from .db import get_conn
from . import system

router = APIRouter(prefix="/api", tags=["trend"])

SAMPLE_INTERVAL = 30  # 每 30s 采一次
MAX_KEEP_DAYS = 7      # 保留 7 天

# trend 表结构（在 db.init_db 中创建）
# CREATE TABLE IF NOT EXISTS trend (
#   id INTEGER PRIMARY KEY AUTOINCREMENT,
#   ts INTEGER,           -- unix 秒
#   gpu_used_mib INTEGER,
#   gpu_free_mib INTEGER,
#   gpu_util INTEGER,
#   mem_used_mb INTEGER,
#   mem_total_mb INTEGER
# )


def _sample_once():
    gpu = system.gpu_total()
    mem = system.memory_total()
    conn = get_conn()
    conn.execute(
        "INSERT INTO trend (ts, gpu_used_mib, gpu_free_mib, gpu_util, mem_used_mb, mem_total_mb) VALUES (?,?,?,?,?,?)",
        (int(time.time()), gpu["used"], gpu["free"], gpu["util"], mem["used"], mem["total"]),
    )
    # 清理过期数据
    cutoff = int(time.time()) - MAX_KEEP_DAYS * 86400
    conn.execute("DELETE FROM trend WHERE ts < ?", (cutoff,))
    conn.commit()
    conn.close()


def _sampler_loop():
    while True:
        try:
            _sample_once()
        except Exception:
            pass
        time.sleep(SAMPLE_INTERVAL)


def start_sampler():
    """后台启动采样线程（幂等）"""
    t = threading.Thread(target=_sampler_loop, daemon=True)
    t.start()


@router.get("/trend")
def trend(hours: int = 6, user: str = Depends(get_current_user)):
    """返回最近 hours 小时的趋势点，自动降采样到最多 240 点"""
    if hours <= 0:
        hours = 1
    if hours > MAX_KEEP_DAYS * 24:
        hours = MAX_KEEP_DAYS * 24
    since = int(time.time()) - hours * 3600
    conn = get_conn()
    rows = conn.execute(
        "SELECT ts, gpu_used_mib, gpu_free_mib, gpu_util, mem_used_mb, mem_total_mb FROM trend WHERE ts >= ? ORDER BY ts",
        (since,),
    ).fetchall()
    conn.close()
    if not rows:
        return {"points": [], "interval": SAMPLE_INTERVAL, "sample_started": False}

    # 降采样：如果点太多，按时间桶聚合求均值
    max_points = 240
    points = [dict(r) for r in rows]
    if len(points) > max_points:
        bucket = (len(points) + max_points - 1) // max_points
        agg = []
        for i in range(0, len(points), bucket):
            chunk = points[i:i + bucket]
            agg.append({
                "ts": chunk[0]["ts"],
                "gpu_used_mib": round(sum(p["gpu_used_mib"] for p in chunk) / len(chunk)),
                "gpu_free_mib": round(sum(p["gpu_free_mib"] for p in chunk) / len(chunk)),
                "gpu_util": round(sum(p["gpu_util"] for p in chunk) / len(chunk)),
                "mem_used_mb": round(sum(p["mem_used_mb"] for p in chunk) / len(chunk)),
                "mem_total_mb": round(sum(p["mem_total_mb"] for p in chunk) / len(chunk)),
            })
        points = agg
    return {"points": points, "interval": SAMPLE_INTERVAL, "sample_started": True}
