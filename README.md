# IT helpdesk voice agent — AssemblyAI Voice Agent API

A runnable IT helpdesk / technical-support voice agent built on the [AssemblyAI Voice Agent API](https://www.assemblyai.com/products/voice-agent-api). It does the four things a real tier-1 support line needs:

- **Routes** the caller by issue type (access, network, hardware, software, security)
- **Answers** known questions from your knowledge base (`search_knowledge_base`)
- **Acts** in your ITSM system — opens and checks tickets (`create_ticket`, `check_ticket_status`)
- **Escalates** to a human queue with a written summary when an issue needs one (`escalate_to_human`)

The whole conversation loop — speech-to-text, the LLM, text-to-speech, turn detection, and tool calling — runs over a **single WebSocket** to `wss://agents.assemblyai.com/v1/ws`. Twilio Media Streams pipe the phone audio in and out. The bridge is thin; the agent is four tools plus a routing system prompt.

This is the companion repo for the tutorial: [How to build a voice agent for IT helpdesk and technical support](https://www.assemblyai.com/blog/build-voice-agent-for-it-helpdesk-technical-support).

> **Why speech-to-text accuracy is the whole game here.** Helpdesk speech is dense with alphanumerics — ticket numbers like `INC0012345`, error codes like `0x80070005`, asset tags, VLAN numbers. One wrong character means the agent looks up the wrong ticket or files a useless one. The Voice Agent API runs [Universal-3 Pro Streaming](https://www.assemblyai.com/universal-3-pro-streaming), which is tuned for exactly this: 21% fewer alphanumeric errors and 28% better accuracy on consecutive numbers than the previous generation.

## Architecture

```
  Caller ──PSTN──> Twilio number
                      │
                      │  <Connect><Stream> (bidirectional, audio/pcmu)
                      ▼
              bridge_server.py  ──ws──> Voice Agent API
                      │                  STT (Universal-3 Pro) + LLM + TTS
                      │                  + turn detection + tool calling
                      │                          │
                      │        tool.call ────────┤
                      │   ┌──────────────────────┼───────────────────────┐
                      │   ▼               ▼               ▼               ▼
                      │ search_kb    create_ticket   check_status   escalate_to_human
                      │ (interactive) (interactive)  (interactive)   (hold → transfer)
                      │   │               │               │               │
                      │   └──── tool.result (JSON) ───────┘          terminal: no result,
                      │         returned on reply.done                hand call to a queue
                      ▼
                Your KB + ITSM (ServiceNow / Jira SM / Zendesk)
```

## What's in the repo

| File | What it does |
|---|---|
| `bridge_server.py` | FastAPI + WebSocket bridge: Twilio ⇄ Voice Agent API, plus the tool-call routing |
| `tools.py` | The four tool definitions (JSON Schema), passed in `session.tools` |
| `tool_loop.py` | Dispatches `tool.call` → handler, returns `tool.result` at the `reply.done` boundary |
| `prompts.py` | The routing system prompt and the spoken greeting |
| `integrations.py` | **Mock** KB + ITSM (in-memory) so it runs out of the box; production `httpx` versions included for swapping |
| `transfer.py` | `escalate_to_human` hand-off — redirects the live Twilio call to a human queue |
| `byok_stt.py` | The bring-your-own-key alternative: Universal-3 Pro Streaming as the STT leg under your own LLM/TTS |

About 500 lines of Python total. The KB and ITSM are **mocked in memory** by default — clone it and hear the agent work without standing up ServiceNow first. Two tickets are pre-seeded (`INC0019001`, `INC0019044`) so "what's the status of my ticket" works in a demo.

## Prerequisites

- Python 3.11+
- An [AssemblyAI account](https://www.assemblyai.com/dashboard/signup) with Voice Agent API access
- A [Twilio account](https://www.twilio.com/try-twilio) and a voice-capable phone number
- [ngrok](https://ngrok.com/download) or any public HTTPS tunnel — Twilio needs to reach your local server

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/kelsey-aai/voice-agent-it-helpdesk
cd voice-agent-it-helpdesk

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — at minimum, drop in your AssemblyAI API key.
```

`ASSEMBLYAI_API_KEY` is all you need to run with the mock KB/ITSM. The Twilio
transfer numbers and real KB/ITSM URLs are optional (see below).

### 3. Start the server

```bash
uvicorn bridge_server:app --host 0.0.0.0 --port 8000
```

### 4. Expose it and point Twilio at it

```bash
ngrok http 8000
```

In the Twilio console, set your number's **"A call comes in"** webhook to:

```
POST https://<your-ngrok-subdomain>.ngrok.app/twilio/voice
```

Call the number and try:

- *"How do I set up the VPN?"* → `search_knowledge_base` (network)
- *"My laptop won't connect, can you open a ticket? My employee ID is 4471."* → `create_ticket`, then the agent reads the new ticket number back digit by digit
- *"What's the status of ticket I-N-C zero zero one nine zero zero one?"* → `check_ticket_status` → "in progress"
- *"I think I clicked a phishing link."* → `escalate_to_human` straight to the `security` queue, no troubleshooting
- *"Just get me a person."* → `escalate_to_human` to `service_desk`

## Swapping the mocks for a real backend

`integrations.py` ships with in-memory mocks so the repo runs immediately. When
you're ready to wire a real KB and ITSM:

1. Open `integrations.py`. The production `httpx` versions of `kb_search`,
   `itsm_create_ticket`, and `itsm_get_ticket` are at the bottom of the file.
2. Replace the mock bodies with them (the returned dict shapes are identical, so
   nothing else changes).
3. Set `KB_SEARCH_URL`, `ITSM_API_URL`, and `ITSM_TOKEN` in `.env`.

The Voice Agent API doesn't integrate with a specific ITSM — it just calls
whatever tool you define, so ServiceNow, Jira Service Management, and Zendesk are
all a matter of pointing these handlers at the right REST endpoints. Tickets are
tagged `channel: "voice_agent"` so you can measure containment later.

For the `escalate_to_human` transfer, set `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, and either `HUMAN_TRANSFER_NUMBER` or the per-queue numbers
in `.env`. Without them the escalation runs in mock mode and logs the hand-off.

## Two security boundaries worth keeping

These live in `prompts.py` and matter more than tone:

- **Never collect credentials by voice.** The agent never asks for a password,
  PIN, or MFA code. It triggers a self-service reset link or escalates to a
  verified human check.
- **Never fabricate.** The agent answers from knowledge-base snippets or says it
  doesn't know and opens a ticket — it does not invent ticket numbers, error-code
  meanings, or fixes.

## Bring-your-own-key (BYOK) alternative

Need a specific fine-tuned LLM, a cloned brand voice, or an existing orchestrator
like LiveKit or Pipecat? Run [Universal-3 Pro Streaming](https://www.assemblyai.com/docs/streaming-stt/universal-3-pro/overview)
(`u3-rt-pro`) as the transcription leg under your own stack. `byok_stt.py` shows
the standalone connection. Note the auth difference: the Streaming API takes the
**raw** API key in the `Authorization` header, while the Voice Agent API takes
`Bearer YOUR_KEY`.

## Docs

- [Voice Agent API quickstart](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/quickstart)
- [Session configuration](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/session-configuration)
- [Tool calling](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/tool-calling)
- [WebSocket events reference](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/events-reference)
- [Connect to Twilio](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/connect-to-twilio)
