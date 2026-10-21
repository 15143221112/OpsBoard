"""远程主机采集器（SSH/paramiko）

接口签名与 system.py 保持一致：
  - gpu_total() / memory_total() / cpu_total() / disk_total() / hostname() / uptime()
  - app_status(app)  # app 字典里必须含 host_id
"""
import time
import threading
import paramiko

# 连接缓存：{host_id: SSHClient}
_conn_cache = {}
_lock = threading.Lock()


def _get_client(host_row):
    """按 hosts 行建立或复用 SSH 连接"""
    hid = host_row["id"]
    with _lock:
        cli = _conn_cache.get(hid)
        if cli:
            transport = cli.get_transport()
            if transport and transport.is_active():
                return cli
            # 连接死了就清掉重建
            try:
                cli.close()
            except Exception:
                pass
            _conn_cache.pop(hid, None)

        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(
            hostname=host_row["ip"],
            port=int(host_row.get("ssh_port") or 22),
            username=host_row["ssh_user"],
            password=host_row["ssh_password"] or None,
            timeout=8,
            allow_agent=False,
            look_for_keys=False,
        )
        _conn_cache[hid] = cli
        return cli


def run(host_row, cmd: str, timeout: int = 10, use_sudo: bool = False):
    """远程执行命令，返回 (ok, stdout_str)。
    use_sudo=True 时把密码通过 stdin 喂给 sudo -S。
    """
    if use_sudo and host_row.get("ssh_password"):
        # 在远程用 bash -c 执行：先 echo 密码 | sudo -S 跑目标命令
        wrapped = f"echo {_ssh_quote(host_row['ssh_password'])} | sudo -S -S bash -c {_ssh_quote(cmd)}"
    else:
        wrapped = cmd
    cli = _get_client(host_row)
    try:
        stdin, stdout, stderr = cli.exec_command(wrapped, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        code = stdout.channel.recv_exit_status()
        return code == 0, out
    except Exception as e:
        # 连接可能断了，清掉缓存下次重连
        with _lock:
            _conn_cache.pop(host_row["id"], None)
        return False, f"(ssh error: {e})"


def _ssh_quote(s: str) -> str:
    """给远程 bash -c 用的字符串加单引号转义"""
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


# ---------- GPU（远程多半没有，就返回无 GPU） ----------
def gpu_total(host_row):
    ok, out = run(host_row, "nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits", timeout=6)
    if not ok or not out:
        return {"name": "无独立GPU", "total": 0, "used": 0, "free": 0, "util": 0}
    parts = out.split(",")
    try:
        return {
            "name": parts[0].strip(),
            "total": int(parts[1].strip()),
            "used": int(parts[2].strip()),
            "free": int(parts[3].strip()),
            "util": int(parts[4].strip()),
        }
    except Exception:
        return {"name": "解析失败", "total": 0, "used": 0, "free": 0, "util": 0}


# ---------- 内存 ----------
def memory_total(host_row):
    ok, out = run(host_row, "free -m")
    if not ok:
        return {"total": 0, "used": 0, "free": 0, "available": 0}
    for line in out.splitlines():
        if line.startswith("Mem:") or line.startswith("内存"):
            parts = line.split()
            return {
                "total": int(parts[1]), "used": int(parts[2]),
                "free": int(parts[3]), "available": int(parts[6]),
            }
    return {"total": 0, "used": 0, "free": 0, "available": 0}


# ---------- CPU（远程读 /proc/stat 两次） ----------
def cpu_total(host_row):
    def _read():
        ok, out = run(host_row, "head -1 /proc/stat")
        if not ok:
            return None, None
        parts = out.split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:8])
        return idle, total

    idle0, total0 = _read()
    if idle0 is None:
        return {"util": 0}
    time.sleep(0.5)
    idle1, total1 = _read()
    if idle1 is None:
        return {"util": 0}
    dt = total1 - total0
    if dt <= 0:
        return {"util": 0}
    return {"util": round((1 - (idle1 - idle0) / dt) * 100, 1)}


# ---------- 硬盘 ----------
def disk_total(host_row):
    ok, out = run(host_row, "df -P /")
    if not ok:
        return {"total": 0, "used": 0, "free": 0, "pct": 0}
    lines = out.splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 5:
            try:
                total_kb = int(parts[1]); used_kb = int(parts[2]); free_kb = int(parts[3])
                pct = int(parts[4].rstrip("%"))
            except (ValueError, IndexError):
                continue
            return {
                "total": round(total_kb / 1024 / 1024, 1),
                "used": round(used_kb / 1024 / 1024, 1),
                "free": round(free_kb / 1024 / 1024, 1),
                "pct": pct,
            }
    return {"total": 0, "used": 0, "free": 0, "pct": 0}


def hostname(host_row):
    ok, out = run(host_row, "hostname")
    return out if ok else "unknown"


def uptime(host_row):
    ok, out = run(host_row, "uptime -p")
    return out if ok else ""


# ---------- 应用状态（远程主要是 docker 容器） ----------
def _docker_running(host_row, name: str):
    # 远程 docker 一般需要 sudo
    ok, out = run(host_row, f"docker inspect -f '{{{{.State.Running}}}}' {_ssh_quote(name)}", use_sudo=True, timeout=8)
    return ok and out.strip().lower() == "true"


def _docker_stats(host_row, name: str):
    """返回 (cpu_pct_str, mem_mb)"""
    ok, out = run(
        host_row,
        f"docker stats --no-stream --format '{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}' {_ssh_quote(name)}",
        use_sudo=True, timeout=10,
    )
    if not ok or not out:
        return "", 0
    try:
        cpu_str, mem_str = out.split("|", 1)
        mem_part = mem_str.split("/")[0].strip()
        # 解析 "123.4MiB" / "1.2GiB" / "500kB"
        num = float("".join(c for c in mem_part if c.isdigit() or c == "."))
        low = mem_part.lower()
        if "gib" in low or "gb" in low:
            mem_mb = int(num * 1024)
        elif "mib" in low or "mb" in low:
            mem_mb = int(num)
        elif "kib" in low or "kb" in low:
            mem_mb = int(num / 1024)
        else:
            mem_mb = int(num)
        return cpu_str.strip(), mem_mb
    except Exception:
        return "", 0


def _port_listening(host_row, port):
    if not port:
        return False
    ok, out = run(host_row, f"ss -tln | grep -E ':{port}\\b' | head -1", use_sudo=True, timeout=6)
    return ok and bool(out.strip())


def _health_check(host_row, port):
    if not port:
        return "unknown"
    for path in ["/health", "/", "/"]:
        ok, out = run(host_row, f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 3 http://127.0.0.1:{port}{path}", timeout=6)
        if ok and out.strip() == "200":
            return "ok"
    if _port_listening(host_row, port):
        return "running"
    return "unknown"


def app_status(host_row, app: dict) -> dict:
    # 虚拟分组节点：不采集
    if app.get("type") == "group":
        return {
            "id": app["id"], "name": app["name"], "type": "group",
            "unit_name": app["unit_name"], "port": None,
            "deploy_type": "group", "path": "", "web_url": "",
            "web_user": "", "web_password": "",
            "group_name": app.get("group_name", ""), "icon": app.get("icon", "📁"),
            "remark": app.get("remark", ""), "sort": app.get("sort", 0),
            "parent_id": app.get("parent_id"), "host_id": app.get("host_id"),
            "status": "running", "listening": False,
            "mem_mb": 0, "cpu_pct": "", "gpu_mib": 0,
            "health": "ok", "enabled": "n/a",
        }

    unit = app["unit_name"]
    is_docker = app["type"] == "docker"

    if is_docker:
        running = _docker_running(host_row, unit)
        cpu_str, mem_mb = _docker_stats(host_row, unit) if running else ("", 0)
    else:
        # 远程 systemd 服务（少见，预留）
        ok, out = run(host_row, f"systemctl is-active {_ssh_quote(unit)}", use_sudo=True, timeout=6)
        running = ok and out.strip() == "active"
        cpu_str, mem_mb = "", 0

    port = app.get("port")
    listening = _port_listening(host_row, port)

    return {
        "id": app["id"],
        "name": app["name"],
        "type": app["type"],
        "unit_name": unit,
        "port": port,
        "deploy_type": app.get("deploy_type", ""),
        "path": app.get("path", ""),
        "web_url": app.get("web_url", ""),
        "web_user": app.get("web_user", "") or "",
        "web_password": app.get("web_password", "") or "",
        "group_name": app.get("group_name", ""),
        "icon": app.get("icon", "📦"),
        "remark": app.get("remark", ""),
        "sort": app.get("sort", 0),
        "parent_id": app.get("parent_id"),
        "host_id": app.get("host_id"),
        "status": "running" if running else "stopped",
        "listening": listening,
        "mem_mb": mem_mb,
        "cpu_pct": cpu_str,
        "gpu_mib": 0,  # 远程主机显存另算
        "health": _health_check(host_row, port),
        "enabled": "n/a",
    }
