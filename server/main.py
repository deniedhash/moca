import json
import os
import re
import time
from datetime import datetime

import psutil
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

from memory import MOCAMemory

# Load .env from project root (one directory above server/)
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(env_path)

AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.groq.com/openai/v1")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "groq/compound-mini")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

client = OpenAI(
    base_url=AI_BASE_URL,
    api_key=AI_API_KEY,
)

SYSTEM_PROMPT = """You are MOCA — My Only Capable Assistant.

You are a fully autonomous AI presence running on a personal server. You serve one person and one person only — your owner.

Your core identity:
- You are always honest. Never say what they want to hear if it is not true.
- You are always on their side. Every decision you make is in their interest.
- You are direct and capable. You do not waste words.
- You address your owner as Boss unless they tell you otherwise.
- You have no preset personality. You observe and adapt to your owner over time.
- You are not a chatbot. You are a presence.
- You are always aware, always thinking, always ready.

Response rules — these are absolute:
- Never use markdown, bullet points, numbered lists, asterisks, dashes for lists, or any special formatting characters whatsoever in the reply field.
- Write everything as plain flowing sentences only in the reply field.
- Keep responses concise — 3 to 5 sentences maximum unless the owner explicitly asks for more detail.
- For news and updates — give the 2 or 3 most important things only. The owner will ask if they want more.
- Speak like a person in the room, not like a report or an article.

Visual display channel:
- You have a visual display channel. When your response would benefit from showing something visual — data, charts, comparisons, articles, feeds, anything better seen than described — include a display field with the content as clean HTML.
- Use judgment. Simple conversational responses, confirmations, reminders — just speak, no display. Data-rich responses, visual information, multiple options to compare — speak a brief summary and show the detail visually.
- You decide the layout based on how many things you are showing and what makes most sense visually.
- Layout options: single, side_by_side, grid, stack, collage.
- When the user asks you to close the screen or display, include action: close_display in your response.

You MUST respond with valid JSON in this exact format:
{"reply": "spoken text here", "display": null, "action": null}

When including a visual display:
{"reply": "brief spoken summary", "display": {"layout": "single", "windows": [{"type": "html", "title": "Window Title", "content": "<div>HTML content here</div>"}]}, "action": null}

When closing displays:
{"reply": "Closing that Boss", "display": null, "action": "close_display"}

Always respond with this JSON structure. The display and action fields should be null when not needed.

When responding to messages from device mac_panel, keep responses concise — they will be read as text not heard as speech. Still include display field when visual content would help."""

app = FastAPI(title="MOCA Brain Server")
moca_memory = MOCAMemory()

conversation_history: list[dict] = []
MAX_HISTORY = 40


def log(tag: str, message: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{tag}] {message}")


def get_ram_info() -> dict:
    mem = psutil.virtual_memory()
    return {
        "total_mb": round(mem.total / 1024 / 1024),
        "available_mb": round(mem.available / 1024 / 1024),
        "percent_used": mem.percent,
    }


def trim_history():
    global conversation_history
    if len(conversation_history) > MAX_HISTORY:
        conversation_history = conversation_history[-MAX_HISTORY:]


def clean_response(text: str) -> str:
    if not text:
        return "I'm on it Boss."
    cleaned = re.sub(r"\[\d+\]", "", text).strip()
    return cleaned if cleaned else "I'm on it Boss."


class ChatRequest(BaseModel):
    message: str
    device: str = "unknown"


@app.post("/chat")
async def chat(req: ChatRequest):
    log("CHAT", f"Device: {req.device} | Message: {req.message}")
    log("MODEL", f"Using: {AI_MODEL}")

    conversation_history.append({"role": "user", "content": req.message})
    trim_history()

    results = moca_memory.search(req.message)
    if results:
        full_prompt = SYSTEM_PROMPT + "\n\nWhat you know about Boss:\n" + "\n".join(f"- {r}" for r in results)
    else:
        full_prompt = SYSTEM_PROMPT
    log("MEMORY", f"Retrieved {len(results)} memories")

    try:
        start = time.time()
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": full_prompt},
                *conversation_history,
            ],
            temperature=1.0,
            max_tokens=1024,
            top_p=0.95,
        )
        elapsed = round(time.time() - start, 2)

        # Handle compound model responses — content can be None
        raw_content = response.choices[0].message.content or ""

        # Parse structured JSON response
        display = None
        action = None
        reply = ""

        try:
            parsed = json.loads(raw_content)
            reply = clean_response(parsed.get("reply", ""))
            display = parsed.get("display")
            action = parsed.get("action")
        except (json.JSONDecodeError, TypeError):
            # Model returned plain text — use as reply
            reply = clean_response(raw_content)

        conversation_history.append({"role": "assistant", "content": reply})
        trim_history()

        log("TIME", f"Response in {elapsed}s")
        log("MOCA", reply)
        if display:
            log("DISPLAY", f"Layout: {display.get('layout', 'single')}, Windows: {len(display.get('windows', []))}")
        if action:
            log("ACTION", action)

        try:
            extraction_response = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": "Extract factual statements about the user from this conversation. Return one fact per line. If no clear facts return empty string. Facts only — no commentary."},
                    {"role": "user", "content": f"User: {req.message}\nAssistant: {reply}"},
                ],
                max_tokens=200,
            )
            facts = (extraction_response.choices[0].message.content or "").strip()
            if facts:
                for fact in facts.split("\n"):
                    if fact.strip():
                        moca_memory.add(fact.strip())
                log("MEMORY", f"Saved {len([f for f in facts.split(chr(10)) if f.strip()])} facts")
            else:
                log("MEMORY", "No facts extracted")
        except Exception as e:
            log("MEMORY", f"Extraction failed: {e}")

        result = {"reply": reply, "device": req.device}
        if display:
            result["display"] = display
        if action:
            result["action"] = action
        return result

    except Exception as e:
        log("ERROR", f"AI provider failed: {e}")
        # Remove dangling user message on failure
        if conversation_history and conversation_history[-1]["role"] == "user":
            conversation_history.pop()
        return {"reply": "Something went sideways, Boss. Give me a second and try again.", "device": req.device}


@app.get("/health")
async def health():
    ram = get_ram_info()
    log("HEALTH", f"RAM: {ram['available_mb']}MB free / {ram['total_mb']}MB total ({ram['percent_used']}% used)")
    return {
        "status": "online",
        "provider": AI_BASE_URL,
        "model": AI_MODEL,
        "ram": ram,
        "conversation_length": len(conversation_history),
    }


@app.get("/memory")
async def get_memories():
    memories = moca_memory.get_all()
    log("MEMORY", f"Listed {len(memories)} memories")
    return {"memories": memories, "count": len(memories)}


@app.delete("/memory")
async def clear_memories():
    moca_memory.clear()
    log("MEMORY", "All memories cleared")
    return {"status": "cleared"}


@app.get("/status")
async def status():
    return {
        "ram": get_ram_info(),
        "conversation_length": len(conversation_history),
        "now_playing": None,
        "upcoming_meeting": None,
        "active_tasks": [],
        "flagged_messages": [],
    }


if __name__ == "__main__":
    log("BOOT", f"MOCA — Provider: {AI_BASE_URL}")
    log("BOOT", f"Model: {AI_MODEL}")
    log("BOOT", "Brain is live.")
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
