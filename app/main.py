import os
import json
import logging
from datetime import date
from contextlib import asynccontextmanager

# 启动时优先加载项目根目录 .env（存放 MINIMAX_API_KEY 等敏感配置）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from app.models.database import init_db
from app.api.members import router as members_router
from app.api.broadcast import router as broadcast_router
from app.api.admin import router as admin_router
from app.api.emotion import router as emotion_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WebSocketManager:
    """WebSocket连接管理器"""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket连接建立，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


websocket_manager = WebSocketManager()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库
    init_db()
    logger.info("数据库初始化完成")

    # 真实产品：不填充任何演示成员/日程/播报，仅确保最基础的默认配置存在
    from app.models.database import SessionLocal
    db = SessionLocal()
    try:
        from app.api.admin import init_base_config
        init_base_config(db)
    finally:
        db.close()

    yield
    logger.info("应用关闭")


app = FastAPI(title="Memory Companion", version="2.0", lifespan=lifespan)

# 注册路由
app.include_router(members_router)
app.include_router(broadcast_router)
app.include_router(admin_router)
app.include_router(emotion_router)

# 静态文件
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """根路径重定向到播放页"""
    return FileResponse(os.path.join(TEMPLATES_DIR, "play.html"))


@app.get("/config", response_class=HTMLResponse)
async def config_page():
    """家属配置页"""
    return FileResponse(os.path.join(TEMPLATES_DIR, "config.html"))


@app.get("/play", response_class=HTMLResponse)
async def play_page():
    """患者播放页"""
    return FileResponse(os.path.join(TEMPLATES_DIR, "play.html"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket端点，用于实时推送播报"""
    await websocket_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # 处理前端发来的消息（如心跳）
            msg = json.loads(data) if data else {}
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
