# AI Tool Usage

I used **Claude (Anthropic)** as my primary AI coding assistant along with Chat-GPT  throughout
this project, via its chat interface with code execution.

## How I used it

- **Architecture and scaffolding**: worked through the design (LangGraph
  state machine, the 3-tool split, access-control-in-the-tool-layer, the
  confirm-before-action pattern) conversationally before any code was
  written, then had it generate the initial FastAPI + LangGraph + LangChain
  scaffold against that design.
- **Adapting to the real data pack**: once I uploaded the actual PDFs and
  workbook, I had it read the real contract/SOP text directly and rewrite
  the hardcoded business-rule constants (cancellation grace periods, fee
  amounts, service-credit thresholds, contract-specific overrides) to
  match exactly what the real documents say, rather than the placeholder
  values from an earlier mock-data version.
- **Debugging via a real test loop**: I ran the ingestion scripts and the
  server myself, and fed back the actual output/tracebacks (a Windows page-
  file OSError, an access-control account-ID mismatch, a Chroma zero-
  results bug, a LangGraph state-leak bug across conversation turns) as
  they occurred. Each fix was diagnosed against the specific error I
  pasted, applied, and I re-ran it myself to confirm before moving on —
  this was an iterative loop, not a single generate-and-accept step.
- **Frontend**: had it build the React/Vite chat UI (including the
  insights dashboard for the proactive-detection bonus) to match the
  backend's actual API contract.
- **Documentation**: this note, the architecture note, the product note,
  and the README's setup/testing sections were drafted by the assistant
  and reviewed by me before inclusion.

## What I verified myself

I ran both ingestion scripts and the live server against the real data
pack, tested the example queries from the brief plus additional scenarios
covering access control, source-conflict resolution, and the
confirm/cancel action flow, and reported back exact request/response
payloads when behavior looked wrong — which is how the state-leak and
account-ID bugs above were actually caught, rather than being found in
review.
