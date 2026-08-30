# ParcelPilot Support Agent — Backend

FastAPI + LangGraph agent answering ParcelPilot customer support questions
over policies, contracts, and order/ticket data, with source-authority
awareness, access control, and confirm-before-action escalations.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env and set GROQ_API_KEY
```

## Add your data

Drop the real data pack into `data/`:

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

If your PDF filenames differ from the ones above, update the
`DOCUMENT_METADATA` map in `app/config.py` so each file's authority/status/
customer scope is explicit — this is intentionally hand-authored, not
inferred by the LLM.

If your workbook's account/order/ticket columns use different names than
`account_id`, `order_id`, `scheduled_pickup`, `actual_pickup`, `delay_cause`,
adjust the queries in `app/tools/data.py` to match.

## Ingest

Two independent steps — run either alone when only that part of the data changes:

```bash
# 1. Load accounts/orders/tickets into SQLite (only touches the workbook)
python -m app.ingestion.load_structured_data

# 2. Build embeddings from the PDFs (only touches data/pdfs/)
python -m app.ingestion.embed_documents
```

`load_structured_data` writes `data/db/parcelpilot.db` and
`data/db/snapshot_time.txt` (the reference "now" for time-based questions,
read from the README sheet). `embed_documents` writes `data/db/chroma/`
using local sentence-transformers embeddings — no API key needed for this
step, only `GROQ_API_KEY` for the LLM itself.

Re-run whichever one changed — e.g. if you only edit a PDF, you don't need
to reload the workbook.

## Run

```bash
uvicorn main:app --reload
```

## API

### `POST /chat`
```json
{
  "message": "Can Northstar cancel ORD-1001 without a cancellation fee?",
  "thread_id": "demo-thread-1",
  "user": {"user_id": "u1", "role": "customer", "account_id": "northstar"}
}
```
Returns `{reply, tool_trace, pending_action, cited_sources, confidence}`.
`pending_action` is non-null when the agent wants to escalate and is
waiting for confirmation.

### `POST /chat/confirm`
```json
{"thread_id": "demo-thread-1", "confirmed": true}
```
Confirms or cancels the pending action on that thread.

`role` is one of `customer` | `internal_support` | `internal_admin`.
`account_id` is required for `customer` and ignored for internal roles.
`thread_id` is any string you choose per conversation — it's what lets
multi-turn context and the confirm step resume the right conversation.

## Structure

```
main.py              FastAPI app
app/
  config.py           settings + document authority map
  access_control.py   the single enforcement point for data scoping
  snapshot.py          dataset "now" reference time
  graph.py              state, schemas, prompts, nodes, LangGraph build
  routes.py             /chat and /chat/confirm
  ingestion/
    load_structured_data.py   workbook -> SQLite (independent, run alone)
    embed_documents.py         PDFs -> Chroma (independent, run alone)
  tools/
    documents.py        Tool 1: document search/retrieval
    data.py              Tool 2: structured data lookup + calculations
    actions.py           Tool 3: escalation (mocked, preview + confirm)
```
