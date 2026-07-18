import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CACHE_FILE = CACHE_DIR / "llm_cache.json"

_cache: dict[str, dict] = {}


def _load():
    global _cache
    if CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    else:
        _cache = {}


def _save():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_key(rule_id: str, text: str) -> str:
    h = hashlib.md5((rule_id + text[:30000]).encode()).hexdigest()
    return f"{rule_id}:{h}"


def get(rule_id: str, text: str) -> list[dict] | None:
    if not _cache:
        _load()
    key = _make_key(rule_id, text)
    result = _cache.get(key)
    return result.get("issues") if result else None


def set(rule_id: str, text: str, issues: list[dict]):
    key = _make_key(rule_id, text)
    _cache[key] = {
        "rule_id": rule_id,
        "issues": issues,
    }
    if len(_cache) % 20 == 0:
        _save()


def clear():
    global _cache
    _cache = {}
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def stats() -> dict:
    if not _cache:
        _load()
    return {"entries": len(_cache), "file": str(CACHE_FILE)}
