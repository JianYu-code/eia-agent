import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.api import projects, knowledge, admin, files, generate, issues
from app.config import BASE_DIR

_TEMPLATES_DIR = str(BASE_DIR.parent / "frontend" / "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    try:
        from app.api.deps import get_admin_token
        print(f"\n🔑 管理员口令: {get_admin_token()}")
        print("   （用于大模型API/知识库管理/知识入库页面访问；可用环境变量 ADMIN_TOKEN 固定）\n")
    except Exception:
        pass
    try:
        from app.engine.coefficient_db import init_coefficient_db, init_waste_db
        await init_coefficient_db()
        await init_waste_db()
    except Exception:
        pass
    try:
        import asyncio
        from app.engine.standards_index import ensure_standards_index
        await asyncio.to_thread(ensure_standards_index)
    except Exception:
        pass
    yield


app = FastAPI(title="恒新环保智能系统", lifespan=lifespan)

_online_hits: dict[str, float] = {}
_today_visitors: set[str] = set()
_today_stamp: str = ""
ONLINE_WINDOW_SEC = 180


@app.middleware("http")
async def _track_visitors(request: Request, call_next):
    """局域网访问统计：记录每个 IP 的最后活动时间"""
    ip = request.client.host if request.client else ""
    if ip:
        global _today_stamp
        now = time.time()
        _online_hits[ip] = now
        today = time.strftime("%Y-%m-%d")
        if today != _today_stamp:
            _today_stamp = today
            _today_visitors.clear()
        _today_visitors.add(ip)
    return await call_next(request)


@app.get("/api/online")
async def api_online():
    """实时在线（3分钟窗口内有活动的 IP 数）+ 今日累计访客。开放接口。"""
    now = time.time()
    for ip in [k for k, t in _online_hits.items() if now - t > ONLINE_WINDOW_SEC]:
        _online_hits.pop(ip, None)
    return {"online": len(_online_hits), "today_visitors": len(_today_visitors),
            "window_sec": ONLINE_WINDOW_SEC}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(knowledge.router)
app.include_router(admin.router)
app.include_router(files.router)
app.include_router(generate.router)
app.include_router(issues.router)

app.mount("/static", StaticFiles(directory=str(BASE_DIR.parent / "frontend" / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/app/overview", response_class=HTMLResponse)
async def app_overview(request: Request):
    return templates.TemplateResponse("overview.html", {"request": request})


@app.get("/app/audit", response_class=HTMLResponse)
async def app_audit(request: Request):
    return templates.TemplateResponse("audit.html", {"request": request})


@app.get("/app/realtime", response_class=HTMLResponse)
async def app_realtime(request: Request):
    return templates.TemplateResponse("realtime.html", {"request": request})


@app.get("/app/knowledge", response_class=HTMLResponse)
async def app_knowledge(request: Request):
    return templates.TemplateResponse("knowledge.html", {"request": request})


@app.get("/app/knowledge-hub", response_class=HTMLResponse)
async def app_knowledge_hub(request: Request):
    return templates.TemplateResponse("knowledge_hub.html", {"request": request, "active_page": "knowledge_hub"})


@app.get("/app/rules", response_class=HTMLResponse)
async def app_rules(request: Request):
    return templates.TemplateResponse("rules.html", {"request": request})


@app.get("/app/coord", response_class=HTMLResponse)
async def app_coord(request: Request):
    return templates.TemplateResponse("coord.html", {"request": request})


@app.get("/app/generate", response_class=HTMLResponse)
async def app_generate(request: Request):
    return templates.TemplateResponse("generate.html", {"request": request})


@app.get("/app/settings", response_class=HTMLResponse)
async def app_settings(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


@app.get("/app/calculator", response_class=HTMLResponse)
async def app_calculator(request: Request):
    return templates.TemplateResponse("calculator.html", {"request": request})


@app.get("/app/files", response_class=HTMLResponse)
async def app_files(request: Request):
    return templates.TemplateResponse("files.html", {"request": request, "active_page": "files"})


@app.get("/app/admin/files", response_class=HTMLResponse)
async def app_admin_files(request: Request):
    return templates.TemplateResponse("admin/files.html", {"request": request})


@app.get("/app/admin/logs", response_class=HTMLResponse)
async def app_admin_logs(request: Request):
    return templates.TemplateResponse("admin/logs.html", {"request": request})
