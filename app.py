from __future__ import annotations

import json
import os
import sqlite3
import textwrap
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


DB_PATH = Path(os.getenv("LEADS_DB_PATH", "leads.db"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

app = FastAPI(title="Lead Processing MVP")


class LeadPayload(BaseModel):
    name: str
    email: str
    phone: str | None = None
    company: str | None = None
    role: str | None = None
    services: list[str] | str | None = None
    budget: int | float | str | None = None
    timeline: str | None = None
    source: str | None = None
    message: str | None = None


class NormalizedLead(BaseModel):
    name: str
    email: str
    phone: str | None = None
    company: str | None = None
    role: str | None = None
    message: str | None = None
    budget: int | None = None
    timeline: str | None = None
    source: str | None = None
    services: list[str]
    submitted_at: str


class LeadResponse(BaseModel):
    id: int
    classification: str
    score: int
    summary: str
    telegram_status: str
    normalized: NormalizedLead


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Lead Processing MVP. Open /docs or POST JSON to /leads."}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/leads", status_code=201, response_model=LeadResponse)
def create_lead(payload: LeadPayload) -> dict[str, Any]:
    try:
        return process_submission(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                company TEXT,
                budget INTEGER,
                timeline TEXT,
                source TEXT,
                score INTEGER NOT NULL,
                classification TEXT NOT NULL,
                ai_summary TEXT NOT NULL,
                raw_payload TEXT NOT NULL,
                normalized_payload TEXT NOT NULL,
                telegram_status TEXT NOT NULL
            )
            """
        )


def process_submission(payload: dict[str, Any]) -> dict[str, Any]:
    lead = normalize_lead(payload)
    summary = generate_ai_summary(lead)
    classification, score = classify_lead(lead)
    telegram_status = send_telegram_notification(lead, summary, classification, score)
    lead_id = save_lead(payload, lead, summary, classification, score, telegram_status)

    return {
        "id": lead_id,
        "classification": classification,
        "score": score,
        "summary": summary,
        "telegram_status": telegram_status,
        "normalized": lead,
    }


def normalize_lead(payload: dict[str, Any]) -> dict[str, Any]:
    name = first_present(payload, "name", "full_name", "fullName")
    email = first_present(payload, "email", "email_address", "emailAddress")

    if not name or not email:
        raise ValueError("Both 'name' and 'email' are required.")

    return {
        "name": str(name).strip(),
        "email": str(email).strip().lower(),
        "phone": clean_optional(first_present(payload, "phone", "phone_number", "phoneNumber")),
        "company": clean_optional(first_present(payload, "company", "organization")),
        "role": clean_optional(first_present(payload, "role", "title", "job_title")),
        "message": clean_optional(first_present(payload, "message", "notes", "description", "project")),
        "budget": normalize_budget(first_present(payload, "budget", "monthly_budget", "projectBudget")),
        "timeline": clean_optional(first_present(payload, "timeline", "start_date", "urgency")),
        "source": clean_optional(first_present(payload, "source", "utm_source", "referrer")),
        "services": normalize_services(first_present(payload, "services", "service", "interests")),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


def first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def clean_optional(value: Any) -> str | None:
    if value in (None, ""):
        return None
    value = str(value).strip()
    return value or None


def normalize_budget(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)

    digits = "".join(char for char in str(value) if char.isdigit())
    return int(digits) if digits else None


def normalize_services(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return [part.strip().lower() for part in str(value).split(",") if part.strip()]


def generate_ai_summary(lead: dict[str, Any]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required to generate the AI summary.")

    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Summarize this lead in one concise sentence for a sales operator.",
            },
            {"role": "user", "content": json.dumps(lead, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError) as exc:
        raise RuntimeError(f"AI summary generation failed: {exc}") from exc


def classify_lead(lead: dict[str, Any]) -> tuple[str, int]:
    score = 0

    if lead["budget"] and lead["budget"] >= 10000:
        score += 45
    elif lead["budget"] and lead["budget"] >= 3000:
        score += 25
    elif lead["budget"]:
        score += 10

    timeline = (lead["timeline"] or "").lower()
    if any(term in timeline for term in ("asap", "urgent", "now", "this month")):
        score += 25
    elif any(term in timeline for term in ("quarter", "month", "soon")):
        score += 15

    if lead["company"]:
        score += 15
    if lead["message"] and len(lead["message"]) >= 40:
        score += 10
    if lead["services"]:
        score += 5

    if score >= 70:
        return "hot", score
    if score >= 40:
        return "warm", score
    return "cold", score


def save_lead(
    raw_payload: dict[str, Any],
    lead: dict[str, Any],
    summary: str,
    classification: str,
    score: int,
    telegram_status: str,
) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO leads (
                created_at, name, email, company, budget, timeline, source, score,
                classification, ai_summary, raw_payload, normalized_payload, telegram_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead["submitted_at"],
                lead["name"],
                lead["email"],
                lead["company"],
                lead["budget"],
                lead["timeline"],
                lead["source"],
                score,
                classification,
                summary,
                json.dumps(raw_payload, ensure_ascii=False),
                json.dumps(lead, ensure_ascii=False),
                telegram_status,
            ),
        )
        return int(cursor.lastrowid)


def send_telegram_notification(lead: dict[str, Any], summary: str, classification: str, score: int) -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return "skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured"

    text = textwrap.dedent(
        f"""
        New lead: {lead['name']} <{lead['email']}>
        Classification: {classification} ({score})
        Company: {lead.get('company') or 'n/a'}
        Summary: {summary}
        """
    ).strip()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return "sent" if 200 <= response.status < 300 else f"failed: HTTP {response.status}"
    except urllib.error.URLError as exc:
        return f"failed: {exc.reason}"
