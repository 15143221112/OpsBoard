"""系统命令封装：nvidia-smi / ps / ss / systemctl / docker / journalctl"""
import subprocess
import re
import os
import time

# 面板以 ubuntu 运行，需 sudoers NOPASSWD 放行下面这些命令
SUDO_BIN = os.environ.get("APP_PANEL_SUDO", "/usr/bin/sudo")


def _run(cmd, timeout=8):
    """执行命令，返回 (ok, stdout)"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "(timeout)"
    except Exception as e:
        return False, str(e)


def _sudo(cmd, timeout=8):
    return _run(f"{SUDO_BIN} -n {cmd}", timeout=timeout)


# ---------- GPU 显存（按 cgroup 全子进程匹配） ----------
def gpu_process_mem():
    """返回 {pid: used_mib}，PID 需与 ps 进程对应"""
    ok, out = _run("nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits", timeout=8)
    result = {}
    if not ok:
        return result
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            pid = parts[0].strip()
            mem = parts[1].strip()
            if pid.isdigit() and mem.isdigit():
                result[int(pid)] = int(mem)
    return result


def _pids_in_cgroup(unit_name):
    """返回 systemd 服务 cgroup 下所有 PID（含子进程，如 VLLM::EngineCore）"""
    if unit_name.endswith(".service"):
        # 首选 cgroup.procs（最可靠，含所有子进程 PID）
        ok, out = _sudo(f"cat /sys/fs/cgroup/system.slice/{unit_name}/cgroup.procs", timeout=6)
        pids = set()
        if ok:
            for line in out.splitlines():
                if line.strip().isdigit():
                    pids.add(int(line.strip()))
            if pids:
                return pids
        # 兜底：ControlGroup 值里提取数字
        ok2, out2 = _sudo(f"systemctl show -p ControlGroup --value {unit_name}", timeout=6)
        if ok2:
            for tok in out2.split():
                if tok.isdigit():
                    pids.add(int(tok))
        return pids
    return set()


def gpu_total():
    """返回 GPU 总显存信息"""
    ok, out = _run("nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits", timeout=8)
    if not ok:
        return {"name": "无GPU/不可用", "total": 0, "used": 0, "free": 0, "util": 0}
    parts = out.split(",")
    return {
        "name": parts[0].strip(),
        "total": int(parts[1].strip()),
        "used": int(parts[2].strip()),
        "free": int(parts[3].strip()),
        "util": int(parts[4].strip()),
    }


# ---------- 内存 ----------
def memory_total():
    ok, out = _run("free -m")
    if not ok:
        return {"total": 0, "used": 0, "free": 0, "available": 0}
    for line in out.splitlines():
        # 兼容中英文表头（Mem: / 内存：）
        if line.startswith("Mem:") or line.startswith("内存"):
            parts = line.split()
            return {
                "total": int(parts[1]), "used": int(parts[2]),
                "free": int(parts[3]), "available": int(parts[6]),
            }
    return {"total": 0, "used": 0, "free": 0, "available": 0}


# ---------- CPU 占用 ----------
def cpu_total():
    """返回 CPU 使用率(%)：读取 /proc/stat 两次取样"""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        if len(parts) < 8 or parts[0] != "cpu":
            return {"util": 0}
        idle0 = int(parts[4]); total0 = sum(int(x) for x in parts[1:8])
    except Exception:
        return {"util": 0}
    time.sleep(0.5)
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        idle1 = int(parts[4]); total1 = sum(int(x) for x in parts[1:8])
    except Exception:
        return {"util": 0}
    dt = total1 - total0
    if dt <= 0:
        return {"util": 0}
    return {"util": round((1 - (idle1 - idle0) / dt) * 100, 1)}
def _pid_of_service(unit_name: str):
    """获取 systemd 服务主 PID"""
    if unit_name.endswith(".service"):
        ok, out = _sudo(f"systemctl show -p MainPID --value {unit_name}", timeout=6)
        if ok and out.strip().isdigit():
            return int(out.strip())
    return None


def _docker_container_pid(container_name: str):
    ok, out = _run(f"docker inspect -f '{{{{.State.Pid}}}}' {container_name} 2>/dev/null", timeout=6)
    if ok and out.strip().isdigit():
        return int(out.strip())
    return None


def _mem_of_pids(pids):
    """多个 PID 的内存 RSS(MB) 之和；返回 {pid: mem_mb}"""
    result = {}
    for pid in pids:
        ok, out = _run(f"ps -o rss= -p {pid}", timeout=5)
        if ok and out.strip().isdigit():
            result[pid] = int(out.strip()) // 1024
    return result


def _port_listening(port):
    if not port:
        return False
    ok, out = _run(f"ss -tln | grep -E ':{port}\\b' | head -1", timeout=5)
    return ok and bool(out.strip())


def _systemd_active(unit_name: str):
    ok, out = _sudo(f"systemctl is-active {unit_name}", timeout=6)
    return ok and out.strip() == "active"


def _docker_running(container_name: str):
    ok, out = _run(f"docker inspect -f '{{{{.State.Running}}}}' {container_name} 2>/dev/null", timeout=6)
    return ok and out.strip() == "true"


def _health_check(port):
    """对端口做健康探活（尝试 /health、/v1/models、/）"""
    if not port:
        return "unknown"
    for path in ["/health", "/v1/models", "/"]:
        ok, out = _run(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 2 http://127.0.0.1:{port}{path}", timeout=5)
        if ok and out.strip() == "200":
            return "ok"
    # 端口开着但无 200 → running
    if _port_listening(port):
        return "running"
    return "unknown"


def app_status(app):
    """返回单个应用实时状态"""
    unit = app["unit_name"]
    is_docker = app["type"] == "docker"
    port = app["port"]

    if is_docker:
        running = _docker_running(unit)
        pid = _docker_container_pid(unit) if running else None
    else:
        running = _systemd_active(unit)
        pid = _pid_of_service(unit) if running else None

    mem_map = _mem_of_pids([pid]) if pid else {}
    mem_mb = sum(mem_map.values())

    # 显存：匹配 cgroup 内所有子进程（vLLM 显存在 VLLM::EngineCore 子进程）
    gpu_mem = 0
    if pid:
        gpu = gpu_process_mem()
        gpu_mem = gpu.get(pid, 0)
        if gpu_mem == 0:
            cg_pids = _pids_in_cgroup(unit)
            for p in cg_pids:
                gpu_mem += gpu.get(p, 0)

    listening = _port_listening(port)

    # 状态判定
    if running:
        status = "running"
    else:
        status = "stopped"

    return {
        "id": app["id"],
        "name": app["name"],
        "type": app["type"],
        "unit_name": unit,
        "port": port,
        "deploy_type": app["deploy_type"],
        "path": app["path"],
        "web_url": app["web_url"],
        "web_user": app.get("web_user", "") or "",
        "web_password": app.get("web_password", "") or "",
        "group_name": app["group_name"],
        "icon": app["icon"],
        "remark": app["remark"],
        "status": status,
        "listening": listening,
        "mem_mb": mem_mb,
        "gpu_mib": gpu_mem,
        "health": _health_check(port),
        "enabled": app_enabled_state(app),
    }


# ---------- 启停 ----------
def app_action(app, action: str):
    """action: start / stop / restart"""
    unit = app["unit_name"]
    if app["type"] == "docker":
        cmd = f"docker {action} {unit}"
        return _run(cmd, timeout=30)
    else:
        cmd = f"systemctl {action} {unit}"
        return _sudo(cmd, timeout=30)


def app_enable(app, enable: bool):
    """开机自启：enable / disable"""
    unit = app["unit_name"]
    if app["type"] == "docker":
        # docker 容器默认随 docker 启动；这里返回提示
        return False, "docker 容器不支持单独开机自启开关（随 docker 服务启动）"
    act = "enable" if enable else "disable"
    ok, out = _sudo(f"systemctl {act} {unit}", timeout=20)
    return ok, out


def app_enabled_state(app):
    """返回开机自启状态：enabled / disabled / n/a
    归一化 systemctl is-enabled 的输出：enabled-runtime/static/generated/alias 等一律归为 enabled/disabled
    """
    unit = app["unit_name"]
    if app["type"] == "docker":
        return "n/a"
    ok, out = _sudo(f"systemctl is-enabled {unit}", timeout=6)
    if not ok:
        out = out.strip() or "unknown"
        return "enabled" if out in ("enabled",) else ("disabled" if out == "disabled" else "unknown")
    s = out.strip()
    if s.startswith("enabled"):
        return "enabled"
    if s.startswith("disabled"):
        return "disabled"
    # static / generated / indirect / alias → 有启动依赖，算 enabled 更贴近"自启"
    if s in ("static", "generated", "indirect", "alias", "linked", "masked"):
        return "enabled" if s != "masked" else "disabled"
    return "unknown"


# ---------- 实时日志 ----------
def log_lines(app, tail=200):
    """取历史日志 tail 行"""
    unit = app["unit_name"]
    if app["type"] == "docker":
        return _run(f"docker logs --tail {tail} -t {unit} 2>&1", timeout=8)
    else:
        return _sudo(f"journalctl -u {unit} -n {tail} --no-pager -o short-iso 2>&1", timeout=8)


def log_follow_cmd(app):
    """返回实时日志的跟随命令（供 SSE 子进程）"""
    unit = app["unit_name"]
    if app["type"] == "docker":
        return ["docker", "logs", "-f", "-t", unit]
    else:
        return [SUDO_BIN, "-n", "journalctl", "-u", unit, "-f", "--no-pager", "-o", "short-iso"]
