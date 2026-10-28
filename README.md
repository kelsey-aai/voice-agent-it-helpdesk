# IT helpdesk voice agent: Universal-3.6 Pro Realtime + LLM Gateway + AssemblyAI TTS

A runnable IT helpdesk / technical-support voice agent for the Northwind service desk. It does the four things a real tier-1 support line needs:

- **Routes** the caller by issue type (access, network, hardware, software, security)
- **Answers** known questions from your knowledge base (`search_knowledge_base`)
- **Acts** in your ITSM system: opens and checks tickets (`create_ticket`, `check_ticket_status`)
- **Escalates** to a human queue with a written summary when an issue needs one (`escalate_to_human`)

It runs on three AssemblyAI APIs that share one API key:

1. [Universal-3.6 Pro Realtime](https://www.assemblyai.com/docs/streaming/getting-started/transcribe-streaming-audio) streaming speech-to-text (`speech_model=universal-3-6-pro`), which also tells you when the caller has finished a turn
2. [LLM Gateway](https://www.assemblyai.com/docs/llm-gateway/tool-calling) for the model that routes the call and calls your tools
3. The [AssemblyAI Text-to-Speech API](https://www.assemblyai.com/docs/tts/getting-started/quickstart) for the agent's voice

Twilio Media Streams carry the phone audio in and out at 8 kHz mu-law in both directions, so nothing is transcoded.

This is the companion repo for the tutorial: [How to build a voice agent for IT helpdesk and technical support](https://www.assemblyai.com/blog/build-voice-agent-for-it-helpdesk-technical-support).

> **Why speech-to-text accuracy matters most here.** Helpdesk speech is dense with alphanumerics: ticket numbers like `INC0012345`, error codes like `0x80070005`, asset tags, VLAN numbers. One wrong character means the agent looks up the wrong ticket or files a useless one. Universal-3.6 Pro Realtime posts a 14.4% entity error rate on [AssemblyAI's English voice-agent benchmark](https://www.assemblyai.com/blog/universal-3-6-pro-realtime-research), and 10.0% on codes and IDs.

## Architecture

```
Caller (phone)
   |
Twilio  <Connect><Stream>: 8 kHz mu-law audio in both directions
   |
bridge_server.py (FastAPI)
   |-- Universal-3.6 Pro Realtime   wss://streaming.assemblyai.com/v3/ws
   |      caller audio in -> Turn events out (end_of_turn = the caller finished)
   |
   |-- LLM Gateway                  https://llm-gateway.assemblyai.com/v1
   |      conversation + 4 tools in -> reply text or tool calls out
   |         search_knowledge_base              -> your knowledge base
   |         create_ticket, check_ticket_status -> your ITSM
   |         escalate_to_human                  -> Twilio transfer (ends the session)
   |
   |-- AssemblyAI Text-to-Speech    wss://tts.us.assemblyai.com/v1/ws
          reply text in -> 8 kHz mu-law audio out, back through Twilio
```

## What's in the repo

| File | What it does |
|---|---|
| `bridge_server.py` | FastAPI bridge: Twilio media stream ⇄ Universal-3.6 Pro Realtime, LLM Gateway, and AssemblyAI TTS. Handles turns, barge-in, and the `escalate_to_human` transfer |
| `agent_loop.py` | `respond()`: one assistant turn. Sends the conversation and tools to the LLM, runs the tools it calls, loops until it answers in words |
| `tools.py` | The four tool definitions, in the OpenAI function format LLM Gateway accepts |
| `prompts.py` | The routing system prompt and the spoken greeting |
| `integrations.py` | **Mock** KB + ITSM (in-memory) so it runs out of the box; production `httpx` versions included at the bottom for swapping |

The KB and ITSM are **mocked in memory** by default, so you can clone it and hear the agent work without standing up ServiceNow first. Two tickets are pre-seeded (`INC0019001`, `INC0019044`) so "what's the status of my ticket" works in a demo.

## Prerequisites

- Python 3.11+
- An [AssemblyAI account](https://www.assemblyai.com/dashboard/signup) and API key (the same key covers speech-to-text, LLM Gateway, and text-to-speech)
- A [Twilio account](https://www.twilio.com/try-twilio) and a voice-capable phone number
- [ngrok](https://ngrok.com/download) or any public HTTPS tunnel, so Twilio can reach your local server

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/kelsey-aai/voice-agent-it-helpdesk
cd voice-agent-it-helpdesk

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Text-to-speech is in the AssemblyAI Python SDK from version 1.8.2.

### 2. Configure

```bash
cp .env.example .env
# Edit .env: at minimum, set ASSEMBLYAI_API_KEY.
```

`ASSEMBLYAI_API_KEY` is all you need to run with the mock KB/ITSM. The Twilio transfer settings and real KB/ITSM URLs are optional (see below).

### 3. Start the server

```bash
python bridge_server.py
```

It listens on port 8000 (set `PORT` to change it).

### 4. Expose it and point Twilio at it

```bash
ngrok http 8000
```

In the Twilio console, set your number's **"A call comes in"** webhook to:

```
POST https://<your-ngrok-subdomain>.ngrok.app/twilio/voice
```

Twilio fetches the TwiML, opens the media stream to `/media-stream`, and the agent answers with its greeting. Call the number and try:

- *"How do I set up the VPN?"* → `search_knowledge_base` (network)
- *"My laptop won't connect, can you open a ticket? My employee ID is 4471."* → `create_ticket`, then the agent reads the new ticket number back digit by digit
- *"What's the status of ticket I-N-C zero zero one nine zero zero one?"* → `check_ticket_status`
- *"I think I clicked a phishing link."* → `escalate_to_human` straight to the `security` queue, no troubleshooting
- *"Just get me a person."* → `escalate_to_human` to `service_desk`

## How a turn works

- **Speech-to-text** connects to `wss://streaming.assemblyai.com/v3/ws` with `speech_model=universal-3-6-pro`, `encoding=pcm_mulaw` and `sample_rate=8000`. Twilio's 20 ms frames are batched into 100 ms chunks. A `keyterms_prompt` list boosts helpdesk jargon (Okta, VLAN, Kerberos, ...).
- **Turn detection** comes from the speech model: a `Turn` event with `end_of_turn` set sends the transcript to `respond()`.
- **The LLM** gets the conversation plus the four tools on every request. The three data tools run inside `respond()`; `escalate_to_human` is terminal, so `respond()` returns its arguments and the bridge speaks a hand-off line and transfers the call.
- **Text-to-speech** opens one `AsyncTTSSession` per call with `encoding="pcm_mulaw"` and `sample_rate=8000` (the native rate is 24 kHz; the server resamples). The default voice is Audrey (`49c443a4-8a1d-5f7e-b70b-98bdbd9823c3`).
- **Barge-in**: when the caller speaks over the agent (and it's more than a backchannel like "mhm"), the bridge calls `cancel()` on the TTS session, cancels the reply task, and sends Twilio a `clear` message.
- Before each reply, the agent's own words go to the speech model as `agent_context`, so a short answer like an employee ID is heard in context.

## Swapping in your own LLM, voice, or framework

**A different LLM.** Change `LLM_MODEL` in `agent_loop.py` to any tool-capable model on LLM Gateway's [available models](https://www.assemblyai.com/docs/llm-gateway/available-models) list. To call a provider directly, any async client with an OpenAI-style `chat.completions.create()` works with `respond()` as written:

```python
# In bridge_server.py: any client with an OpenAI-style chat.completions.create()
# works with respond() unchanged. For example, OpenAI directly:
import os

from openai import AsyncOpenAI

import agent_loop

agent_loop.LLM_MODEL = "gpt-4.1-mini"
llm = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ...then build the call with it instead of the gateway:
# call = Call(twilio_ws, stt_ws, tts, llm)
```

(That needs `pip install openai` and an `OPENAI_API_KEY`.)

**A different voice.** Change `voice_id` in `TTS_CONFIG` (in `bridge_server.py`) to another voice from the [voice library](https://www.assemblyai.com/docs/tts/voices), or clone your brand's voice in the dashboard and use its ID. [Voice cloning](https://www.assemblyai.com/docs/tts/cloned-voices) covers the consent requirements.

**An orchestration framework.** LiveKit and Pipecat both have AssemblyAI integrations for speech-to-text and text-to-speech: see the [LiveKit](https://www.assemblyai.com/docs/tts/livekit) and [Pipecat](https://www.assemblyai.com/docs/tts/pipecat) guides. Your tools, prompt, and integrations carry over unchanged.

## Swapping the mocks for a real backend

`integrations.py` ships with in-memory mocks so the repo runs immediately. When you're ready to wire a real KB and ITSM:

1. Open `integrations.py`. The production `httpx` versions of `kb_search`, `itsm_create_ticket`, and `itsm_get_ticket` are commented out at the bottom of the file.
2. Replace the mock bodies with them (the returned dict shapes are identical, so nothing else changes).
3. Set `KB_SEARCH_URL`, `ITSM_API_URL`, and `ITSM_TOKEN` in `.env`.

The agent only ever calls the functions you define, so ServiceNow, Jira Service Management, and Zendesk are all a matter of pointing these handlers at the right REST endpoints. Tickets are tagged `channel: "voice_agent"` so you can measure containment later.

For the `escalate_to_human` transfer, set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and either `HUMAN_TRANSFER_NUMBER` or the per-queue numbers in `.env`. Without them the escalation runs in mock mode and logs the hand-off.

## Two security boundaries worth keeping

These live in `prompts.py` and matter more than tone:

- **Never collect credentials by voice.** The agent never asks for a password, PIN, or MFA code. It triggers a self-service reset link or escalates to a verified human check.
- **Never fabricate.** The agent answers from knowledge-base snippets or says it doesn't know and opens a ticket. It does not invent ticket numbers, error-code meanings, or fixes.

## Docs

- [Streaming speech-to-text quickstart](https://www.assemblyai.com/docs/streaming/getting-started/transcribe-streaming-audio)
- [Universal-3.5 Pro streaming for voice agents](https://www.assemblyai.com/docs/voice-agents/universal-3-5-pro-streaming-api) (turn detection and backchannel guidance; this repo uses `universal-3-6-pro`)
- [LLM Gateway tool calling](https://www.assemblyai.com/docs/llm-gateway/tool-calling)
- [Text-to-Speech quickstart](https://www.assemblyai.com/docs/tts/getting-started/quickstart)
- [Text-to-Speech voices](https://www.assemblyai.com/docs/tts/voices)
