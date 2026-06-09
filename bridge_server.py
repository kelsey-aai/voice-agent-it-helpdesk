# bridge_server.py
#
# The thin bridge between Twilio and the AssemblyAI Voice Agent API. It shuttles
# audio in both directions, and when the agent fires a tool call, runs the work
# and returns the result. Twilio streams G.711 μ-law at 8 kHz, which the Voice
# Agent API accepts natively when input/output encoding is `audio/pcmu` — no
# transcoding, lowest latency.
#
# Endpoint specifics:
#   - The endpoint is wss://agents.assemblyai.com/v1/ws
#   - Auth is `Authorization: Bearer YOUR_KEY` (the Bearer prefix is specific to
#     the Voice Agent API; the standalone Streaming API takes the raw key).
#   - The first message is a `session.update` event with everything nested under
#     a `session` object. There is no separate `session.start`.
#   - Wait for `session.ready` before sending any `input.audio` frames. Voice,
#     greeting, and output format are fixed once the session is ready.
import asyncio
import json
import os

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response

from prompts import SYSTEM_PROMPT, GREETING
from tools import HELPDESK_TOOLS
from tool_loop import flush_pending
from transfer import start_transfer

load_dotenv()

VOICE_AGENT_WS = "wss://agents.assemblyai.com/v1/ws"
ASSEMBLYAI_KEY = os.environ["ASSEMBLYAI_API_KEY"]

app = FastAPI()


@app.post("/twilio/voice")
async def twilio_voice(request: Request):
    host = request.url.hostname
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{host}/media-stream" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket):
    await twilio_ws.accept()
    stream_sid = {"value": None}
    call_sid = {"value": None}

    session_config = {
        "type": "session.update",
        "session": {
            "system_prompt": SYSTEM_PROMPT,
            "greeting": GREETING,
            "tools": HELPDESK_TOOLS,
            "input": {
                "format": {"encoding": "audio/pcmu"},
                # Boost the alphanumeric jargon a helpdesk hears constantly.
                "keyterms": ["Okta", "VPN", "MFA", "VLAN", "Kerberos",
                             "SSO", "ServiceNow", "Active Directory"],
                "turn_detection": {
                    "vad_threshold": 0.5,
                    "min_silence": 800,
                    "max_silence": 2500,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": "ivy", "format": {"encoding": "audio/pcmu"}},
        },
    }

    async with websockets.connect(
        VOICE_AGENT_WS,
        additional_headers={"Authorization": f"Bearer {ASSEMBLYAI_KEY}"},
    ) as va_ws:
        await va_ws.send(json.dumps(session_config))
        ready = asyncio.Event()
        state = {"last_type": None, "pending": [], "transferring": False}

        async def twilio_to_va():
            async for raw in twilio_ws.iter_text():
                event = json.loads(raw)
                kind = event.get("event")
                if kind == "start":
                    stream_sid["value"] = event["start"]["streamSid"]
                    call_sid["value"] = event["start"]["callSid"]
                elif kind == "media" and ready.is_set():
                    await va_ws.send(json.dumps({
                        "type": "input.audio",
                        "audio": event["media"]["payload"],
                    }))
                elif kind == "stop":
                    return

        async def va_to_twilio():
            async for raw in va_ws:
                event = json.loads(raw)
                t = event.get("type")
                state["last_type"] = t

                if t == "session.ready":
                    ready.set()
                elif t == "reply.audio" and stream_sid["value"]:
                    await twilio_ws.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid["value"],
                        "media": {"payload": event["data"]},
                    }))
                elif t == "input.speech.started":
                    state["pending"].clear()   # caller barged in; drop stale calls
                elif t == "tool.call":
                    if event["name"] == "escalate_to_human" and not state["transferring"]:
                        state["transferring"] = True
                        asyncio.create_task(
                            start_transfer(call_sid["value"], event.get("arguments", {}))
                        )
                    else:
                        state["pending"].append(event)
                elif t == "reply.done":
                    await flush_pending(va_ws, state["pending"], state["last_type"])

        await asyncio.gather(twilio_to_va(), va_to_twilio())


@app.get("/")
async def root():
    return {"ok": True, "service": "voice-agent-it-helpdesk"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        log_level="info",
    )
