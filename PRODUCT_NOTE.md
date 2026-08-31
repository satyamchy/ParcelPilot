# Product Note — ParcelPilot Support Agent

## Additional client problem(s) addressed

Both were built, but with deliberately different levels of investment:

**Trust & Reliability (primary focus)** — this is threaded through the
entire architecture, not bolted on: the authority-ranked source
reconciliation, code-computed (not LLM-computed) financial figures, an
explicit uncertainty/escalation path instead of forced answers, and a
mechanically-enforced confirmation step before any state change. This got
the majority of the design effort because a support agent that's fast but
occasionally confidently wrong is worse than a slower one that knows what
it doesn't know — reliability is the precondition for the product being
usable at all, not a nice-to-have on top of it.

**Proactive Issue Detection (secondary, deliberately lightweight)** —
`GET /internal/insights` surfaces SLA breaches/at-risk tickets, recurring
known-issue clusters, and carrier-level order anomalies. Built as pure
SQL-style aggregation with zero LLM involvement in the detection logic
itself (an LLM only phrases the already-computed findings as a short
narrative) — consistent with the trust principle above: an internal ops
tool that says "there's a spike" needs that to be a verifiable count, not
a model's impression.

## What else I'd build next, prioritized

1. **Real scheduling + alerting for proactive detection** — today it's
   pull-based (an operator opens the Insights tab). The actual product
   value is push-based: a scheduled job that runs the same detection
   functions hourly and posts to Slack/email when something crosses a
   threshold. This matters most because the whole point of "proactive" is
   not requiring someone to remember to check.
2. **The other two action types** — only `create_escalation` is
   implemented; `update_ticket` and `create_follow_up_task` from the
   brief's example list aren't. Same two-phase preview/confirm pattern
   would extend directly.
3. **Northstar's monthly aggregate credit cap** — currently mentioned as
   a caveat in responses but not actually tracked against cumulative
   credits issued. Needed before this could be trusted for real
   approvals.
4. **A real business-hours calendar** for SLA math, replacing the current
   flat-hour approximation of "business day"/"business hours" — matters
   more as ticket volume grows and the approximation error compounds.
5. **An audit view of every committed action** — right now confirmed
   escalations land in a `mock_actions` table with no UI to review them;
   for a real trust story, a support lead should be able to see everything
   the agent actually did, not just what it said.
6. **A feedback loop** — letting a human agent flag a wrong answer, feeding
   that back into the reconciliation logic or the document metadata
   (e.g. "this contract clause needs a higher-priority tag") rather than
   the system repeating the same mistake.

## What I intentionally left out

- **Real authentication** — role/account context is passed directly in
  the request body; there's no login, JWT, or session. Explicitly allowed
  by the brief ("mock authentication... as appropriate").
- **Two of the three example action types** (see above) — only escalation
  is wired up; the other two would be mechanical repeats of the same
  pattern and didn't add new design value to demonstrate.
- **Automated test suite** — validated manually against the 10 scenarios
  in the README plus targeted unit tests on the calculation functions;
  no CI pipeline or regression suite.
- **Persistent, browsable conversation history** — the LangGraph
  checkpointer keeps state per thread for the interrupt/resume mechanism,
  but there's no UI to browse past conversations beyond the current
  session.
- **Production security hardening** — CORS is wide open, no rate
  limiting, no request logging/observability beyond the tool trace
  returned to the client.

## One metric I'd use to judge usefulness

**Confident-answer accuracy**: of all responses the system gave with
`confidence="confident"` (i.e., it didn't escalate), what percentage does
a human auditor later confirm were actually correct?

I'd track this over resolution rate (what % of queries got answered
without escalation) *specifically because* resolution rate alone can be
gamed by the system just guessing more — a support tool that resolves 95%
of queries but is wrong 10% of the time is worse than one that resolves
70% correctly and escalates the rest. Confident-answer accuracy forces the
system's confidence calibration itself to be the thing under measurement,
which is exactly what the brief's "Trust and Reliability" concern is
about. Resolution rate is still worth tracking alongside it, but as a
secondary metric — a system with high confident-answer accuracy and low
resolution rate has room to expand; the reverse is a trust problem, not a
coverage problem.
