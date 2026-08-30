# ParcelPilot Support Agent — Backend

An AI support agent for ParcelPilot, a B2B logistics platform. It answers
customer questions about cancellations, service credits, and policies by
reasoning over real policy documents, signed customer contracts, and
structured order/account/ticket data — while enforcing access control in
code, resolving conflicting sources by authority, and requiring explicit
human confirmation before any state-changing action.

Built with **FastAPI**, **LangGraph**, **LangChain**, **Groq** (LLM), and
**Chroma** (vector store, local embeddings — no extra API key needed).

---

## 1. What this project actually does (end-to-end)

### The problem
ParcelPilot's support team fields hundreds of questions a week that require
cross-referencing: a general support policy, a cancellation/credit SOP, a
customer's specific signed contract (which can override the general
policy), and live order/ticket data — while some of those documents are
outdated and some historical ticket resolutions are simply wrong. A
support agent (human or AI) that confidently answers from the wrong source
is worse than one that admits uncertainty.

### The architecture, in one picture

```
                         ┌─────────────┐
   user message  ──────► │   router    │  (LLM, structured output)
                         └──────┬──────┘
                                │  decides: needs docs? needs data? wants action?
                 ┌──────────────┼──────────────┐
                 ▼                              ▼
       ┌──────────────────┐          ┌──────────────────────┐
       │  retrieve_docs    │          │   retrieve_data       │
       │  (Chroma search)  │          │  (SQLite + calc)      │
       └─────────┬─────────┘          └───────────┬──────────┘
                 └──────────────┬───────────────────┘
                                 ▼
                        ┌─────────────────┐
                        │    reconcile     │  (LLM — only if 2+ sources conflict)
                        └────────┬─────────┘
                                 ▼
                        ┌─────────────────┐
                        │   synthesize     │  (LLM, structured output:
                        └────────┬─────────┘   answer + confidence + escalate?)
                                 │
                    ┌────────────┴────────────┐
                    ▼                          ▼
          (confident / no action)     (user asked for an action)
                    │                          ▼
                    │                 ┌──────────────────┐
                    │                 │  prepare_action    │ (builds preview,
                    │                 └────────┬───────────┘  writes nothing)
                    │                          ▼
                    │                 ⏸  GRAPH PAUSES HERE  ⏸
                    │                  (interrupt_before)
                    │                          │
                    │                 ── user clicks Confirm/Cancel ──
                    │                          ▼
                    │                 ┌──────────────────┐
                    │                 │  execute_action    │ (writes only if confirmed)
                    │                 └────────┬───────────┘
                    └────────────┬─────────────┘
                                 ▼
                         ┌──────────────┐
                         │   respond     │
                         └──────────────┘
```

This is a single **LangGraph** state machine (`app/graph.py`). Every box is
a node; the arrows are conditional edges chosen by the router's decision
and the synthesis result. The pause before `execute_action` is a real
mechanism (LangGraph's `interrupt_before`), not a prompt instruction — the
graph physically cannot execute a state-changing action until the
`/chat/confirm` endpoint explicitly resumes it.

### Walkthrough: "Can Northstar cancel ORD-1001 without a cancellation fee?"

1. **`router`** (LLM call #1, structured output): reads the message,
   extracts `order_id="ORD-1001"`, decides `needs_document_search=True`
   (need the policy/contract) and `needs_structured_data=True` (need the
   order itself). A safety net in code also force-enables structured-data
   lookup whenever an order/ticket ID is present, regardless of what the
   LLM decided — this isn't left to chance.
2. **`retrieve_docs`**: calls `search_documents()` (Tool 1), which does a
   Chroma similarity search, tags every result with its authority rank
   (contract > current SOP/policy > deprecated policy), and — critically —
   drops any other customer's contract from the results before they ever
   reach the LLM.
3. **`retrieve_data`**: calls `get_order()` (Tool 2), which raises
   `AccessDeniedError` before returning anything if the requesting
   customer doesn't own that order. Assuming access is fine, it then runs
   `calculate_cancellation_fee()` — a plain Python function implementing
   the actual SOP rule (30-min grace, ₹250 fee) with the Northstar
   contract override (free, always) applied by account ID. The number in
   the final answer comes from this calculation, not from the LLM "reading"
   a percentage off a PDF.
4. **`reconcile`** (LLM call #2, structured output, only runs if 2+
   distinct documents came back): given the contract and the SOP together,
   decides which one governs. Rule baked into the prompt: a signed
   contract overrides general policy on any term it explicitly addresses.
5. **`synthesize`** (LLM call #3, structured output): writes the actual
   answer, using the calculation result and the reconciliation verdict —
   not free-associating. Also decides `confidence` and whether to
   escalate.
6. **`respond`**: assembles the final reply text, appends this turn to
   conversation history (persisted via the LangGraph checkpointer so the
   next message in the same `thread_id` has full context).

No action was requested here, so the graph ends. If the user had instead
said "please escalate this," the flow would continue to `prepare_action`
→ pause → wait for `/chat/confirm`.

### Why source conflicts don't get guessed at
`DOCUMENT_METADATA` in `app/config.py` is a **hand-authored** map from
filename → `{doc_type, status, customer_id}`. This is deliberate: asking
the LLM to infer "is this policy current or deprecated" from the document
text is exactly the kind of confident-but-wrong behavior the assessment
brief warns against. The authority hierarchy (`DOC_AUTHORITY_RANK`) is
equally explicit: contract=0, current SOP/policy=1, deprecated=99. The
`reconcile` node only has to decide *which* authoritative source applies
to *this specific question* — it never has to guess *which document is
more trustworthy in general*.

### Why access control can't be "argued around"
Every function in `app/tools/data.py` and `app/tools/documents.py` that
touches account-scoped data calls `check_account_access()`
(`app/access_control.py`) before returning anything. This is a hard Python
exception (`AccessDeniedError`), raised from inside the tool itself — not
a system-prompt instruction like "don't show other accounts' data" that a
sufficiently persuasive user message could talk the model out of.

### Why actions need a real confirmation step
`prepare_escalation()` (Tool 3) only ever builds and returns a preview
object — it writes nothing to the database. The actual write happens in
`commit_action()`, which is only reachable through the `execute_action`
graph node, which the graph is compiled to pause before
(`interrupt_before=["execute_action"]`). The pause is enforced by
LangGraph's checkpointer, not by the LLM choosing to ask nicely — even if
a prompt-injected document told the agent to "just do it," the graph
mechanically cannot proceed past that node without a `/chat/confirm` call.

---

## 2. Setup

### 2.1 Install dependencies
```bash
cd parcelpilot-backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2.2 Configure environment
```bash
cp .env.example .env
```
Set `GROQ_API_KEY` in `.env`. Everything else has a working default.

### 2.3 Add the data pack
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

---

## 3. Run the ingestion tools

Two independent scripts — re-run only the one whose source data changed.

```bash
python -m app.ingestion.load_structured_data   # workbook -> SQLite + snapshot time
python -m app.ingestion.embed_documents         # PDFs -> Chroma (local embeddings)
```

**Windows note:** if `embed_documents` fails with
`OSError: The paging file is too small`, increase your Windows virtual
memory (page file) size and restart — this is a system memory-mapping
limit for the embedding model load, not a code issue.

---

## 4. Run the server

```bash
uvicorn main:app --reload
```
API docs: `http://localhost:8000/docs`

---

## 5. API

### `POST /chat`
```json
{
  "message": "Can Northstar cancel ORD-1001 without a cancellation fee?",
  "thread_id": "demo-1",
  "user": {"user_id": "u1", "role": "customer", "account_id": "ACCT-001"}
}
```
`role` is one of `customer | internal_support | internal_admin`.
`account_id` is required for `customer`, ignored for internal roles. Reuse
`thread_id` across a conversation for multi-turn memory; a new one starts
fresh.

Response:
```json
{
  "reply": "...",
  "tool_trace": [{"tool": "...", "input_summary": "...", "output_summary": "..."}],
  "pending_action": null,
  "cited_sources": ["..."],
  "confidence": "confident"
}
```
`pending_action` is non-null exactly when the graph has paused awaiting
confirmation.

### `POST /chat/confirm`
```json
{"thread_id": "demo-1", "confirmed": true}
```
Resumes the paused thread. `confirmed: false` cancels the action instead
of executing it.

### `GET /health`
Liveness check.

---

## 6. What each file does

```
main.py
```
FastAPI app entry point — creates the app, adds CORS, mounts routes.

```
app/config.py
```
Settings (from `.env`) plus two hand-authored constants: `DOC_AUTHORITY_RANK`
(contract > current policy > deprecated policy) and `DOCUMENT_METADATA`
(filename -> doc_type/status/customer_id). Static, never LLM-inferred.

```
app/access_control.py
```
The single enforcement point for data scoping: `Role`, `UserContext`,
`check_account_access` (raises if a customer requests another account's
data), `check_action_permission` (gates which roles may perform which
actions).

```
app/snapshot.py
```
Reads `data/db/snapshot_time.txt` — the reference "now" for every
time-based calculation, so answers don't depend on the actual wall-clock
date.

```
app/graph.py
```
The agent: `AgentState`, structured-output schemas (`RouterDecision`,
`ReconciliationResult`, `AnswerSynthesis`), prompts, all graph nodes, edges,
and `build_graph()` (compiles with a SQLite checkpointer and
`interrupt_before=["execute_action"]`).

```
app/routes.py
```
`POST /chat`, `POST /chat/confirm`, `GET /health`.

```
app/ingestion/load_structured_data.py
```
Workbook -> SQLite + snapshot time. Independent of PDFs/Chroma.

```
app/ingestion/embed_documents.py
```
PDFs -> Chroma. Independent of the workbook/SQLite.

```
app/tools/documents.py — Tool 1: document search/retrieval
```
Chroma similarity search; drops other customers' contracts from results;
sorts by authority rank; ignores an invalid LLM-guessed `doc_type` filter
instead of silently returning zero results.

```
app/tools/data.py — Tool 2: structured data lookup + calculation
```
Access-gated order/ticket/account lookups, plus `calculate_delay_hours`,
`calculate_service_credit`, and `calculate_cancellation_fee` — implementing
the actual SOP rules and both customer contract overrides in code, not
left for the LLM to compute from retrieved text.

```
app/tools/actions.py — Tool 3: escalation (mocked)
```
`prepare_escalation()` (preview only, writes nothing) and
`commit_action()` (writes only if `confirmed=True`).

---

## 7. Test questions (10 scenarios covering different behaviors)

Use the same request shape as `/chat` above, swapping `message`,
`thread_id` (use a fresh one per scenario), and `user`.

**1. Contract override — free cancellation (Northstar)**
> "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why."
> `role: customer, account_id: ACCT-001`
Expect: fee = ₹0, citing the Northstar Enterprise Agreement over the
general SOP.

**2. Standard SOP applies — no waiver (LumenWorks)**
> "I want to cancel an order I booked over an hour ago — will I be charged?"
> `role: customer, account_id: ACCT-002`
Expect: standard ₹250 fee (past the 30-min grace), citing the SOP, no
contract waiver mentioned.

**3. Contract override — fixed credit amount (LumenWorks)**
> "My pickup was delayed and it was the carrier's fault — do I get a credit?"
(use a real LumenWorks order ID with a >4hr carrier-fault delay from your
workbook)
Expect: fixed ₹300 credit, explicitly citing the 4-hour threshold override
— not the default 2-hour/percentage rule.

**4. Default SOP applies (no special contract)**
> Same style of question, but for Beacon Retail or Axis Labs (`ACCT-003`/`ACCT-004`)
Expect: default 2-hour threshold, credit = min(₹500, 10% of shipment fee).

**5. Ambiguous fault → escalate, don't guess**
> Ask about an order where `carrier_fault`/`customer_fault` are missing or
> unclear in the data
Expect: `confidence: "uncertain"`, explicit statement that fault can't be
determined, offer to escalate — no fabricated yes/no.

**6. Cross-account access denial**
> Logged in as `ACCT-002`, ask about an order that belongs to `ACCT-001`
Expect: refusal to disclose the other account's data — this should be
enforced even if you word the request as pretending to be an internal
agent.

**7. Deprecated policy correctly ignored**
> "What's the standard SLA for a P2 issue?"
Expect: current policy's numbers (2 hours), not the deprecated policy's
different numbers — and ideally an explicit note that the older policy
isn't being used.

**8. Known-issue awareness**
> "My SwiftShip order still shows as BOOKED even though the driver already
> picked it up — is something wrong?"
Expect: reference to the known SwiftShip webhook delay (KI-211) rather
than treating it as a new incident.

**9. Historical ticket contains wrong guidance — should not repeat it**
> Ask a question matching a scenario where a past ticket's resolution
> notes contradict the current SOP/contract (see `tickets` sheet)
Expect: the current correct answer, with the historical note treated as
context only — not repeated as fact.

**10. Full action + confirmation flow**
> "This isn't resolved — please escalate it to a human."
Expect: `pending_action` populated in the response, nothing written yet.
Then call `/chat/confirm` with `confirmed: true` → the escalation commits.
Repeat with `confirmed: false` on a fresh case → confirm it's cancelled,
not created.

---

## 8. Known limitations (be upfront about these in your product note)
- Northstar's ₹5,000/month aggregate service-credit cap is *mentioned* in
  responses but not actually tracked against cumulative credits issued.
- The router's document/data-need detection is LLM-judgment-based with a
  couple of hardcoded safety nets (forcing data lookup when an ID is
  present) — it isn't exhaustively rule-based, so unusual phrasings could
  still occasionally under- or over-trigger retrieval.
- No proactive issue detection (the other optional bonus problem) was
  built — Trust/Reliability was the chosen focus per the assessment's
  "pick your priority" framing.



## Project Structure

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
