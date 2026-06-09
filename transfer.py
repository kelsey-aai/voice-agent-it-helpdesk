# transfer.py
#
# `escalate_to_human` is a TERMINAL tool: you never send a tool.result for it.
# Transferring the caller ends the <Connect><Stream>, which tears down the
# Voice Agent session — there's no live session left to receive a result.
#
# This implementation redirects the live Twilio call to a human queue number
# using the Twilio REST API. If Twilio credentials aren't configured, it logs
# the hand-off instead, so the agent still runs end to end in development.
#
# Doing the transfer *warmly* — keeping a live transcript running for the
# technician across a conference bridge — is its own subject; this is the
# straight hand-off.

import os

# Map each escalation queue to the phone number that should take the call.
# In production these come from your telephony/ACD config. Strings are read
# from the environment so you don't commit real numbers.
QUEUE_NUMBERS = {
    "service_desk": os.environ.get("QUEUE_SERVICE_DESK"),
    "identity_access": os.environ.get("QUEUE_IDENTITY_ACCESS"),
    "network": os.environ.get("QUEUE_NETWORK"),
    "security": os.environ.get("QUEUE_SECURITY"),
    "on_call": os.environ.get("QUEUE_ON_CALL"),
}

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
# Fallback number used when a queue isn't individually configured.
DEFAULT_TRANSFER_NUMBER = os.environ.get("HUMAN_TRANSFER_NUMBER")


async def start_transfer(call_sid: str | None, arguments: dict) -> None:
    """Hand the live call to a human queue. Arguments come straight from the
    escalate_to_human tool.call (already a dict): {queue, summary}."""
    queue = (arguments or {}).get("queue", "service_desk")
    summary = (arguments or {}).get("summary", "")
    number = QUEUE_NUMBERS.get(queue) or DEFAULT_TRANSFER_NUMBER

    # In a real deployment, push `summary` to the agent's screen-pop / ticket
    # so the technician has context the moment they pick up.
    print(f"[transfer] queue={queue} summary={summary!r}")

    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and call_sid and number):
        print("[transfer] mock mode — set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
              f"and a queue/HUMAN_TRANSFER_NUMBER to dial for real "
              f"(would transfer call {call_sid} to {queue}).")
        return

    # Redirect the in-progress call to <Dial> the human queue. Updating the
    # call with new TwiML replaces the <Connect><Stream>, which closes the media
    # WebSocket and ends the agent session.
    from twilio.rest import Client

    twiml = f"<Response><Dial>{number}</Dial></Response>"
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    # Twilio's SDK call is synchronous; run it off the event loop.
    import asyncio

    await asyncio.to_thread(client.calls(call_sid).update, twiml=twiml)
    print(f"[transfer] redirected call {call_sid} to {queue} ({number}).")
