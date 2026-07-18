import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.api import projects, knowledge, admin, files, generate, dify
from app.config import BASE_DIR

_TEMPLATES_DIR = str(BASE_DIR.parent / "frontend" / "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    try:
        from app.engine.coefficient_db import init_coefficient_db, init_waste_db
        await init_coefficient_db()
        await init_waste_db()
    except Exception:
        pass
    yield


app = FastAPI(title="恒新环保智能系统", lifespan=lifespan)

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
app.include_router(dify.router)

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
