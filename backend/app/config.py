"""app-panel 配置"""
import os
from pathlib import Path

# 基础路径
BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = Path(os.environ.get("APP_PANEL_DATA", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite 数据库文件
DB_PATH = DATA_DIR / "data.db"

# JWT 配置
SECRET_KEY = os.environ.get("APP_PANEL_SECRET") or "change-me-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24h

# 面板服务端口（前端静态由 FastAPI 托管，无需单独端口）
PANEL_PORT = int(os.environ.get("APP_PANEL_PORT", "8085"))

# 状态采集间隔（秒）
STATUS_INTERVAL = 5
