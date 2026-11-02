"""应用列表、状态、启停、增删改、导出"""
import csv
import io
import json
import re
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


def _size_to_mb(size):
    """把 ollama ps 的 SIZE 字符串('17 GB'/'850 MB')转成 MB；数字(字节)也兼容"""
    if size is None:
        return 0
    if isinstance(size, (int, float)):
        return int(size / 1024 / 1024)
    try:
        s = str(size).strip()
        num = float(s.split()[0])
        low = s.lower()
        if "tb" in low:
            return int(num * 1024 * 1024)
        if "gb" in low or "gib" in low:
            return int(num * 1024)
        if "mb" in low or "mib" in low:
            return int(num)
        if "kb" in low or "kib" in low:
            return int(num / 1024)
        return int(num * 1024)  # 裸数字默认按 GB
    except Exception:
        return 0


def _parse_ollama_ps(text):
    """解析 `ollama ps` 表格输出（老版本不支持 --format json）。
    行格式: NAME ID SIZE PROCESSOR CONTEXT RUNNER UNTIL
    NAME 无空格，SIZE 形如 '7.9 GB'，PROCESSOR 形如 '100% GPU' 或 '72%/28% CPU/GPU'。
    """
    models = []
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        m = re.match(r"^(\S+)\s+([0-9a-f]{10,})\s+([\d.]+)\s*([GMK]B)\s+(.*)$", line)
        if not m:
            continue
        name, mid, num, unit, rest = m.groups()
        # rest = "PROCESSOR CONTEXT RUNNER UNTIL..."；CONTEXT 是第一个纯数字 token
        proc_parts, rest_tokens = [], rest.split()
        for t in rest_tokens:
            if t.isdigit():
                break
            proc_parts.append(t)
        processor = " ".join(proc_parts) if proc_parts else ""
        models.append({"name": name, "size": f"{num} {unit}", "processor": processor})
    return models


def _ollama_proc_runtime(host_row):
    """返回 ({pid: rss_mb}, {pid: vram_mib})：llama-server 进程的内存 RSS 与 nvidia-smi 实际显存。

    SIZE 是模型大小不是显存，显存必须以 nvidia-smi 实测为准；CPU 模型(如 qwen-embed 走内存)不在 GPU 上，显存为 0。
    """
    if host_row is None:
        run = system._run
    else:
        run = lambda c, timeout=8: system_remote.run(host_row, c, timeout=timeout)
    proc_mem = {}
    ok, out = run("ps -eo pid,rss,cmd 2>/dev/null | grep llama-server | grep -v grep")
    if ok:
        for line in out.splitlines():
            p = line.split(None, 3)
            if len(p) >= 3 and p[0].isdigit():
                proc_mem[int(p[0])] = int(p[1]) // 1024  # rss 单位 KB → MB
    proc_vram = {}
    ok2, out2 = run("nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>/dev/null")
    if ok2:
        for line in out2.splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                proc_vram[int(parts[0])] = int(parts[1])  # MiB
    return proc_mem, proc_vram


def _attach_dyn_children(results, host_row):
    """对 ollama 应用，把当前加载的模型作为动态只读子项附加（parent_id=ollama.id）。

    模型是运行时的，不写库；每次列表实时跑 `ollama ps` 拿当前加载的模型，
    并按 PID 匹配 llama-server 进程得到真实内存(RSS)与显存(nvidia-smi)。
    """
    out = list(results)
    ollama = next((a for a in out if "ollama" in (a.get("name") or "").lower()), None)
    if ollama is None:
        return out
    if host_row is None:
        ok, txt = system._run("ollama ps", timeout=6)
    else:
        ok, txt = system_remote.run(host_row, "ollama ps", timeout=6)
    if not ok or not txt:
        return out
    models = _parse_ollama_ps(txt)
    proc_mem, proc_vram = _ollama_proc_runtime(host_row)
    gpu_procs = [p for p, v in proc_vram.items() if v > 0 and p in proc_mem]
    cpu_procs = [p for p in proc_mem if p not in proc_vram]
    gpi = cpi = 0
    for m in models:
        name = m["name"]
        proc = m["processor"]
        # 显存列以实际 GPU 占用为准：SIZE 是模型大小不是显存。
        # 100% GPU 的模型匹配 GPU 上的 llama-server(真实显存)，CPU/混合模型(走内存)匹配非 GPU 进程，显存为 0。
        mg = re.search(r"(\d+)%\s*GPU", proc)
        gpu_pct = int(mg.group(1)) if mg else 0
        if gpu_pct >= 100 and gpi < len(gpu_procs):
            pid = gpu_procs[gpi]; gpi += 1
            gpu_mb = proc_vram.get(pid, 0)
            mem_mb = proc_mem.get(pid, 0)
        elif gpu_pct < 100 and cpi < len(cpu_procs):
            pid = cpu_procs[cpi]; cpi += 1
            gpu_mb = 0
            mem_mb = proc_mem.get(pid, 0)
        else:
            gpu_mb = _size_to_mb(m["size"]) if gpu_pct >= 100 else 0
            mem_mb = 0
        out.append({
            "id": "dyn-ollama-" + str(name),
            "name": str(name),
            "type": "model",
            "unit_name": str(name),
            "port": None,
            "deploy_type": "model",
            "path": "", "web_url": "", "web_user": "", "web_password": "",
            "group_name": ollama.get("group_name", ""),
            "icon": "🧠",
            "remark": ("处理器: " + str(proc)) if proc else "Ollama 已加载模型",
            "sort": 0,
            "parent_id": ollama["id"],
            "host_id": ollama.get("host_id"),
            "status": "running",
            "listening": False,
            "mem_mb": mem_mb,
            "gpu_mib": gpu_mb,
            "health": "ok",
            "enabled": "n/a",
            "dynamic": True,
        })
    return out


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
    return _attach_dyn_children(_running_first(results), host_row)


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
