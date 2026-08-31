# ParcelPilot Support Agent — Backend

FastAPI + LangGraph support agent for ParcelPilot. See
[`ARCHITECTURE_NOTE.md`](./ARCHITECTURE_NOTE.md) for how it works,
[`PRODUCT_NOTE.md`](./PRODUCT_NOTE.md) for product decisions and roadmap,
and [`AI_TOOL_USAGE.md`](./AI_TOOL_USAGE.md) for AI-assistance disclosure.

This file is setup and run instructions only.

---

## 1. Install

```bash
cd parcelpilot-backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure

```bash
cp .env.example .env
```
Set `GROQ_API_KEY` in `.env`. Everything else has a working default.

## 3. Add the data pack

```
data/
  pdfs/
    01_Support_Policy_v3_CURRENT.pdf
    02_Support_Policy_v2_DEPRECATED.pdf
    03_Cancellation_and_Service_Credit_SOP_v4.pdf
    04_Product_Operations_Guide_and_Known_Issues.pdf
    05_Northstar_Logistics_Enterprise_Agreement.pdf
    06_LumenWorks_Service_Agreement.pdf
  ParcelPilot_Assessment_Data.xlsx
```

## 4. Run ingestion

Two independent scripts — re-run only the one whose source data changed.

```bash
python -m app.ingestion.load_structured_data   # workbook -> SQLite + snapshot time
python -m app.ingestion.embed_documents         # PDFs -> Chroma (local embeddings)
```

**Windows note:** if `embed_documents` fails with
`OSError: The paging file is too small`, increase Windows virtual memory
(page file) size and restart.

## 5. Run the server

```bash
uvicorn main:app --reload
```
Interactive API docs: `http://localhost:8000/docs`

## 6. API quick reference

**`POST /chat`**
```json
{
  "message": "Can Northstar cancel ORD-1001 without a cancellation fee?",
  "thread_id": "demo-1",
  "user": {"user_id": "u1", "role": "customer", "account_id": "ACCT-001"}
}
```
`role`: `customer | internal_support | internal_admin`. `account_id`
required for `customer`. Reuse `thread_id` for multi-turn memory.

**`POST /chat/confirm`**
```json
{"thread_id": "demo-1", "confirmed": true}
```
Resumes a thread paused on a pending action.

**`GET /internal/insights?role=internal_support`**
Proactive issue detection (SLA breaches, recurring issues, order
anomalies) — internal roles only.

**`GET /health`**
Liveness check.

## 7. Run the frontend

```bash
cd ../parcelpilot-frontend
npm install
cp .env.example .env    # set VITE_API_URL to your backend URL
npm run dev
```
