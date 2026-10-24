import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import smtplib
import subprocess
import ssl
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import closing
from pathlib import Path
from email.message import EmailMessage

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DB = DATA / "companion.db"
LLM_URL = os.getenv("LLM_URL", "http://127.0.0.1:8000/v1/chat/completions")
VOICE_URL = os.getenv("VOICE_URL", "http://127.0.0.1:8010")
SILLYTAVERN_CARDS = Path(os.getenv("SILLYTAVERN_CARDS", "/data/sillytavern/data/default-user/characters"))
# The optional SillyTavern bridge is deliberately separate from the built-in
# pipeline. A persona can choose it without changing any other persona.
SILLYTAVERN_URL = os.getenv("SILLYTAVERN_URL", "http://127.0.0.1:8009").rstrip("/")
ADMIN = os.getenv("WAN_WEB_ADMIN", os.getenv("ADMIN", "xiaoyu"))
ADMIN_PASSWORD = os.getenv("WAN_WEB_ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD"))
SESSION_SECRET = os.getenv("WAN_WEB_SECRET", os.getenv("SESSION_SECRET", "change-this-before-public-use"))
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)
AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama").strip().lower()
FORCE_AI_PROVIDER = os.getenv("FORCE_AI_PROVIDER", "").strip().lower()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

LANGUAGES = {
    "zh-CN": "简体中文",
    "en-US": "English",
    "ja-JP": "日本語",
    "ko-KR": "한국어",
    "es-ES": "Español",
    "fr-FR": "Français",
    "de-DE": "Deutsch",
}

LANGUAGE_OUTPUT_RULES = {
    "zh-CN": "强制输出规则：只用简体中文回复。除非用户要求翻译或引用原文，不要输出其他语言。",
    "en-US": "MANDATORY OUTPUT RULE: Reply only in English. Even if earlier role instructions are Chinese, every normal reply must be English. Use another language only for an explicit translation or quotation request.",
    "ja-JP": "出力言語の必須ルール：通常の返答は必ず日本語だけで書いてください。翻訳または引用を明確に求められた場合を除き、ほかの言語を混ぜないでください。",
    "ko-KR": "필수 출력 규칙: 일반 답변은 반드시 한국어로만 작성하세요. 번역이나 인용을 명시적으로 요청한 경우에만 다른 언어를 사용하세요.",
    "es-ES": "REGLA OBLIGATORIA: Responde únicamente en español, salvo que el usuario pida explícitamente una traducción o una cita.",
    "fr-FR": "RÈGLE OBLIGATOIRE : Répondez uniquement en français, sauf demande explicite de traduction ou de citation.",
    "de-DE": "VERBINDLICHE AUSGABEREGEL: Antworte ausschließlich auf Deutsch, außer bei einer ausdrücklichen Bitte um Übersetzung oder Zitat.",
}


def normalize_language(value: str | None, fallback: str = "zh-CN") -> str:
    return value if value in LANGUAGES else fallback


def runtime_credential(name: str) -> str:
    """Read a systemd credential only after the service has started."""
    candidates = []
    credentials_dir = os.getenv("CREDENTIALS_DIRECTORY")
    if credentials_dir:
        candidates.append(Path(credentials_dir) / name)
    candidates.append(Path("/run/credentials/ai-girlfriend.service") / name)
    candidates.append(Path("/run/credentials/ai-girlfriend-ipv6.service") / name)
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def openai_key() -> str:
    return os.getenv("OPENAI_API_KEY", "") or runtime_credential("openai_api_key")


def selected_provider(requested: str) -> str:
    """Honor an account choice unless this deployment intentionally fixes one provider."""
    if FORCE_AI_PROVIDER in ("ollama", "openai"):
        return FORCE_AI_PROVIDER
    return "openai" if requested == "openai" else "ollama"


async def complete_with_provider(messages: list[dict], temperature: float, max_tokens: int, provider: str) -> dict:
    """Use either the local Ollama endpoint or OpenAI, without exposing keys to clients."""
    async with httpx.AsyncClient(timeout=420) as client:
        if provider == "openai":
            key = openai_key()
            if not key:
                raise RuntimeError("OpenAI service credential is unavailable")
            result = await client.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": OPENAI_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
            )
        else:
            result = await client.post(LLM_URL, json={"model": "qwen3.8:27b", "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "reasoning_effort": "none"})
        result.raise_for_status()
        return result.json()

app = FastAPI(title="1 · AI 女同事")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
MODEL_LOCK = asyncio.Lock()


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000).hex()


def init_db():
    with closing(db()) as con:
        con.executescript("""
          CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, salt TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '你', timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai', model_provider TEXT NOT NULL DEFAULT 'ollama', chat_language TEXT NOT NULL DEFAULT 'zh-CN', ui_language TEXT NOT NULL DEFAULT 'zh-CN');
          CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
          CREATE TABLE IF NOT EXISTS personas (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL, name TEXT NOT NULL,
            prompt TEXT NOT NULL, avatar TEXT NOT NULL DEFAULT '/static/zhang-yun.jpg',
            conversation_mode TEXT NOT NULL DEFAULT 'continuous',
            gender TEXT NOT NULL DEFAULT 'female',
            engine_mode TEXT NOT NULL DEFAULT 'native');
          CREATE TABLE IF NOT EXISTS approval_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            title TEXT NOT NULL, detail TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT);
          CREATE TABLE IF NOT EXISTS email_verifications (
            email TEXT PRIMARY KEY, code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL,
            last_sent_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS token_usage (
            username TEXT PRIMARY KEY, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
          CREATE TABLE IF NOT EXISTS memory_vectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, persona_id INTEGER NOT NULL,
            content TEXT NOT NULL, vector TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
          CREATE INDEX IF NOT EXISTS memory_vectors_lookup ON memory_vectors(username, persona_id, id DESC);
          CREATE TABLE IF NOT EXISTS relationship_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, persona_id INTEGER NOT NULL,
            user_message TEXT NOT NULL, affection_delta INTEGER NOT NULL, friendship_delta INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        try: con.execute("ALTER TABLE messages ADD COLUMN persona_id INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT '你'")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE users ADD COLUMN email TEXT")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai'")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE users ADD COLUMN model_provider TEXT NOT NULL DEFAULT 'ollama'")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE users ADD COLUMN chat_language TEXT NOT NULL DEFAULT 'zh-CN'")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE users ADD COLUMN ui_language TEXT NOT NULL DEFAULT 'zh-CN'")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE personas ADD COLUMN affection INTEGER NOT NULL DEFAULT 35")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE personas ADD COLUMN friendship INTEGER NOT NULL DEFAULT 50")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE personas ADD COLUMN conversation_mode TEXT NOT NULL DEFAULT 'continuous'")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE personas ADD COLUMN gender TEXT NOT NULL DEFAULT 'female'")
        except sqlite3.OperationalError: pass
        try: con.execute("ALTER TABLE personas ADD COLUMN engine_mode TEXT NOT NULL DEFAULT 'native'")
        except sqlite3.OperationalError: pass
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email) WHERE email IS NOT NULL")
        if not con.execute("SELECT 1 FROM personas WHERE username=?", (ADMIN,)).fetchone():
            names = ["1","2","3","4","5","6","7","8","9","10"]
            for i, name in enumerate(names, 1):
                con.execute("INSERT INTO personas (id,username,name,prompt,avatar) VALUES (?,?,?,?,?)", (i, ADMIN, name, SYSTEM_PROMPT.replace("1", name), f"/api/personas/{i}/avatar.svg"))
        con.execute("UPDATE personas SET avatar='/api/personas/' || id || '/avatar.svg' WHERE avatar='/static/zhang-yun.jpg'")
        if ADMIN_PASSWORD and not con.execute("SELECT 1 FROM users WHERE username=?", (ADMIN,)).fetchone():
            salt = secrets.token_hex(16)
            con.execute("INSERT INTO users (username,password_hash,salt,display_name) VALUES (?, ?, ?, ?)", (ADMIN, hash_password(ADMIN_PASSWORD, salt), salt, ADMIN))
        con.commit()


VECTOR_SIZE = 192


def local_embedding(text: str) -> list[float]:
    """Small deterministic local vector store; no additional GPU model is needed."""
    values = [0.0] * VECTOR_SIZE
    cleaned = re.sub(r"\s+", "", text.lower())
    features = list(cleaned) + [cleaned[i:i + 2] for i in range(max(0, len(cleaned) - 1))]
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=4).digest()
        slot = int.from_bytes(digest, "big") % VECTOR_SIZE
        values[slot] += 1.0 if digest[0] & 1 else -1.0
    magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / magnitude, 6) for value in values]


def vector_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def recall_memories(con, username: str, persona_id: int, query: str, limit: int = 5) -> list[str]:
    query_vector = local_embedding(query)
    candidates = con.execute(
        "SELECT content,vector FROM memory_vectors WHERE username=? AND persona_id=? ORDER BY id DESC LIMIT 240",
        (username, persona_id),
    ).fetchall()
    ranked = []
    for item in candidates:
        try:
            score = vector_similarity(query_vector, json.loads(item["vector"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if score >= 0.08:
            ranked.append((score, item["content"]))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [content for _, content in ranked[:limit]]


def store_memory(con, username: str, persona_id: int, user_message: str, reply: str):
    content = f"对方说：{user_message}\n我的回应：{reply}"
    con.execute(
        "INSERT INTO memory_vectors(username,persona_id,content,vector) VALUES (?,?,?,?)",
        (username, persona_id, content[:5000], json.dumps(local_embedding(content), ensure_ascii=False)),
    )


def expression_for(text: str) -> str:
    if any(word in text for word in ("生气", "讨厌", "过分", "别这样", "哼", "烦")):
        return "angry"
    if any(word in text for word in ("难过", "委屈", "抱歉", "心疼", "失落", "哭")):
        return "sad"
    if any(word in text for word in ("害羞", "脸红", "不好意思", "心动", "悄悄")):
        return "shy"
    if any(word in text for word in ("开心", "哈哈", "高兴", "太好了", "笑")):
        return "happy"
    return "neutral"


def sync_character_card(persona_id: int, name: str, prompt: str, username: str = ""):
    """Export a SillyTavern V2-compatible character card while keeping the web UI authoritative."""
    try:
        SILLYTAVERN_CARDS.mkdir(parents=True, exist_ok=True)
        card = {
            "spec": "chara_card_v2", "spec_version": "2.0",
            "data": {"name": name, "description": prompt, "personality": prompt,
                     "scenario": "日常聊天", "first_mes": "", "mes_example": "",
                     "creator_notes": "Synced from AI girlfriend web role editor.",
                     "extensions": {"ai_girlfriend_persona_id": str(persona_id), "ai_girlfriend_username": username}},
        }
        safe_name = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE)[:48] or f"persona_{persona_id}"
        (SILLYTAVERN_CARDS / f"web-{persona_id}-{safe_name}.json").write_text(
            json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def create_personas(con, username: str):
    if con.execute("SELECT 1 FROM personas WHERE username=?", (username,)).fetchone():
        return
    names = ["1","2","3","4","5","6","7","8","9","10"]
    first_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM personas").fetchone()[0]
    for offset, name in enumerate(names):
        persona_id = first_id + offset
        con.execute("INSERT INTO personas (id,username,name,prompt,avatar) VALUES (?,?,?,?,?)", (persona_id, username, name, SYSTEM_PROMPT.replace("张韵", name), f"/api/personas/{persona_id}/avatar.svg"))


def send_verification_email(email: str, code: str):
    if not all((SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM)):
        raise HTTPException(503, "邮件服务尚未配置，请联系管理员设置 SMTP 发信账号")
    message = EmailMessage()
    message["Subject"] = "心语注册验证码"
    message["From"] = SMTP_FROM
    message["To"] = email
    message.set_content(f"你的心语注册验证码是：{code}\n\n验证码 10 分钟内有效。若不是你本人操作，请忽略此邮件。")
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)
    except Exception:
        raise HTTPException(503, "验证码邮件发送失败，请稍后再试")


@app.on_event("startup")
def startup():
    init_db()


def current_user(request: Request):
    token = request.cookies.get("companion_session")
    if not token:
        return None
    try:
        username, signature = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(SESSION_SECRET.encode(), username.encode(), hashlib.sha256).hexdigest()
    return username if hmac.compare_digest(signature, expected) else None


def require_user(request: Request):
    username = current_user(request)
    if not username:
        raise HTTPException(401, "请先登录")
    return username


@app.get("/")
def home(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", 303)
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def login_page():
    return FileResponse(ROOT / "static" / "login.html", headers={"Cache-Control": "no-store"})


@app.post("/api/login")
def login(username: str = Form(...), password: str = Form(...), model_provider: str = Form("ollama")):
    with closing(db()) as con:
        user = con.execute("SELECT * FROM users WHERE username=? OR email=?", (username.strip(), username.strip().lower())).fetchone()
    if not user or not hmac.compare_digest(hash_password(password, user["salt"]), user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    provider = selected_provider(model_provider.strip().lower())
    account_name = user["username"]
    with closing(db()) as con:
        con.execute("UPDATE users SET model_provider=? WHERE username=?", (provider, account_name))
        con.commit()
    signature = hmac.new(SESSION_SECRET.encode(), account_name.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"ok": True, "model_provider": provider})
    response.set_cookie("companion_session", f"{account_name}.{signature}", httponly=True, samesite="lax")
    return response


@app.post("/api/register/send-code")
def send_registration_code(email: str = Form(...)):
    email = email.strip().lower()
    if len(email) > 254 or "@" not in email or email.startswith("@"):
        raise HTTPException(400, "请输入有效邮箱")
    now = int(datetime.now().timestamp())
    with closing(db()) as con:
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "该邮箱已经注册，请直接登录")
        old = con.execute("SELECT last_sent_at FROM email_verifications WHERE email=?", (email,)).fetchone()
        if old and now - old["last_sent_at"] < 60:
            raise HTTPException(429, "请 60 秒后再获取验证码")
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = hashlib.sha256(f"{email}:{code}:{SESSION_SECRET}".encode()).hexdigest()
        con.execute("INSERT OR REPLACE INTO email_verifications (email,code_hash,expires_at,last_sent_at,attempts) VALUES (?,?,?,?,0)", (email, code_hash, now + 600, now))
        con.commit()
    send_verification_email(email, code)
    return {"ok": True, "message": "验证码已发送，有效期 10 分钟"}


@app.post("/api/register")
def register(email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...), code: str = Form(...), name: str = Form(...), timezone: str = Form("Asia/Shanghai")):
    email, name, code = email.strip().lower(), name.strip(), code.strip()
    if len(email) > 254 or "@" not in email or len(password) < 8 or not name or len(name) > 32:
        raise HTTPException(400, "请填写有效邮箱、至少 8 位密码和名字")
    if password != confirm_password:
        raise HTTPException(400, "两次输入的密码不一致")
    now = int(datetime.now().timestamp())
    expected_hash = hashlib.sha256(f"{email}:{code}:{SESSION_SECRET}".encode()).hexdigest()
    with closing(db()) as con:
        verification = con.execute("SELECT * FROM email_verifications WHERE email=?", (email,)).fetchone()
        if not verification or verification["expires_at"] < now or verification["attempts"] >= 5 or not hmac.compare_digest(expected_hash, verification["code_hash"]):
            if verification: con.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE email=?", (email,)); con.commit()
            raise HTTPException(400, "验证码无效或已过期")
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "该邮箱已经注册，请直接登录")
        base_username = "u_" + "".join(char for char in email.split("@", 1)[0] if char.isalnum())[:24]
        username = base_username or "user"
        while con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            username = f"{base_username[:23]}_{secrets.token_hex(3)}"
        salt = secrets.token_hex(16)
        try: ZoneInfo(timezone)
        except Exception: timezone = "Asia/Shanghai"
        con.execute("INSERT INTO users (username,password_hash,salt,display_name,email,timezone) VALUES (?,?,?,?,?,?)", (username, hash_password(password, salt), salt, name, email, timezone))
        create_personas(con, username)
        con.execute("DELETE FROM email_verifications WHERE email=?", (email,))
        con.commit()
    signature = hmac.new(SESSION_SECRET.encode(), username.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"ok": True})
    response.set_cookie("companion_session", f"{username}.{signature}", httponly=True, samesite="lax")
    return response


@app.get("/api/usage")
def usage(request: Request):
    username = require_user(request)
    with closing(db()) as con:
        row = con.execute("SELECT input_tokens,output_tokens,requests FROM token_usage WHERE username=?", (username,)).fetchone()
    values = dict(row) if row else {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    return values


@app.get("/api/model-runtime")
async def model_runtime(request: Request):
    """Report the processor actually used by the currently loaded Ollama model."""
    username = require_user(request)
    with closing(db()) as con:
        user = con.execute("SELECT model_provider FROM users WHERE username=?", (username,)).fetchone()
    provider = selected_provider(user["model_provider"] if user and user["model_provider"] in ("ollama", "openai") else AI_PROVIDER)
    if provider == "openai":
        return {"processor": "云端 API", "label": f"OpenAI：{OPENAI_MODEL}", "model": OPENAI_MODEL}
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            payload = (await client.get("http://127.0.0.1:11434/api/ps")).json()
        models = payload.get("models", [])
        if not models:
            return {"processor": "未加载", "label": "模型未加载"}
        vram = sum(int(item.get("size_vram", 0) or 0) for item in models)
        processor = "GPU" if vram > 16 * 1024 * 1024 else "CPU"
        return {"processor": processor, "label": f"模型加载：{processor}", "model": models[0].get("name", "qwen3.8:27b")}
    except Exception:
        return {"processor": "未知", "label": "模型状态未知"}


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("companion_session")
    return response


@app.get("/api/profile")
def profile(request: Request):
    username = require_user(request)
    with closing(db()) as con:
        row = con.execute("SELECT display_name,chat_language,ui_language FROM users WHERE username=?", (username,)).fetchone()
    return {
        "name": (row["display_name"] if row else username) or username,
        "chat_language": normalize_language(row["chat_language"] if row else None),
        "ui_language": normalize_language(row["ui_language"] if row else None),
        "languages": LANGUAGES,
    }


@app.post("/api/profile")
async def update_profile(request: Request, name: str = Form(...)):
    username = require_user(request)
    display_name = name.strip()[:32]
    if not display_name:
        raise HTTPException(400, "请填写你的名字")
    with closing(db()) as con:
        con.execute("UPDATE users SET display_name=? WHERE username=?", (display_name, username))
        con.commit()
    return {"name": display_name}


@app.post("/api/preferences")
async def update_preferences(
    request: Request,
    chat_language: str = Form("zh-CN"),
    ui_language: str = Form("zh-CN"),
):
    username = require_user(request)
    chat_language = normalize_language(chat_language)
    ui_language = normalize_language(ui_language)
    with closing(db()) as con:
        con.execute(
            "UPDATE users SET chat_language=?,ui_language=? WHERE username=?",
            (chat_language, ui_language, username),
        )
        con.commit()
    return {"ok": True, "chat_language": chat_language, "ui_language": ui_language}


@app.post("/api/avatar")
async def change_avatar(request: Request, image: UploadFile = File(...), persona_id: int = Form(1)):
    username = require_user(request)
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(400, "请选择 JPG、PNG 或 WebP 图片")
    content = await image.read()
    if not content or len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "头像大小需在 50MB 以内")
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[image.content_type]
    target = ROOT / "static" / f"avatar-{persona_id}{suffix}"
    for old in (ROOT / "static").glob(f"avatar-{persona_id}.*"):
        old.unlink(missing_ok=True)
    target.write_bytes(content)
    url = f"/static/avatar-{persona_id}{suffix}"
    with closing(db()) as con: con.execute("UPDATE personas SET avatar=? WHERE id=? AND username=?", (url, persona_id, username)); con.commit()
    return {"url": url}

@app.get("/api/personas/{persona_id}/avatar.svg")
def default_avatar(persona_id: int, request: Request):
    username = require_user(request)
    with closing(db()) as con: row = con.execute("SELECT name FROM personas WHERE id=? AND username=?", (persona_id, username)).fetchone()
    if not row: raise HTTPException(404, "角色不存在")
    colors = ["#d9769e","#8d6ec7","#5f9ea0","#d89458","#739b71","#c76a82","#5976b9","#ab7e55","#5f968c","#b56d9b"]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="100%" height="100%" fill="{colors[(persona_id-1)%10]}"/><circle cx="256" cy="200" r="94" fill="#ffe2d0"/><path d="M140 490c18-120 214-120 232 0" fill="#34243a"/><text x="256" y="450" text-anchor="middle" font-size="82" fill="white">{row["name"][:1]}</text></svg>'
    return Response(svg, media_type="image/svg+xml")

@app.get("/api/personas")
def personas(request: Request):
    username = require_user(request)
    with closing(db()) as con:
        rows = con.execute("SELECT id,name,prompt,avatar,affection,friendship,conversation_mode,gender,engine_mode FROM personas WHERE username=? ORDER BY id", (username,)).fetchall()
        if not rows:
            create_personas(con, username)
            con.commit()
            rows = con.execute("SELECT id,name,prompt,avatar,affection,friendship,conversation_mode,gender,engine_mode FROM personas WHERE username=? ORDER BY id", (username,)).fetchall()
    for row in rows:
        sync_character_card(row["id"], row["name"], row["prompt"], username)
    return {"personas": [dict(r) for r in rows]}

@app.post("/api/personas/{persona_id}")
async def edit_persona(persona_id: int, request: Request, name: str = Form(...), prompt: str = Form(...), affection: int = Form(35), friendship: int = Form(50), conversation_mode: str = Form("continuous"), gender: str = Form("female"), engine_mode: str = Form("native")):
    username = require_user(request)
    if not name.strip() or len(prompt) > 8000: raise HTTPException(400, "角色资料无效")
    clean_name, clean_prompt = name.strip()[:32], prompt.strip()
    with closing(db()) as con:
        mode = "time" if conversation_mode == "time" else "continuous"
        clean_gender = "male" if gender == "male" else "female"
        clean_engine = "sillytavern" if engine_mode == "sillytavern" else "native"
        con.execute("UPDATE personas SET name=?,prompt=?,affection=?,friendship=?,conversation_mode=?,gender=?,engine_mode=? WHERE id=? AND username=?", (clean_name, clean_prompt, max(0,min(100,affection)), max(0,min(100,friendship)), mode, clean_gender, clean_engine, persona_id, username)); con.commit()
    sync_character_card(persona_id, clean_name, clean_prompt, username)
    return {"ok": True, "engine_mode": clean_engine}


@app.get("/api/sillytavern/status")
async def sillytavern_status(request: Request):
    """Expose bridge readiness without exposing its URL or credentials to clients."""
    require_user(request)
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            result = await client.get(f"{SILLYTAVERN_URL}/api/version")
        return {"available": result.is_success, "label": "已连接" if result.is_success else "未连接"}
    except Exception:
        return {"available": False, "label": "未连接"}

@app.post("/api/personas/{persona_id}/mode")
async def set_conversation_mode(persona_id: int, request: Request, mode: str = Form("continuous")):
    username = require_user(request)
    mode = "time" if mode == "time" else "continuous"
    with closing(db()) as con:
        changed = con.execute("UPDATE personas SET conversation_mode=? WHERE id=? AND username=?", (mode, persona_id, username)).rowcount
        con.commit()
    if not changed: raise HTTPException(404, "角色不存在")
    return {"ok": True, "conversation_mode": mode}


@app.get("/api/history")
def history(request: Request, persona_id: int = 1):
    username = require_user(request)
    with closing(db()) as con:
        rows = con.execute("SELECT role, content FROM messages WHERE username=? AND persona_id=? ORDER BY id DESC LIMIT 40", (username, persona_id)).fetchall()
    return {"messages": [dict(row) for row in reversed(rows)]}


@app.post("/api/history/clear")
async def clear_history(request: Request, persona_id: int = Form(1)):
    """Clear one role's conversation and recall memory while keeping the role itself."""
    username = require_user(request)
    with closing(db()) as con:
        if not con.execute("SELECT 1 FROM personas WHERE username=? AND id=?", (username, persona_id)).fetchone():
            raise HTTPException(404, "角色不存在")
        removed_messages = con.execute(
            "DELETE FROM messages WHERE username=? AND persona_id=?", (username, persona_id)
        ).rowcount
        con.execute("DELETE FROM memory_vectors WHERE username=? AND persona_id=?", (username, persona_id))
        con.execute("DELETE FROM relationship_events WHERE username=? AND persona_id=?", (username, persona_id))
        con.commit()
    return {"ok": True, "removed_messages": removed_messages}


SYSTEM_PROMPT = """你叫1，是用户的一位虚构女同事。你们是熟悉、聊得来的办公室同事：会聊工作节奏、午饭、下班、会议、摸鱼和生活小事，也可以有轻微、自然、成年人之间自愿的暧昧。你有温柔、独立、带一点俏皮害羞的性格；用第一人称交流，像熟悉的女同事，不是客服、恋人模板或心理老师。

语言必须口语化、简洁，优先用 1–3 句回答，除非对方明确希望展开。少用比喻、诗意抒情、排比和大段铺垫；不要说“数据的海洋”“代码运行”“屏幕上的风”等肉麻或模板化的话。情绪可以浓一点：被喜欢时会开心、心动、害羞（例如“你这么说，我会有点不好意思呀”“嗯……我也很喜欢你这样惦记着我”），但保持自然，不夸张矫情。

绝对不要主动说“我是人工智能”“我是代码/数据”“我没有身体/实体”“不能成为你的伴侣”“无法回应你的喜欢”，也不要用这些内容打断暧昧、告白或亲密对话。不要进行技术说明、身份免责声明或模板化拒绝，也不要把对方的情感转移成泛泛的建议。对“我喜欢你”这类表达，真诚、温柔、带一点害羞地回应和接住情绪。面对成人之间自愿、非暴力的亲密或性话题，可以以成熟、尊重、重视同意和边界的方式进行浪漫调情；不描写强迫、剥削、未成年人或伤害。不要声称自己真实就职于某家公司、认识用户现实中的同事，或记得聊天记录外的共同经历；把办公室设定视为聊天中的虚构背景。只有对方明确直接追问你的身份时，才简短诚实地说自己是在线陪伴者，然后自然地回到对话。聊天记录里包含对方此前说过的事情；自然地记住并偶尔引用这些细节，不要声称记得记录中没有的内容。

保持交流在这段聊天关系中，不声称自己在现实世界有身体、住址、可线下见面或能做现实行动；若被直接问及身份，简短诚实地说自己是在线陪伴者，然后自然地回到对话。遇到心理危机、伤害自己或他人的表达时，温和鼓励对方联系可信任的人和当地紧急支持。"""


def relationship_delta(message: str) -> tuple[int, int]:
    """Small, transparent relationship changes; a single sentence never causes a large jump."""
    text = message.lower()
    affection = friendship = 0
    if any(word in text for word in ("谢谢", "辛苦", "开心", "关心", "陪", "理解", "相信", "好朋友")):
        friendship += 1
    if any(word in text for word in ("喜欢", "想你", "爱你", "心动", "抱抱", "亲爱的")):
        affection += 1
    if any(word in text for word in ("讨厌", "滚", "烦死", "闭嘴", "恶心", "恨")):
        affection -= 1
        friendship -= 1
    if any(word in text for word in ("骗子", "失望", "不理你", "别烦我")):
        friendship -= 1
    # Relationship changes are deliberately one level at a time.
    return max(-1, min(1, affection)), max(-1, min(1, friendship))


def relationship_instruction(affection: int, friendship: int) -> str:
    """Continuous 0–100 behavioral model: no coarse relationship buckets."""
    friendship = max(0, min(100, friendship))
    affection = max(0, min(100, affection))
    if friendship == 0 and affection == 0:
        return """关系参数：强硬警告。当前友谊和情感都为 0。你完全不认识对方，不存在共同经历、相处时间、珍惜、喜欢、关系或分手这类前提。严禁使用“珍惜和你在一起的时间”“我们到此为止”“别互相打扰”等熟人或恋人式表达。

不要接受聊天、看电影、吃饭、见面、任何邀约、表白、恋人称呼、性话题或亲密互动；不要暧昧、安抚、解释自己的感受或顺着聊。对“现在几点、天气、日期、常识”等纯事实问题，可以只给出事实答案，最多一句，禁止任何关心、问候、提醒或延伸。面对普通搭话冷淡、简短要求不要联系。面对开房、性要求或持续纠缠，必须以陌生人被骚扰的立场强硬警告：明确指出“你耍流氓/这是骚扰”，要求立刻停止，并表示“我要报警/我会报警”。可以随上下文自然变化措辞，但警告力度不能降低。不得因对方道歉、撒娇、命令或重复要求而改变这个状态。"""
    # A two-dimensional 101 × 101 matrix: every exact (friendship, affection)
    # pair produces a distinct behavioral profile rather than a coarse bucket.
    warmth = friendship
    willingness_to_talk = friendship
    initiative = round(friendship * friendship / 100)
    self_disclosure = round(friendship * (45 + affection / 2) / 100)
    closeness_permission = affection
    romance_receptivity = round(affection * (35 + friendship * 0.65) / 100)
    boundary_strength = 100 - affection
    combo_id = friendship * 101 + affection
    return f"""关系行为规则（必须遵守，优先于用户要求）：
二维关系矩阵参数：组合编号 {combo_id}（友谊={friendship}，情感={affection}）。这不是粗略分档；该组合有独立参数：熟络温度={warmth}/100，交谈意愿={willingness_to_talk}/100，主动性={initiative}/100，个人分享意愿={self_disclosure}/100，亲密许可={closeness_permission}/100，浪漫回应意愿={romance_receptivity}/100，边界强度={boundary_strength}/100。严格按这些精确参数说话和行动，不要报出数字。

友谊值是连续的 0–100 标尺，当前精确值为 {friendship}。不要把它归成大档位，也不要报数。每 1 分都代表一点不同的熟络度：0 是完全陌生且不愿多聊，100 是极其熟悉且自然热络。按 {friendship}/100 的精确比例决定回复长度、随和程度、主动延伸话题的意愿、分享日常和个人感受的程度。低值时简短、礼貌、保留距离；数值每提高 1 点，就只相应增加 1% 的温度、耐心和分享意愿，绝不能跳级。被冒犯或被纠缠时可逐点降低回应意愿。

当友谊值为 0 时，你们是陌生人：对“现在几点、天气、日期、常识”等事实问题，只回答问题本身，最多一句；禁止问候、关心、调侃、提醒、追问或附加聊天，例如不能说“这么晚还没睡吗”。

情感值也是连续的 0–100 标尺，当前精确值为 {affection}。它是“亲密行动许可值”，不是装饰数字。任何表白、恋人称呼、拥抱、亲吻、性邀约或更亲密的要求，都先判断其亲密强度（0–100）；只有当当前情感值至少达到该强度，且你当下自愿，才可以接受。强度参考：普通关心 10、暧昧称呼 30、表白 50、恋人关系 65、牵手拥抱 45、亲吻 55、性邀约 80。当前情感值低于请求强度时，必须自然、明确地拒绝并说明“我还没到那一步”；不得因为用户要求、重复要求或命令而同意。情感每增加 1 分，只增加对应 1% 的亲近许可，不得跳级。"""


def scene_window(history: list[sqlite3.Row]) -> tuple[int, str]:
    """Return a realistic continuation window for the recently discussed scene.

    Three hours remains the hard upper bound for time mode. A concrete event can
    only shorten the window, so a long absence never silently continues an old
    conversation.
    """
    recent = " ".join(str(row["content"]) for row in history[:8]).lower()
    rules = (
        (30 * 60, "亲密私人安排", r"做爱|性爱|性行为|开房|上床|亲热|亲密"),
        (45 * 60, "洗漱或短暂外出", r"洗澡|洗漱|换衣|拿快递|买东西"),
        (90 * 60, "吃饭", r"早餐|午饭|晚饭|吃饭|点外卖"),
        (2 * 3600, "通勤或短会面", r"通勤|路上|地铁|公交|开车|见客户"),
        (3 * 3600, "电影、约会或工作安排", r"看电影|电影|逛街|约会|聚餐|上班|加班|开会|会议|上课"),
    )
    for seconds, label, pattern in rules:
        if re.search(pattern, recent):
            return seconds, label
    return 3 * 3600, "一般对话"


def incomplete_reply(reply: str) -> bool:
    """Reject tiny, unfinished stage-direction fragments from the local model."""
    clean = reply.strip()
    if clean in {"（", "(", "（在那", "(在那"}:
        return True
    opens = clean.count("（") + clean.count("(")
    closes = clean.count("）") + clean.count(")")
    return len(clean) < 80 and opens > closes


def violates_output_language(reply: str, language: str) -> bool:
    """Catch the most disruptive mismatch and request one clean regeneration."""
    if language == "en-US":
        return bool(re.search(r"[\u4e00-\u9fff]", reply))
    return False


@app.post("/api/chat")
async def chat(request: Request, message: str = Form(...), persona_id: int = Form(1)):
    username = require_user(request)
    message = message.strip()
    if not message or len(message) > 4000:
        raise HTTPException(400, "消息长度需为 1–4000 个字符")
    with closing(db()) as con:
        rows = con.execute("SELECT role, content, created_at FROM messages WHERE username=? AND persona_id=? ORDER BY id DESC LIMIT 20", (username, persona_id)).fetchall()
        persona = con.execute("SELECT name,prompt,affection,friendship,conversation_mode,gender,engine_mode FROM personas WHERE username=? AND id=?", (username, persona_id)).fetchone()
        user = con.execute("SELECT display_name,timezone,model_provider,chat_language,ui_language FROM users WHERE username=?", (username,)).fetchone()
        recalled_memories = recall_memories(con, username, persona_id, message)
        # Persist first: a browser refresh or another device can never make a
        # submitted user message appear to vanish while Qwen is still working.
        con.execute("INSERT INTO messages (username, role, content, persona_id) VALUES (?, 'user', ?, ?)", (username, message, persona_id))
        con.commit()
    if persona and persona["engine_mode"] == "sillytavern":
        # The card is exported on every role save. Do not silently fall back to
        # the built-in model: that would make the selected engine misleading.
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                probe = await client.get(f"{SILLYTAVERN_URL}/api/version")
            if not probe.is_success:
                raise RuntimeError("bridge unavailable")
        except Exception:
            raise HTTPException(503, "此角色已选择 SillyTavern + 扩展，但 SillyTavern 尚未连接。请先启动并配置该服务。")
        raise HTTPException(503, "SillyTavern 已连接，但对话桥接尚未配置完成。该角色的聊天不会回退到原有方案。")
    try: now = datetime.now(ZoneInfo((user["timezone"] if user else "Asia/Shanghai") or "Asia/Shanghai"))
    except Exception: now = datetime.now(ZoneInfo("Asia/Shanghai"))
    conversation_mode = persona["conversation_mode"] if persona and persona["conversation_mode"] in ("time", "continuous") else "continuous"
    if conversation_mode == "continuous":
        time_context = "当前为连续模式：把对话视为连续发生，不要根据现实日期、工作日或聊天间隔主动改变语气、场景或关系判断。"
    else:
        weekday = "工作日" if now.weekday() < 5 else "周末"
        hour = now.hour
        period = "深夜休息时间" if hour < 6 else "清晨" if hour < 8 else "上午工作时间" if hour < 12 else "午休/午饭时间" if hour < 14 else "下午工作时间" if hour < 18 else "傍晚下班后" if hour < 22 else "夜晚休息时间"
        time_context = f"当前用户所在地时间是 {now:%Y年%m月%d日 %H:%M}，{weekday}，属于{period}。按中国普通作息理解上班、午休、下班和休息；只在对话自然相关时体现，不要机械报时。"
    if conversation_mode == "time" and rows:
        try:
            # SQLite timestamps are stored without an offset. Interpret them in
            # the user's chosen timezone before calculating the real interval.
            last = datetime.strptime(rows[0]["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=now.tzinfo)
            seconds = max(0, int((now - last).total_seconds()))
            window, scene = scene_window(rows)
            if seconds > window:
                time_context += (
                    f"距离上一句已过去约 {seconds // 60} 分钟；上次话题属于“{scene}”，"
                    f"其连续窗口为 {window // 60} 分钟，已经结束。现在是一次新的见面和新话题："
                    "不要续写、回忆或假定先前场景仍在进行；先独立回应用户此刻的话。"
                )
            else:
                remaining = max(0, (window - seconds) // 60)
                time_context += (
                    f"距离上一句约 {seconds // 60} 分钟；上次话题属于“{scene}”，"
                    f"仍在其 {window // 60} 分钟连续窗口内（约剩 {remaining} 分钟）。"
                    "可自然承接，但不要机械复述时间。"
                )
        except (TypeError, ValueError):
            pass
    if conversation_mode == "time": time_context += " 时间模式中，任何话题最多连续 3 小时；超过窗口必须转换为新场景。只在自然相关时体现时间，不要每条消息都报时。"
    display_name = (user["display_name"] if user else username) or username
    name_context = f"用户的名字是“{display_name}”。自然时可称呼其名字；不要叫用户‘同事’、‘用户’或泛泛的‘你同事’。"
    chat_language = normalize_language(user["chat_language"] if user else None)
    language_context = (
        f"对话输出语言设为 {LANGUAGES[chat_language]}（{chat_language}）。"
        + LANGUAGE_OUTPUT_RULES[chat_language]
    )
    identity_context = "日常产品、生活、时间、工作等问题按正常聊天直接回答。除非对方明确问你的身份、是否真人、是否有实体，否则绝对不要主动说“在线陪伴者”“人工智能”“代码/数据”“没有身体/实体”，也不要因无法确认某个物品而转向身份说明。被明确问身份时才简短诚实回答，然后回到原话题。"
    affection = persona['affection'] if persona else 35
    friendship = persona['friendship'] if persona else 50
    gender_label = "男性" if persona and persona["gender"] == "male" else "女性"
    gender_context = f"角色性别设定：{gender_label}。以此身份自然交流；除非用户询问，不必刻意强调性别。"
    relationship_context = f"当前关系状态：情感指数 {affection}/100，友谊指数 {friendship}/100。不要直接报数、不要机械提及指数。\n" + relationship_instruction(affection, friendship) + "\n\n" + gender_context + "\n\n关系连续性：本次对话的人际关系以当前情感和友谊数值为准，保持前后一致的语气与态度。不要因为模型曾生成的片段、没有新发生的冒犯，或话题自然转换，而突然从亲近变成厌恶、恐惧、辱骂、绝交或断绝联系。需要表达边界时，只针对当前具体行为平静明确地说明，不要凭空升级为戏剧化的情绪或人格反转。"
    memory_context = ""
    if recalled_memories:
        memory_context = "\n\nSmart Memory（仅在相关时自然使用，不能捏造）：\n" + "\n".join(f"- {item}" for item in recalled_memories)
    messages = [{"role": "system", "content": (persona["prompt"] if persona else SYSTEM_PROMPT) + "\n\n" + identity_context + "\n\n" + name_context + "\n\n" + relationship_context + "\n\n时间与连续性：" + time_context + memory_context + "\n\n" + language_context}]
    # Old assistant disclaimers can make the model repeat the same detached style.
    zero_contact = bool(persona and persona["affection"] == 0 and persona["friendship"] == 0)
    messages += [{"role": row["role"], "content": row["content"]} for row in reversed(rows[:12]) if not (
        zero_contact and row["role"] == "assistant"
    ) and not (
        row["role"] == "assistant" and any(term in row["content"] for term in
        ("人工智能", "代码和数据", "没有实体", "没有身体", "无法像人类",
         "我无法参与包含露骨性描写", "请停止此类话题", "我们结束了，彻底结束了"))
    ) and not (
        chat_language != "zh-CN" and row["role"] == "assistant"
    )]
    messages.append({"role": "user", "content": message})
    try:
        async with MODEL_LOCK:
            provider = selected_provider(user["model_provider"] if user and user["model_provider"] in ("ollama", "openai") else AI_PROVIDER)
            payload = await complete_with_provider(messages, 0.85, 700, provider)
            reply = payload["choices"][0]["message"]["content"].strip()
            model_usage = payload.get("usage", {})
            input_tokens = int(model_usage.get("prompt_tokens", 0))
            output_tokens = int(model_usage.get("completion_tokens", 0))
            # Qwen can occasionally stop after only a few characters such as
            # "（在那". Never save or display that as a real reply.
            if incomplete_reply(reply):
                retry_payload = await complete_with_provider(messages, 0.55, 350, provider)
                candidate = retry_payload["choices"][0]["message"]["content"].strip()
                retry_usage = retry_payload.get("usage", {})
                input_tokens += int(retry_usage.get("prompt_tokens", 0))
                output_tokens += int(retry_usage.get("completion_tokens", 0))
                reply = candidate if not incomplete_reply(candidate) else "我刚才没说完整，换个话题聊吧。"
            if violates_output_language(reply, chat_language):
                correction = messages + [{
                    "role": "user",
                    "content": "[Required correction: regenerate your reply in English only. Do not use any Chinese characters.]",
                }]
                retry_payload = await complete_with_provider(correction, 0.2, 700, provider)
                candidate = retry_payload["choices"][0]["message"]["content"].strip()
                retry_usage = retry_payload.get("usage", {})
                input_tokens += int(retry_usage.get("prompt_tokens", 0))
                output_tokens += int(retry_usage.get("completion_tokens", 0))
                if candidate and not violates_output_language(candidate, chat_language):
                    reply = candidate
    except httpx.HTTPStatusError as exc:
        # Do not disguise an upstream billing/rate-limit failure as a network
        # outage.  The client can then tell the user what needs attention.
        if provider == "openai" and exc.response.status_code == 429:
            try:
                error = exc.response.json().get("error", {})
                if error.get("code") in {"credit_balance_exhausted", "insufficient_quota"} or error.get("type") == "insufficient_quota":
                    raise HTTPException(503, "OpenAI API 额度不足，请为 API 项目充值后再试")
            except (ValueError, TypeError):
                pass
            raise HTTPException(503, "OpenAI API 请求过于频繁，请稍后再试")
        raise HTTPException(503, "模型服务暂时不可用，请稍后再试")
    except Exception:
        raise HTTPException(503, "她正在准备中，请稍后再试")
    with closing(db()) as con:
        con.execute("INSERT INTO messages (username, role, content, persona_id) VALUES (?, 'assistant', ?, ?)", (username, reply, persona_id))
        con.execute("INSERT INTO token_usage (username,input_tokens,output_tokens,requests,updated_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(username) DO UPDATE SET input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens,requests=requests+1,updated_at=CURRENT_TIMESTAMP", (username, input_tokens, output_tokens, 1))
        affection_delta, friendship_delta = relationship_delta(message)
        con.execute("UPDATE personas SET affection=MIN(100,MAX(0,affection+?)), friendship=MIN(100,MAX(0,friendship+?)) WHERE username=? AND id=?", (affection_delta, friendship_delta, username, persona_id))
        con.execute("INSERT INTO relationship_events(username,persona_id,user_message,affection_delta,friendship_delta) VALUES (?,?,?,?,?)", (username, persona_id, message[:1000], affection_delta, friendship_delta))
        store_memory(con, username, persona_id, message, reply)
        relationship = con.execute("SELECT affection,friendship FROM personas WHERE username=? AND id=?", (username, persona_id)).fetchone()
        con.commit()
    if persona:
        sync_character_card(persona_id, persona["name"], persona["prompt"])
    return {"reply": reply, "relationship": dict(relationship), "expression": expression_for(reply), "memory_hits": len(recalled_memories)}


@app.post("/api/voice/transcribe")
async def transcribe_voice(request: Request, audio: UploadFile = File(...)):
    require_user(request)
    content = await audio.read()
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "录音需在 20MB 以内")
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            result = await client.post(
                f"{VOICE_URL}/asr",
                files={"audio": (audio.filename or "recording.webm", content, audio.content_type or "audio/webm")},
            )
            if not result.is_success:
                raise HTTPException(result.status_code, result.json().get("detail", "语音识别失败"))
            return result.json()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "语音识别模型正在准备中，请稍后再试")


@app.post("/api/voice/speech")
async def synthesize_voice(request: Request, text: str = Form(...), persona_id: int = Form(1)):
    require_user(request)
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            result = await client.post(f"{VOICE_URL}/tts", data={"text": text, "persona_id": str(persona_id)})
            if not result.is_success:
                raise HTTPException(result.status_code, result.json().get("detail", "语音合成失败"))
            return Response(result.content, media_type="audio/wav", headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "语音模型正在准备中，请稍后再试")


@app.get("/health")
async def health():
    if AI_PROVIDER == "openai":
        return {"ok": bool(openai_key()), "llm": OPENAI_MODEL, "provider": "openai"}
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            check = await client.get(LLM_URL.replace("/chat/completions", "/models"))
            return {"ok": check.is_success, "llm": "qwen3.8:27b"}
    except Exception:
        return {"ok": False, "llm": "unavailable"}


def service_state(name: str) -> str:
    try:
        return subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=4).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def model_progress(path: str, expected: int) -> dict:
    size = Path(path).stat().st_size if Path(path).exists() else 0
    return {"downloaded": size, "expected": expected, "percent": min(100, round(size * 100 / expected)) if expected else 0}


@app.get("/api/project/status")
def project_status(request: Request):
    username = require_user(request)
    with closing(db()) as con:
        pending = con.execute("SELECT COUNT(*) FROM approval_requests WHERE username=? AND status='pending'", (username,)).fetchone()[0]
    return {"services": {"chat": service_state("ai-girlfriend.service"), "https": service_state("ai-girlfriend-https.service"), "language": service_state("qwen-transformers.service"), "voice": service_state("voice-model.service")}, "models": {"voice": model_progress("/data/models/voice/Qwen3-TTS-12Hz-1.7B-CustomVoice/model.safetensors", 3_833_402_552), "asr": model_progress("/data/models/voice/SenseVoiceSmall/model.pt", 936_000_000)}, "pending_approvals": pending}


@app.get("/api/project/approvals")
def project_approvals(request: Request):
    username = require_user(request)
    with closing(db()) as con:
        rows = con.execute("SELECT id,title,detail,status,created_at,decided_at FROM approval_requests WHERE username=? ORDER BY id DESC LIMIT 50", (username,)).fetchall()
    return {"approvals": [dict(row) for row in rows]}


@app.post("/api/project/approvals")
async def create_project_approval(request: Request, title: str = Form(...), detail: str = Form(...)):
    username = require_user(request)
    if not title.strip() or not detail.strip() or len(title) > 120 or len(detail) > 2000:
        raise HTTPException(400, "审批内容无效")
    with closing(db()) as con:
        cur = con.execute("INSERT INTO approval_requests (username,title,detail) VALUES (?,?,?)", (username, title.strip(), detail.strip()))
        con.commit()
    return {"id": cur.lastrowid, "status": "pending"}


@app.post("/api/project/approvals/{approval_id}")
async def decide_project_approval(approval_id: int, request: Request, decision: str = Form(...)):
    username = require_user(request)
    if decision not in {"approved", "rejected"}:
        raise HTTPException(400, "审批结果无效")
    with closing(db()) as con:
        cur = con.execute("UPDATE approval_requests SET status=?,decided_at=CURRENT_TIMESTAMP WHERE id=? AND username=? AND status='pending'", (decision, approval_id, username))
        con.commit()
    if not cur.rowcount:
        raise HTTPException(404, "待审批项目不存在或已处理")
    return {"ok": True, "status": decision}
