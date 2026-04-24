# MOCA — My Only Capable Assistant

Fully autonomous AI presence. One brain, every device.

## Setup

1. Clone the repo
2. Create virtual environment: `python3 -m venv venv`
3. Activate: `source venv/bin/activate` (bash) or `source venv/bin/activate.fish` (fish shell)
4. Install dependencies: `pip install -r requirements.txt`
5. Copy `.env.example` to `.env` and fill in your real values
6. Run: `python server/main.py`

## Project Structure

```
server/          — MOCA brain server (FastAPI + Groq)
clients/mac/     — Mac client (Electron) — Phase 3
clients/android/ — Android client (React Native) — Phase 6
memory/          — Persistent memory storage (gitignored)
workspace/       — Temporary code execution (gitignored)
integrations/    — Self-written device integrations (gitignored)
```

## Environment Variables

Copy `.env.example` to `.env` and fill in:
- `GROQ_API_KEY` — from console.groq.com
- `GROQ_MODEL` — `groq/compound-mini` recommended for development
- `SERVER_PORT` — 8000 default

## Deployment

Brain runs on Oracle Cloud free tier ARM instance (Ubuntu 22.04).
Deploy workflow: push to GitHub on Mac, pull on Oracle, restart systemd service.
