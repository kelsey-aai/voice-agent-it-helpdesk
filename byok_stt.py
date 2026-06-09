# byok_stt.py — the transcription leg of a bring-your-own-key (BYOK) stack.
#
# Use this when you need a specific LLM or TTS voice, or you already run an
# orchestrator like LiveKit or Pipecat. A composed stack is a chain — STT, then
# LLM, then TTS — and its accuracy ceiling is set by the first link. If
# transcription mishears INC0012345 as INC0012845, no downstream LLM or voice
# can recover. So the speech-to-text layer is the foundation, not a commodity.
#
# Auth difference, because it's the most common reason a copy-pasted snippet
# returns a 401: the standalone Streaming API takes the RAW API key in the
# Authorization header, while the Voice Agent API takes `Bearer YOUR_KEY`.
import asyncio
import json
import os
from urllib.parse import urlencode

import websockets
from dotenv import load_dotenv

load_dotenv()

ASSEMBLYAI_KEY = os.environ["ASSEMBLYAI_API_KEY"]
STREAMING_WS = "wss://streaming.assemblyai.com/v3/ws"


async def transcribe_leg(pcm16_audio_source, on_final_transcript):
    params = urlencode({
        "speech_model": "u3-rt-pro",   # Universal-3 Pro Streaming; no default — required
        "sample_rate": 8000,           # match the telephony source; don't upsample
        "format_turns": "true",
    })
    async with websockets.connect(
        f"{STREAMING_WS}?{params}",
        additional_headers={"Authorization": ASSEMBLYAI_KEY},  # RAW key, no "Bearer"
    ) as aai_ws:

        async def send_audio():
            async for pcm16_chunk in pcm16_audio_source:
                await aai_ws.send(pcm16_chunk)

        async def read_turns():
            async for raw in aai_ws:
                msg = json.loads(raw)
                if msg.get("type") == "Turn" and msg.get("end_of_turn"):
                    # Hand the final transcript to YOUR LLM, then YOUR TTS.
                    await on_final_transcript(msg["transcript"])

        await asyncio.gather(send_audio(), read_turns())
