"""app-panel 主入口"""
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import auth, apps, logs, overview, trend, docs, tabs
from .db import init_db

init_db()
trend.start_sampler()

app = FastAPI(title="App Panel", docs_url="/api/docs", openapi_url="/api/openapi.json")

app.include_router(auth.router)
app.include_router(apps.router)
app.include_router(docs.router)
app.include_router(logs.router)
app.include_router(overview.router)
app.include_router(trend.router)
app.include_router(tabs.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 前端静态资源挂到 /static，避免 mount "/" 遮蔽 API 路由
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
def index():
    f = static_dir / "index.html"
    if f.exists():
        return FileResponse(f)
    return {"msg": "backend ok (no frontend yet)"}
