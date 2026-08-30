# ParcelPilot Frontend

Minimal chat UI for the ParcelPilot support agent. Talks to the backend
purely over HTTP — no other coupling.

## Local dev

```bash
npm install
cp .env.example .env
# edit .env: VITE_API_URL=http://127.0.0.1:8000  (or your deployed backend URL)
npm run dev
```

## Build for deployment

```bash
npm run build
```
Outputs static files to `dist/` — deployable to Vercel, Netlify, GitHub
Pages, or any static host.

## What it does

- Role/account selector at top (mocks login — no real auth)
- Chat window, newest message at bottom
- Under each assistant reply: a strip of "trace chips" showing which
  tool(s) fired and what they returned, plus a collapsible source list
- When the agent wants to escalate, a confirmation card appears with
  Confirm/Cancel buttons — nothing is sent to the backend until you click
  one
- "New conversation" generates a fresh `thread_id`, starting a clean
  conversation with no memory of the old one
