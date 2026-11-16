"""AI English Learner — Backend (Phase 3: AI Learning Engine).

تشغيل محلي:
    cd backend
    python -m venv .venv && source .venv/bin/activate   # على ويندوز: .venv\\Scripts\\activate
    pip install -r requirements.txt
    cp .env.example .env     # ثم ضع مفتاح ANTHROPIC_API_KEY
    uvicorn main:app --reload --port 8000
"""

import json
import os
import re
from typing import Literal

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
Level = Literal["A1", "A2", "B1", "B2", "C1", "C2"]

app = FastAPI(title="AI English Learner API", version="0.3.0")

# تطوير محلي فقط: الإضافة تتصل من chrome-extension://...
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_client: AsyncAnthropic | None = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(500, "ANTHROPIC_API_KEY غير موجود في ملف .env")
        _client = AsyncAnthropic()
    return _client


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
    return {"status": "ok", "model": MODEL}


@app.post("/explain", response_model=ExplainResponse)
async def explain(req: ExplainRequest):
    user_msg = (
        f"Learner level: {req.level}. {LEVEL_GUIDE[req.level]}\n\n"
        f"Selected text: {req.text}\n"
        f"Surrounding context: {req.context or '(none)'}"
    )
    try:
        resp = await client().messages.create(
            model=MODEL,
            max_tokens=900,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except HTTPException:
        raise
    except Exception as e:  # أخطاء الشبكة/المفتاح/الحد
        raise HTTPException(502, f"فشل الاتصال بخدمة الذكاء الاصطناعي: {type(e).__name__}")

    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        return ExplainResponse(**extract_json(raw))
    except Exception:
        raise HTTPException(502, "ردّ النموذج لم يكن بصيغة صحيحة، حاول مرة أخرى.")
