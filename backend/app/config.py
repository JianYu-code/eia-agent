import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'eia.db'}")

LANCE_DB_DIR = str(BASE_DIR / "data" / "lance")
LANCE_TABLE = "eia_standards"

JWT_SECRET = os.getenv("JWT_SECRET", "eia-agent-dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 24 * 7

UPLOAD_DIR = BASE_DIR / "uploads"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

LLM_DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
LLM_DEFAULT_MODEL = os.getenv("LLM_MODEL", "glm-4-flash")
LLM_DEFAULT_API_KEY = os.getenv("LLM_API_KEY", "")

OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")

VECTOR_DIM = 1024

MINERU_OUTPUT_DIR = os.getenv("MINERU_OUTPUT_DIR", r"C:\Users\haobo\Desktop\output\a")

DIFY_API_URL = os.getenv("DIFY_API_URL", "http://localhost/v1")
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-b3lTjnMSEWmraDarXZbBTPHF")

AUDIT_ENGINE = os.getenv("AUDIT_ENGINE", "dify")  # "pipeline" | "dify"

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
