"""AI English Learner — Backend (Phase 3: AI Learning Engine).

تشغيل محلي:
    cd backend
    python -m venv .venv && source .venv/bin/activate   # على ويندوز: .venv\\Scripts\\activate
    pip install -r requirements.txt
    cp .env.example .env     # ثم ضع المفتاح (GEMINI_API_KEY أو ANTHROPIC_API_KEY)
    uvicorn main:app --reload --port 8000

المزوّد يُختار من .env عبر AI_PROVIDER = gemini (الافتراضي، له طبقة مجانية) أو claude.
"""

import json
import os
import re
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

PROVIDER = os.getenv("AI_PROVIDER", "gemini").strip().lower()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MODEL = GEMINI_MODEL if PROVIDER == "gemini" else CLAUDE_MODEL
Level = Literal["A1", "A2", "B1", "B2", "C1", "C2"]

app = FastAPI(title="AI English Learner API", version="0.3.0")

# تطوير محلي فقط: الإضافة تتصل من chrome-extension://...
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_claude_client = None


async def call_claude(system: str, user: str) -> str:
    global _claude_client
    if _claude_client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(500, "ANTHROPIC_API_KEY غير موجود في ملف .env")
        from anthropic import AsyncAnthropic  # مستورد هنا حتى لا يلزم إلا لمن يستعمل Claude

        _claude_client = AsyncAnthropic()
    resp = await _claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=900,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


async def call_gemini(system: str, user: str) -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(500, "GEMINI_API_KEY غير موجود في ملف .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 1200},
    }
    async with httpx.AsyncClient(timeout=40) as http:
        r = await http.post(url, headers={"x-goog-api-key": key}, json=body)

    if r.status_code == 429:
        raise HTTPException(429, "تجاوزت الحد المجاني للطلبات. انتظر قليلاً ثم حاول مرة أخرى.")
    if r.status_code in (400, 401, 403):
        raise HTTPException(502, "رفض Gemini الطلب: تحقق من GEMINI_API_KEY في ملف .env.")
    if r.status_code == 404:
        raise HTTPException(502, f"النموذج «{GEMINI_MODEL}» غير موجود. غيّر GEMINI_MODEL في ملف .env.")
    if r.status_code != 200:
        raise HTTPException(502, f"خطأ من Gemini ({r.status_code}).")

    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, ValueError):
        raise HTTPException(502, "لم يرجع Gemini ردّاً (ربما حُجب المحتوى). جرّب نصاً آخر.")
    return "".join(p.get("text", "") for p in parts)


async def generate(system: str, user: str) -> str:
    if PROVIDER == "gemini":
        return await call_gemini(system, user)
    if PROVIDER == "claude":
        return await call_claude(system, user)
    raise HTTPException(500, "AI_PROVIDER يجب أن يكون gemini أو claude")


class ExplainRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="الكلمة أو الجملة المحددة")
    context: str = Field("", max_length=1500, description="الجملة/الفقرة المحيطة بالنص")
    level: Level = "B1"


class Example(BaseModel):
    sentence: str
    note: str = ""


class ExplainResponse(BaseModel):
    kind: Literal["word", "phrase", "idiom", "sentence"]
    meaning: str          # المعنى في هذا السياق تحديداً
    explanation: str      # شرح بإنجليزية مناسبة للمستوى
    examples: list[Example]
    idiom_note: str = ""  # يُملأ فقط إن كان تعبيراً اصطلاحياً
    visual_hint: str = "" # وصف مشهد بسيط (للمرحلة 4: الصورة)


LEVEL_GUIDE = {
    "A1": "Use only very simple words and present-tense sentences of at most 8 words.",
    "A2": "Use simple everyday vocabulary and short sentences.",
    "B1": "Use clear, common vocabulary; short paragraphs are fine.",
    "B2": "Use natural English; you may mention nuance and register.",
    "C1": "Use rich English; discuss nuance, connotation and collocations.",
    "C2": "Use sophisticated English; discuss subtle connotation, register and etymology if useful.",
}

SYSTEM_PROMPT = """You are an English tutor inside a browser extension. A learner selected \
some text on a web page. Explain it for their CEFR level, using the surrounding context \
to decide the meaning that applies HERE (not every dictionary sense).

Rules:
- Write everything in English. Do NOT translate word-for-word into another language; \
explain through simpler English and real situations.
- "kind": "word", "phrase", "idiom" (meaning differs from the literal words) or "sentence".
- "meaning": one short line, the meaning in this context.
- "explanation": 2-4 sentences at the learner's level, written AT that level.
- "examples": exactly 2 realistic, everyday example sentences at that level, each with a \
short "note" saying when/why you would say it.
- "idiom_note": if kind is "idiom", say what the literal words suggest and what it really \
means; otherwise "".
- "visual_hint": one short sentence describing a simple scene that would illustrate the \
meaning (no text in the picture).
- If the selected text is not English, set kind to "sentence" and explain briefly in English \
that it is not English.

Return ONLY a JSON object with exactly these keys: kind, meaning, explanation, examples \
(list of {sentence, note}), idiom_note, visual_hint. No markdown, no extra text."""


def extract_json(raw: str) -> dict:
    """يستخرج JSON من رد النموذج حتى لو لُفّ بـ ```json أو سبقه نص."""
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start : end + 1]
    return json.loads(raw)


@app.get("/health")
async def health():
    return {"status": "ok", "provider": PROVIDER, "model": MODEL}


@app.get("/models")
async def list_models():
    """يعرض نماذج Gemini المتاحة لمفتاحك (لاختيار GEMINI_MODEL). لا يُظهر المفتاح."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(500, "GEMINI_API_KEY غير موجود في ملف .env")
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
            params={"pageSize": 200},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"تعذّر جلب النماذج ({r.status_code}). تحقق من المفتاح.")
    names = [
        m["name"].removeprefix("models/")
        for m in r.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    return {"current": GEMINI_MODEL, "available": names}


GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=400, description="وصف المشهد (visual_hint)")


class ImageResponse(BaseModel):
    mime: str
    data: str  # base64


@app.post("/image", response_model=ImageResponse)
async def image(req: ImageRequest):
    """يولّد صورة بسيطة توضّح المعنى (المرحلة 4). تعمل مع Gemini فقط."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(500, "GEMINI_API_KEY غير موجود في ملف .env")
    prompt = (
        "A simple, clear, friendly illustration for an English learner. "
        "No text, letters or words anywhere in the picture. Scene: " + req.prompt
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.post(url, headers={"x-goog-api-key": key}, json=body)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"فشل الاتصال بخدمة الصور: {type(e).__name__}")

    if r.status_code == 429:
        raise HTTPException(429, "تجاوزت الحد المجاني لتوليد الصور. حاول لاحقاً.")
    if r.status_code == 404:
        raise HTTPException(502, f"نموذج الصور «{GEMINI_IMAGE_MODEL}» غير متاح. غيّر GEMINI_IMAGE_MODEL في .env.")
    if r.status_code != 200:
        # رسالة Google الأصلية تساعد في التشخيص (لا تحتوي المفتاح)
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        raise HTTPException(502, f"خطأ من خدمة الصور ({r.status_code}). {detail[:200]}")

    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, ValueError):
        raise HTTPException(502, "لم تُرجع الخدمة صورة (ربما حُجب الوصف). جرّب نصاً آخر.")
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            return ImageResponse(mime=inline.get("mimeType") or inline.get("mime_type") or "image/png", data=inline["data"])
    raise HTTPException(502, "لم تُرجع الخدمة صورة. جرّب مرة أخرى.")


@app.post("/explain", response_model=ExplainResponse)
async def explain(req: ExplainRequest):
    user_msg = (
        f"Learner level: {req.level}. {LEVEL_GUIDE[req.level]}\n\n"
        f"Selected text: {req.text}\n"
        f"Surrounding context: {req.context or '(none)'}"
    )
    try:
        raw = await generate(SYSTEM_PROMPT, user_msg)
    except HTTPException:
        raise
    except Exception as e:  # أخطاء الشبكة/المهلة
        raise HTTPException(502, f"فشل الاتصال بخدمة الذكاء الاصطناعي: {type(e).__name__}")

    try:
        return ExplainResponse(**extract_json(raw))
    except Exception:
        raise HTTPException(502, "ردّ النموذج لم يكن بصيغة صحيحة، حاول مرة أخرى.")
