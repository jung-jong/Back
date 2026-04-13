# Custom-TA Backend

FastAPI + SQLAlchemy 2.0 async backend for the Custom-TA RAG teaching assistant.

## Project Structure

The codebase is split into presentation, domain/business, dependency, database, and shared core layers.

```text
Back/
|-- webapp/
|   |-- main.py
|   `-- routers/
|       |-- auth.py
|       |-- courses.py
|       |-- documents.py
|       |-- chat.py
|       |-- enrollments.py
|       |-- quests.py
|       |-- interventions.py
|       |-- course_messages.py
|       `-- dashboard.py
|-- src/
|   |-- ai/
|   |-- analytics/
|   |-- auth/
|   |-- courses/
|   |-- enrollments/
|   |-- documents/
|   |-- chat/
|   |-- quests/
|   |-- interventions/
|   |-- course_messages/
|   |-- dashboard/
|   `-- models/
|-- dependencies/
|-- database/
`-- core/
```

`webapp` is the FastAPI presentation layer. `src` contains domain schemas, SQLAlchemy models, and business services. `dependencies`, `database`, and `core` are infrastructure/shared layers.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Put the real TiDB password in `.env`, then run:

```powershell
python run_server.py
```

On Windows, start the app through `run_server.py` instead of `python -m uvicorn`.
This passes a Selector event-loop factory to uvicorn, which avoids Windows
Proactor loop connection errors against TiDB.

## Implemented API

The deployed API follows the frontend contract directly, without an `/api/v1`
prefix.

- `POST /auth/login`
- `POST /auth/signup`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /courses/me`
- `GET /courses/{course_id}`
- `POST /courses`
- `POST /courses/join`
- `DELETE /courses/{course_id}`
- `GET /courses/{course_id}/files`
- `POST /courses/{course_id}/files`
- `PATCH /courses/{course_id}/files/{file_id}`
- `PATCH /courses/{course_id}/files/{file_id}/publish`
- `DELETE /courses/{course_id}/files/{file_id}`
- `GET /courses/{course_id}/chat`
- `POST /courses/{course_id}/chat`
- `POST /courses/{course_id}/chat/stream`
- `GET /courses/{course_id}/quests`
- `GET /courses/{course_id}/quests/{quest_id}/content`
- `POST /courses/{course_id}/quests`
- `PUT /courses/{course_id}/quests/{quest_id}`
- `POST /courses/{course_id}/quests/{quest_id}/send`
- `DELETE /courses/{course_id}/quests/{quest_id}`
- `POST /courses/{course_id}/quests/{quest_id}/submit`
- `GET /courses/{course_id}/analytics`
- `GET /courses/{course_id}/analytics/keywords`
- `GET /courses/{course_id}/ai-proposals`
- `GET /courses/{course_id}/ai-config`
- `PUT /courses/{course_id}/ai-config`
- `GET /courses/{course_id}/me/stats`
- `GET /courses/{course_id}/me/weak-points`
- `GET /courses/{course_id}/notifications`
- `PATCH /courses/{course_id}/notifications/{notification_id}/read`
- `PATCH /courses/{course_id}/notifications/read-all`

`POST /courses/{course_id}/chat` also updates `course_keyword_stats` so the
instructor dashboard can render weekly question keyword trends.

## Weekly AI Suggestions

Weekly instructor suggestions can run automatically inside the FastAPI process.

```env
WEEKLY_INTERVENTION_SCHEDULER_ENABLED=true
WEEKLY_INTERVENTION_INTERVAL_SECONDS=3600
WEEKLY_INTERVENTION_RUN_ON_STARTUP=false
```

The scheduler can create up to three `ai_interventions` rows per course and week range: `SEND_QUEST`, `SEND_MESSAGE`, and `UPLOAD_MATERIAL`. Each category is judged independently from weekly chat keywords, question rate, quiz participation, wrong-answer rate, weak concepts, and material readiness.
