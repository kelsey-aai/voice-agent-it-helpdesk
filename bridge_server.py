# bridge_server.py: Twilio on one side; speech-to-text, the LLM, and
# text-to-speech on the other. Phone audio stays 8 kHz mu-law end to end.
import asyncio
import base64
import json
import os
import string
import time
from urllib.parse import urlencode

import assemblyai as aai
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response

from agent_loop import respond
from prompts import GREETING, SYSTEM_PROMPT

load_dotenv()

ASSEMBLYAI_KEY = os.environ["ASSEMBLYAI_API_KEY"]

STT_URL = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(
    {
        "speech_model": "universal-3-6-pro",
        "encoding": "pcm_mulaw",  # Twilio's G.711 mu-law, sent as-is
        "sample_rate": 8000,
        # Boost the jargon a helpdesk hears all day.
        "keyterms_prompt": json.dumps(
            ["Okta", "VPN", "MFA", "VLAN", "Kerberos", "SSO", "ServiceNow",
             "Active Directory", "GlobalProtect"]
        ),
    }
)
STT_CHUNK_BYTES = 800  # 100 ms of 8 kHz mu-law (send 50-1000 ms per message)

TTS_CONFIG = aai.TTSConfig(
    voice_id="49c443a4-8a1d-5f7e-b70b-98bdbd9823c3",  # Audrey
    language="english",
    encoding="pcm_mulaw",  # what Twilio plays back
    sample_rate=8000,
)

BACKCHANNELS = frozenset(
    {"mhm", "mm", "uh", "um", "hmm", "ah", "oh", "okay", "ok", "right", "yeah", "yep"}
)
_STRIP = str.maketrans("", "", string.punctuation)

QUEUE_NUMBERS = {
    queue: os.environ.get(f"QUEUE_{queue.upper()}")
    for queue in ["service_desk", "identity_access", "network", "security", "on_call"]
}

app = FastAPI()


def is_interruption(text: str) -> bool:
    words = text.lower().translate(_STRIP).split()
    return len(words) >= 2 and not all(w in BACKCHANNELS for w in words)


async def start_transfer(call_sid, args: dict) -> None:
    """Redirect the live call to a human queue. The new TwiML replaces
    <Connect><Stream>, which ends the media stream and this agent session."""
    queue = args.get("queue", "service_desk")
    number = QUEUE_NUMBERS.get(queue) or os.environ.get("HUMAN_TRANSFER_NUMBER")
    # In production, push the summary to the technician's screen-pop or ticket.
    print(f"[transfer] queue={queue} summary={args.get('summary', '')!r}")

    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and token and call_sid and number):
        print("[transfer] mock mode: set Twilio credentials and a queue number to dial")
        return

    from twilio.rest import Client

    client = Client(sid, token)
    await asyncio.to_thread(
        client.calls(call_sid).update, twiml=f"<Response><Dial>{number}</Dial></Response>"
    )


class Call:
    def __init__(self, twilio_ws, stt_ws, tts, llm):
        self.twilio_ws = twilio_ws
        self.stt_ws = stt_ws
        self.tts = tts
        self.llm = llm
        self.stream_sid = None
        self.call_sid = None
        self.messages = [
            {
                "role": "system",
                "content": f'{SYSTEM_PROMPT}\nYou opened the call by saying: "{GREETING}"',
            }
        ]
        self.reply_task = None
        self.in_tts = False         # a TTS request is in flight
        self.playing_until = 0.0    # when Twilio finishes playing what we sent
        self.transferring = False

    @property
    def speaking(self) -> bool:
        return self.in_tts or time.monotonic() < self.playing_until

    async def say(self, text: str):
        print(f"Agent: {text}")
        # Tell the speech model what the agent just asked, so a short answer
        # (an employee ID, a ticket number, "yes") is heard in context.
        await self.stt_ws.send(
            json.dumps({"type": "UpdateConfiguration", "agent_context": text[-1750:]})
        )
        self.in_tts = True
        try:
            async for chunk in self.tts.speak(text):
                await self.twilio_ws.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": base64.b64encode(chunk).decode()},
                        }
                    )
                )
                # Pace the audio at the phone line's rate (8,000 bytes a
                # second), keeping about half a second queued at Twilio.
                now = time.monotonic()
                self.playing_until = max(self.playing_until, now) + len(chunk) / 8000
                ahead = self.playing_until - now - 0.5
                if ahead > 0:
                    await asyncio.sleep(ahead)
        finally:
            self.in_tts = False

    async def handle_turn(self, text: str):
        if self.messages[-1]["role"] == "user":
            self.messages[-1]["content"] += " " + text  # caller kept talking
        else:
            self.messages.append({"role": "user", "content": text})
        try:
            reply, escalation = await respond(self.llm, self.messages)
        except Exception as err:  # an LLM or network failure: recover out loud
            print(f"[llm] {err}")
            reply, escalation = "Sorry, I didn't catch that. Could you say it again?", None

        if escalation:
            self.transferring = True  # stop listening for new turns
        await self.say(reply)
        if escalation:
            # Let the hand-off line finish playing, then transfer.
            await asyncio.sleep(max(0.0, self.playing_until - time.monotonic()))
            await start_transfer(self.call_sid, escalation)

    async def barge_in(self):
        if self.in_tts:
            self.in_tts = False
            await self.tts.cancel()  # the server drops the rest of the reply
        if self.reply_task and not self.reply_task.done():
            self.reply_task.cancel()
        if time.monotonic() < self.playing_until:
            # Drop the audio Twilio has queued but not played yet.
            await self.twilio_ws.send_text(
                json.dumps({"event": "clear", "streamSid": self.stream_sid})
            )
            self.playing_until = 0.0

    def start_reply(self, coro):
        self.reply_task = asyncio.create_task(coro)

    async def on_turn(self, event: dict):
        text = event.get("transcript", "").strip()
        if not text or self.transferring:
            return
        if self.speaking and not is_interruption(text):
            return  # "mhm", "okay": the caller is listening, not interrupting
        await self.barge_in()
        if event.get("end_of_turn"):
            print(f"Caller: {text}")
            self.start_reply(self.handle_turn(text))


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
    async with websockets.connect(
        STT_URL, additional_headers={"Authorization": ASSEMBLYAI_KEY}
    ) as stt_ws, aai.AsyncTTSSession(
        TTS_CONFIG, api_key=ASSEMBLYAI_KEY
    ) as tts, aai.AsyncLLMGateway(api_key=ASSEMBLYAI_KEY) as llm:
        call = Call(twilio_ws, stt_ws, tts, llm)

        async def twilio_to_stt():
            buffer = bytearray()
            async for raw in twilio_ws.iter_text():
                event = json.loads(raw)
                kind = event.get("event")
                if kind == "start":
                    call.stream_sid = event["start"]["streamSid"]
                    call.call_sid = event["start"]["callSid"]
                    call.start_reply(call.say(GREETING))
                elif kind == "media":
                    # Twilio sends 20 ms frames; batch them into 100 ms.
                    buffer += base64.b64decode(event["media"]["payload"])
                    if len(buffer) >= STT_CHUNK_BYTES:
                        await stt_ws.send(bytes(buffer))
                        buffer.clear()
                elif kind == "stop":
                    return

        async def stt_to_agent():
            async for raw in stt_ws:
                event = json.loads(raw)
                if event.get("type") == "Turn":
                    await call.on_turn(event)

        async def keep_tts_alive():
            while True:  # a TTS session closes after two idle minutes
                await asyncio.sleep(60)
                if not call.speaking:
                    await tts.keep_alive()

        background = [asyncio.create_task(stt_to_agent()), asyncio.create_task(keep_tts_alive())]
        try:
            await twilio_to_stt()  # the call is over when Twilio stops streaming
        finally:
            for task in background:
                task.cancel()
            if call.reply_task:
                call.reply_task.cancel()
            try:
                await stt_ws.send(json.dumps({"type": "Terminate"}))
            except websockets.ConnectionClosed:
                pass


@app.get("/")
async def root():
    return {"ok": True, "service": "voice-agent-it-helpdesk"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
