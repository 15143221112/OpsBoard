<p align="center">
  <img src="https://img.shields.io/badge/XH%20Panel-v1.0.0-blue?style=for-the-badge" alt="version" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="python" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="fastapi" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="license" />
</p>

<h1 align="center">🖥️ XH Panel</h1>

<p align="center">A lightweight application management panel for self-hosted AI servers<br/>
<strong>Real-time resource monitoring · One-click start/stop · Deployment docs · Auto-start · Mobile friendly</strong></p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#license">License</a>
</p>

---

## Why XH Panel?

Running a bunch of AI services on a local/self-hosted server — `vLLM` inference, `whisper` transcription, `embedding`, `rerank`, `ComfyUI`, `WeKnora` knowledge base, `Prometheus` monitoring — each with its own port, deployment method (`venv`/`docker`/`native`), and startup command. It's a mess to manage.

**XH Panel** collects every app into a beautiful visual panel: watch resource usage in real time, start/stop services with one click, browse deployment docs, and manage auto-start. Pure local, zero invasive dependencies, fully customizable.

---

## ✨ Features

| Category | Capability |
|---|---|
| 🔐 **Auth** | JWT + bcrypt, change password, mobile responsive |
| 📊 **Monitoring** | GPU VRAM/utilization, CPU, memory + trend charts |
| 🧩 **Overview** | Card/list dual view, filter by group/status/deploy, search & sort |
| ▶️ **Control** | Start/stop/restart systemd services & docker containers |
| ⚡ **Auto-start** | Per-app toggle |
| 📁 **Docs** | Upload multiple deployment docs per app to MinIO, proxy view/download in-panel |
| 📜 **Logs** | SSE real-time service logs in a large modal |
| 🗃️ **CRUD** | Add/edit/delete apps (name/port/path/credentials) |
| 📤 **Export** | One-click CSV export of all apps |
| 📋 **Audit** | Operation log (login, start/stop, CRUD) |
| 🚀 **Lightweight** | No-framework frontend, single SQLite DB, minimal deps |

---

## 🚀 Quick Start

### Requirements

| Component | Note |
|---|---|
| Python | 3.10+ (server should allow calling systemctl / docker / nvidia-smi) |
| Privilege | `sudo` whitelist or passwordless for service control |
| MinIO | Optional, only for deployment-doc feature (S3 compatible) |

### Install

```bash
git clone <your-repo-url>
cd app-panel

python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt

cp .env.example .env
# Set APP_PANEL_SECRET at minimum; add APP_PANEL_MINIO_* for docs feature

cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8085
```

### One-click deploy (server, optional)

```bash
PORT=1234 ./deploy/deploy.sh
```

> Idempotent and re-runnable; preserves `data` dir and SQLite. systemd: `deploy/app-panel.service`.

### Default account

- First init: `admin / admin123`
- **Production: override with `APP_PANEL_ADMIN_USER` / `APP_PANEL_ADMIN_PASSWORD` and change immediately**
- Change password from the top-right menu after login.

---

## 🏗️ Architecture

```mermaid
graph TD
    subgraph Browser[Browser PC / Mobile]
        UI[Vanilla HTML/JS/CSS Frontend]
    end
    subgraph Server[Server]
        API[FastAPI Backend]
        DB[(SQLite<br/>meta/users/audit)]
        MINIO[(MinIO<br/>deployment docs)]
        subgraph Collect[Collectors]
            SYS[systemctl / docker CLI]
            GPU[nvidia-smi]
            CPU[/proc + ps]
        end
        API --> DB
        API --> MINIO
        API --> SYS
        API --> GPU
        API --> CPU
    end
    UI -- "REST / SSE" --> API
```

### Structure

```
app-panel/
├── backend/app/    # FastAPI modules (auth/apps/system/overview/trend/docs/logs/db/config)
├── backend/static/ # frontend single page
├── deploy/         # one-click server deploy (systemd/sudoers/script)
├── .env.example
├── LICENSE
└── CHANGELOG.md
```

---

## 🔒 Security

- **No hardcoded credentials**: MinIO, JWT secret, admin password all via env vars
- **Proxy download for docs**: in-panel auth fetch, no exposure of MinIO private console
- **Least privilege**: sudoers whitelist only for necessary commands
- **Single SQLite DB**: default admin written once on init; passwords bcrypt-hashed

---

## 🤝 Contributing

Fork → branch (`feature/xxx` / `fix/xxx`) → run `python backend/test_api.py` → PR.

---

## 📄 License

[MIT](LICENSE) © 2026 15143221112
