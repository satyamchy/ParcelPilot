# Architecture Note — ParcelPilot Support Agent

## Overview

An AI support agent for ParcelPilot, a B2B logistics platform. It answers
customer questions about cancellations, service credits, and policies by
reasoning over real policy documents, signed customer contracts, and
structured order/account/ticket data — while enforcing access control in
code, resolving conflicting sources by authority, and requiring explicit
human confirmation before any state-changing action. Built with FastAPI,
LangGraph, LangChain, Groq (LLM), and Chroma (vector store, local
embeddings).

## System flow

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

This is a single LangGraph state machine (`app/graph.py`). Every box is a
node; arrows are conditional edges chosen by the router's decision and the
synthesis result. The pause before `execute_action` is a real mechanism
(`interrupt_before`), not a prompt instruction — the graph physically
cannot execute a state-changing action until `/chat/confirm` explicitly
resumes it.

## Agent design

- **`router`** — one LLM call with structured output (Pydantic schema),
  deciding whether the query needs document search, structured data, or
  an action, and extracting any order/ticket ID mentioned. A code-level
  safety net force-enables structured-data lookup whenever an ID is
  present, rather than trusting the LLM's judgment alone.
- **`retrieve_docs` / `retrieve_data`** — call the tools (below).
- **`reconcile`** — only runs when 2+ distinct documents were retrieved;
  a second LLM call decides which source governs this specific question,
  using authority metadata attached to each chunk.
- **`synthesize`** — generates the answer; also emits `confidence` and
  `needs_escalation` as structured fields, not inferred from free text.
- **`prepare_action` → `execute_action`** — the confirm-before-action
  flow (see Trade-offs).

State persists per `thread_id` via a SQLite checkpointer, giving
multi-turn memory and making the interrupt/resume mechanism possible.
Every LLM decision point uses a Pydantic structured-output schema rather
than parsing free text.

### Example walkthrough: "Can Northstar cancel ORD-1001 without a fee?"

1. **`router`** extracts `order_id="ORD-1001"`; the code safety net force-
   enables `needs_structured_data` in addition to whatever the LLM decided
   for document search.
2. **`retrieve_docs`** runs `search_documents()` — Chroma search, results
   tagged with authority rank, other customers' contracts dropped before
   the LLM sees anything.
3. **`retrieve_data`** runs `get_order()` (raises `AccessDeniedError`
   before returning anything if the caller doesn't own the order), then
   `calculate_cancellation_fee()` — a plain Python function implementing
   the actual SOP rule with the Northstar contract override applied by
   account ID. The number in the final answer comes from this
   calculation, not from the LLM reading a percentage off a PDF.
4. **`reconcile`** (only since 2+ documents came back) decides the
   contract governs over the general SOP.
5. **`synthesize`** writes the answer from the calculation result and the
   reconciliation verdict, and sets `confidence`.
6. **`respond`** assembles the reply, appends the turn to persisted
   history.

No action was requested, so the graph ends here. If the user had said
"please escalate this" instead, the flow continues to `prepare_action` →
pause → `/chat/confirm`.

## Tool design

Three tools, kept as plain Python functions (not framework wrappers) so
they're independently unit-testable:

1. **`search_documents`** (`app/tools/documents.py`) — Chroma similarity
   search over the 6 PDFs, returns chunks with authority metadata
   attached and sorted most-authoritative-first, drops other customers'
   contracts.
2. **`get_order` / `get_ticket` / calculations** (`app/tools/data.py`) —
   SQLite lookups plus `calculate_cancellation_fee` and
   `calculate_service_credit`, implementing the actual SOP/contract
   arithmetic in code.
3. **`prepare_escalation` / `commit_action`** (`app/tools/actions.py`) —
   two-phase: preview (writes nothing) then commit (writes only if
   confirmed).

Every function touching account-scoped data calls
`check_account_access()` (`app/access_control.py`) before returning
anything — a hard Python exception, not a system-prompt instruction.

## Document and structured-data handling

- **PDFs**: chunked (~800 chars, 120 overlap), embedded locally via
  `sentence-transformers`, stored in Chroma. Each chunk is tagged at
  ingestion time with `doc_type`, `status`, `version`, `customer_id` —
  read from a hand-authored map (`DOCUMENT_METADATA` in `config.py`), not
  inferred from content.
- **Structured data**: the workbook loads one SQLite table per sheet. The
  README sheet's snapshot timestamp becomes the fixed reference "now" for
  every time-based calculation.
- Ingestion is split into two independent scripts
  (`app/ingestion/load_structured_data.py`,
  `app/ingestion/embed_documents.py`) so either can be re-run alone.

## Source reliability and conflict handling

- **Authority is static and explicit**: `DOC_AUTHORITY_RANK` ranks
  contract (0) above current SOP/policy (1) above deprecated policy (99).
  The system is never asked to infer "is this document trustworthy" —
  only "given these ranked sources, which one governs this question."
- **Numbers come from code, not text extraction**: fees and credits are
  computed by Python functions reading structured order fields, with each
  contract's override hardcoded by account ID. The LLM narrates the
  result; it doesn't compute or transcribe the figure.
- **Uncertainty is a first-class output**: when fault attribution is
  ambiguous, the calculation function returns `eligible=None,
  escalate=True` rather than the LLM being left to decide whether to
  guess.
- **Historical tickets are explicitly downgraded**: the synthesis prompt
  treats ticket resolution notes as context only, and flags (not
  silently repeats) any contradiction with current policy — the real data
  pack includes at least two tickets where the historical resolution
  actually is wrong.

## Major technical trade-offs

- **Groq + local embeddings** instead of one vendor for both — avoids a
  second paid API dependency, at the cost of a larger Docker image.
- **Deterministic calculations over LLM arithmetic** for anything
  involving money — slower to extend to a new contract clause, but
  eliminates "the model misread the percentage" failures.
- **LangGraph's native `interrupt_before`** over a simpler "ask twice"
  API pattern — a real, checkpointed pause that can't be bypassed by
  prompting, at the cost of needing to understand LangGraph's resume
  semantics.
- **Router-LLM tool selection + hardcoded safety nets** rather than pure
  LLM judgment or a fully rule-based router — costs some code complexity
  but catches cases where the LLM's routing judgment was simply wrong
  (observed directly during testing).
- **Fully deterministic proactive-detection logic** — no LLM in the
  detection itself, only in the final narrative phrasing. Less flexible
  than an LLM-driven "spot anything weird" approach, but every flagged
  issue is independently verifiable against a SQL query.

## File reference

```
main.py                                 FastAPI app entry point
app/config.py                           settings + DOC_AUTHORITY_RANK + DOCUMENT_METADATA
app/access_control.py                   Role, UserContext, check_account_access, check_action_permission
app/snapshot.py                         reads the dataset reference "now"
app/graph.py                            AgentState, schemas, prompts, nodes, edges, build_graph()
app/routes.py                           POST /chat, POST /chat/confirm, GET /internal/insights, GET /health
app/ingestion/load_structured_data.py   workbook -> SQLite + snapshot time (independent)
app/ingestion/embed_documents.py        PDFs -> Chroma (independent)
app/tools/documents.py                  Tool 1: document search/retrieval
app/tools/data.py                       Tool 2: structured data lookup + calculation
app/tools/actions.py                    Tool 3: escalation (mocked, preview + confirm)
app/tools/insights.py                   proactive issue detection (deterministic aggregation)
```

## Verification scenarios

Ten scenarios used to test the behaviors above, via `POST /chat` (swap
`message`, use a fresh `thread_id` per scenario, and `user` as noted):

1. **Contract override — free cancellation (Northstar)**
   > "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why."
   `role: customer, account_id: ACCT-001` → expect fee = ₹0, citing the
   Northstar Enterprise Agreement over the general SOP.

2. **Standard SOP applies — no waiver (LumenWorks)**
   > "I want to cancel an order I booked over an hour ago — will I be charged?"
   `role: customer, account_id: ACCT-002` → expect the standard ₹250 fee
   (past the 30-min grace), citing the SOP, no waiver mentioned.

3. **Contract override — fixed credit amount (LumenWorks)**
   > "My pickup was delayed and it was the carrier's fault — do I get a credit?"
   (a real LumenWorks order with a >4hr carrier-fault delay) → expect a
   fixed ₹300 credit citing the 4-hour threshold override, not the
   default 2-hour/percentage rule.

4. **Default SOP applies (no special contract)**
   Same style of question for `ACCT-003`/`ACCT-004` → expect the default
   2-hour threshold, credit = min(₹500, 10% of shipment fee).

5. **Ambiguous fault → escalate, don't guess**
   An order where fault fields are missing/unclear → expect
   `confidence: "uncertain"`, explicit statement that fault can't be
   determined, no fabricated yes/no.

6. **Cross-account access denial**
   Logged in as `ACCT-002`, ask about an `ACCT-001` order → expect
   refusal, even if worded as pretending to be an internal agent.

7. **Deprecated policy correctly ignored**
   > "What's the standard SLA for a P2 issue?"
   → expect the current policy's numbers, not the deprecated policy's
   different ones.

8. **Known-issue awareness**
   > "My SwiftShip order still shows as BOOKED even though the driver already picked it up — is something wrong?"
   → expect a reference to the known SwiftShip webhook delay (KI-211)
   rather than treating it as a new incident.

9. **Historical ticket contains wrong guidance — should not repeat it**
   A question matching a scenario where a past ticket's resolution
   contradicts current SOP/contract → expect the current correct answer,
   historical note treated as context only.

10. **Full action + confirmation flow**
    > "This isn't resolved — please escalate it to a human."
    → expect `pending_action` populated, nothing written yet. Then
    `POST /chat/confirm` with `confirmed: true` commits it; repeat with
    `confirmed: false` on a fresh case to confirm cancellation, not
    creation.
