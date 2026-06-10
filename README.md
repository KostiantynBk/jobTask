# Lead Processing MVP

Small Python MVP for processing landing-page lead submissions.

## What it does

1. Receives JSON form submissions at `POST /leads`
2. Parses and validates the JSON body
3. Normalizes common fields like `name`, `email`, `budget`, `services`, and `timeline`
4. Generates a short AI summary with OpenAI
5. Classifies the lead as `hot`, `warm`, or `cold`
6. Saves the raw payload, normalized payload, summary, score, and status to SQLite
7. Sends a Telegram notification when Telegram credentials are configured

The implementation uses FastAPI for the HTTP layer and SQLite for storage.

## Launch

```powershell
python -m pip install -r requirements.txt
$env:OPENAI_API_KEY = "sk-..."
uvicorn app:app --reload
```

The server starts at:

```text
http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000/docs` to test the API from the browser.

Required for AI summary generation:

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

Optional configuration:

```powershell
$env:LEADS_DB_PATH = "leads.db"
$env:OPENAI_MODEL = "gpt-4o-mini"
$env:TELEGRAM_BOT_TOKEN = "123456:bot-token"
$env:TELEGRAM_CHAT_ID = "123456789"
```

If `OPENAI_API_KEY` is not set, submissions fail because the summary step is explicitly AI-generated. If Telegram credentials are missing, notification is skipped and that status is saved with the lead.

## Test Payload

Example payloads are in `example_payload.json`. Open `/docs`, expand `POST /leads`, and paste one example object into the request body.

Inspect saved leads:

```powershell
python -c "import sqlite3; c=sqlite3.connect('leads.db'); print(c.execute('select id, name, email, classification, score, telegram_status from leads').fetchall())"
```

## Solution Logic

I kept this as a small MVP. There is one FastAPI endpoint that receives a lead, processes it, saves the result, and sends a notification.

I used FastAPI because it is quick to set up and provides `/docs` for easy testing. SQLite is used to show that data is persisted without requiring a separate database service for this task.

The data is normalized first: email is lowercased, budget values like `"$12,000"` are converted into numbers, and services can be handled as either a list or a comma-separated string. After normalization, the app calls OpenAI to generate a short summary of the lead.

For classification, I used a simple rule-based score instead of another AI call. It looks at budget, urgency, company info, message detail, and selected services. Based on the score, the lead is classified as `hot`, `warm`, or `cold`. This keeps the logic easy to understand and check.

Both the original JSON and the normalized version are saved in SQLite. This makes it clear what was submitted and what the system actually used for processing.

Telegram notification is sent at the end if the bot token and chat ID are set. If they are missing, the app saves that the notification was skipped instead of failing the whole flow.

The endpoint returns the lead ID, classification, score, AI summary, Telegram status, and normalized payload.
