"""SQLite 数据库初始化与连接"""
import os
import sqlite3
from contextlib import contextmanager

import bcrypt

from .config import DB_PATH


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # 用户表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 应用清单表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS apps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL DEFAULT 'systemd',   -- systemd / docker
            unit_name TEXT NOT NULL,                 -- systemd 服务名 或 docker 容器名
            port INTEGER,
            deploy_type TEXT DEFAULT 'venv',         -- venv / docker / native
            path TEXT DEFAULT '',
            web_url TEXT DEFAULT '',
            group_name TEXT DEFAULT '其他',
            icon TEXT DEFAULT '📦',
            enabled INTEGER DEFAULT 1,
            remark TEXT DEFAULT '',
            sort INTEGER DEFAULT 0,
            web_user TEXT DEFAULT '',
            web_password TEXT DEFAULT ''
        )
    """)
    # 操作日志表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            target TEXT,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 历史趋势采样表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            gpu_used_mib INTEGER,
            gpu_free_mib INTEGER,
            gpu_util INTEGER,
            mem_used_mb INTEGER,
            mem_total_mb INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trend_ts ON trend(ts)")
    # 应用部署文档表（上传到 minio，桶 document）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            key TEXT NOT NULL,          -- minio 对象 key
            url TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_app_docs_app ON app_docs(app_id)")
    # 迁移：为已有库补充 web_user / web_password 列
    cols = {r[1] for r in cur.execute("PRAGMA table_info(apps)").fetchall()}
    if "web_user" not in cols:
        cur.execute("ALTER TABLE apps ADD COLUMN web_user TEXT DEFAULT ''")
    if "web_password" not in cols:
        cur.execute("ALTER TABLE apps ADD COLUMN web_password TEXT DEFAULT ''")
    conn.commit()

    # 默认管理员：admin / 123456（仅首次初始化，生产请通过环境变量指定并尽快修改）
    DEFAULT_ADMIN = os.environ.get("APP_PANEL_ADMIN_USER", "admin")
    DEFAULT_PWD = os.environ.get("APP_PANEL_ADMIN_PASSWORD", "admin123")
    cur.execute("SELECT id FROM users WHERE username=?", (DEFAULT_ADMIN,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (DEFAULT_ADMIN, hash_password(DEFAULT_PWD), "admin"),
        )
        conn.commit()

    # 预填应用清单（若为空）
    cur.execute("SELECT COUNT(*) FROM apps")
    if cur.fetchone()[0] == 0:
        _seed_apps(cur)
        conn.commit()
    conn.close()


def _seed_apps(cur):
    """预填台账数据"""
    apps = [
        # name, type, unit_name, port, deploy_type, path, web_url, group, icon, remark
        ("rerank", "systemd", "rerank.service", 8001, "venv", "/opt/vllm-venv-new",
         "http://127.0.0.1:8001/v1", "AI", "🔍", "bge-reranker-v2-m3"),
        ("vl-8b", "systemd", "vl-8b.service", 8002, "venv", "/opt/vllm-venv-new",
         "http://127.0.0.1:8002/v1", "AI", "🖼️", "Qwen3-VL-8B-FP8"),
        ("whisper", "systemd", "whisper.service", 8003, "venv", "/opt/whisper-venv",
         "http://127.0.0.1:8003", "AI", "🎙️", "faster-whisper-large-v3-turbo"),
        ("embedding", "systemd", "qwen-embedding.service", 8080, "venv", "/opt/vllm-venv-new",
         "http://127.0.0.1:8080/v1", "AI", "🧬", "Qwen3-Embedding-4B"),
        ("voice-server", "systemd", "voice-server.service", 8090, "venv", "/opt/voice_server/.venv",
         "http://127.0.0.1:8090", "AI", "🗣️", "ASR/TTS/KWS"),
        ("douyin-web", "systemd", "douyin-web.service", 9009, "venv", "/opt/douyin-web",
         "http://127.0.0.1:9009", "Web", "🎬", "douyin-distiller Web"),
        ("minio", "systemd", "minio.service", 9001, "native", "/data/minio",
         "http://127.0.0.1:9001", "存储", "🗄️", "MinIO 对象存储"),
        ("mysql", "systemd", "mysql.service", 3306, "native", "",
         "", "数据库", "🗃️", "MySQL"),
        ("postgresql", "systemd", "postgresql@16-main.service", 5432, "native", "",
         "", "数据库", "🐘", "PostgreSQL 16.15+pgvector"),
        ("tdengine", "systemd", "taosd.service", 6041, "native", "",
         "http://127.0.0.1:6041", "数据库", "⏱️", "TDengine 时序库"),
        ("weknora", "docker", "WeKnora-frontend", 8181, "docker", "/home/ubuntu/weknora",
         "http://127.0.0.1:8181", "Docker", "🧠", "知识库前端"),
        ("weknora-api", "docker", "WeKnora-app", 8100, "docker", "/home/ubuntu/weknora",
         "http://127.0.0.1:8100", "Docker", "🔌", "知识库后端"),
        ("langfuse", "docker", "WeKnora-langfuse-web", 3000, "docker", "/home/ubuntu/weknora",
         "http://127.0.0.1:3000", "Docker", "📈", "LLM 追踪"),
        ("grafana", "docker", "monitoring-grafana", 9300, "docker", "/home/ubuntu/monitoring",
         "http://127.0.0.1:9300", "Docker", "📊", "监控面板"),
        ("prometheus", "docker", "monitoring-prometheus", 9090, "docker", "/home/ubuntu/monitoring",
         "http://127.0.0.1:9090", "Docker", "📡", "指标采集"),
    ]
    for i, a in enumerate(apps):
        cur.execute("""
            INSERT INTO apps (name, type, unit_name, port, deploy_type, path, web_url, group_name, icon, remark, sort)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (*a, i))


def log_operation(username, action, target, detail=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO operation_logs (username, action, target, detail) VALUES (?,?,?,?)",
        (username, action, target, detail),
    )
    conn.commit()
    conn.close()
