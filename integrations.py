# integrations.py: MOCK, in-memory knowledge base and ITSM, so the agent runs
# end to end before you wire up ServiceNow, Jira Service Management, or Zendesk.
# Swap each body for a real API call and keep the returned keys the same:
# the LLM reads these dicts as tool results.
import itertools
from typing import Dict, Optional

# ── Mock knowledge base ──────────────────────────────────────────────────────
# A tiny runbook keyed by category. A real KB call returns ranked snippets; the
# agent is instructed (in the system prompt) to answer only from these.
_KB = {
    "access": [
        {
            "title": "Reset your password (self-service)",
            "summary": "Go to id.northwind.example/reset and follow the secure "
                       "link sent to your registered device. IT never asks for "
                       "your password over the phone.",
            "url": "https://kb.northwind.example/access/password-reset",
        },
        {
            "title": "Enroll in MFA",
            "summary": "Open the Okta Verify app, tap Add Account, and scan the "
                       "QR code shown at id.northwind.example/mfa.",
            "url": "https://kb.northwind.example/access/mfa-enrollment",
        },
    ],
    "network": [
        {
            "title": "Connect to the corporate VPN",
            "summary": "Open GlobalProtect, enter vpn.northwind.example as the "
                       "portal, and sign in with SSO. If it hangs at 'Connecting', "
                       "toggle Wi-Fi off and on once.",
            "url": "https://kb.northwind.example/network/vpn-setup",
        },
    ],
    "hardware": [
        {
            "title": "Add a network printer",
            "summary": "Settings > Printers > Add, then choose the printer named "
                       "for your floor (e.g. NW-3F-Canon). Driver installs "
                       "automatically on the corporate network.",
            "url": "https://kb.northwind.example/hardware/add-printer",
        },
    ],
    "software": [
        {
            "title": "Install approved software",
            "summary": "Open the Company Portal app and pick from the approved "
                       "catalog. Anything not listed needs a software request "
                       "ticket for license review.",
            "url": "https://kb.northwind.example/software/self-service-install",
        },
    ],
    "security": [
        {
            "title": "Report a phishing email",
            "summary": "Use the 'Report Phishing' button in Outlook. Do not click "
                       "links or reply. Security triages reports within 15 minutes.",
            "url": "https://kb.northwind.example/security/report-phishing",
        },
    ],
}


async def kb_search(query: str, category: Optional[str] = None) -> dict:
    """Return grounded snippets. The agent answers only from these."""
    if category and category in _KB:
        hits = _KB[category]
    else:
        # No category given: naive keyword scan across the whole KB.
        q = (query or "").lower()
        hits = [
            item
            for items in _KB.values()
            for item in items
            if any(word in (item["title"] + item["summary"]).lower()
                   for word in q.split())
        ]
    return {
        "found": bool(hits),
        "snippets": [{"title": h["title"], "text": h["summary"], "url": h["url"]}
                     for h in hits[:3]],
    }


# ── Mock ITSM (ticket store) ─────────────────────────────────────────────────
# Tickets live in a dict for the lifetime of the process. Two are pre-seeded so
# "what's the status of my ticket" works in a demo. Real ITSM numbers are
# server-generated; we mimic the ServiceNow `INC` format here.
_ticket_counter = itertools.count(20001)
_TICKETS: Dict[str, dict] = {
    "INC0019001": {
        "number": "INC0019001",
        "state": "in_progress",
        "assigned_to": "Service Desk",
        "updated_at": "2026-06-03T09:14:00Z",
    },
    "INC0019044": {
        "number": "INC0019044",
        "state": "resolved",
        "assigned_to": "Identity & Access",
        "updated_at": "2026-06-02T17:40:00Z",
    },
}


async def itsm_create_ticket(args: dict) -> dict:
    number = f"INC{next(_ticket_counter):07d}"
    _TICKETS[number] = {
        "number": number,
        "state": "new",
        "requester_id": args["employee_id"],
        "category": args["category"],
        "priority": args.get("priority", "normal"),
        "short_description": args["summary"],
        "channel": "voice_agent",  # tag so you can measure containment later
        "assigned_to": None,
        "updated_at": None,
    }
    return {"ticket_id": number, "state": "new"}


async def itsm_get_ticket(ticket_id: str) -> dict:
    t = _TICKETS.get((ticket_id or "").strip().upper())
    if not t:
        return {"found": False}
    return {
        "found": True,
        "ticket_id": t["number"],
        "state": t["state"],
        "assigned_to": t.get("assigned_to"),
        "updated": t.get("updated_at"),
    }


# ── Production versions ───────────────────────────────────────────────────────
# When you have a real KB and ITSM, delete the mocks above and use these. Set
# KB_SEARCH_URL, ITSM_API_URL, and ITSM_TOKEN in your .env. The returned dict
# shapes are identical, so nothing else in the repo changes.
#
# import os
# import httpx
#
# KB_URL = os.environ["KB_SEARCH_URL"]
# ITSM_URL = os.environ["ITSM_API_URL"]
# ITSM_TOKEN = os.environ["ITSM_TOKEN"]
#
#
# async def kb_search(query: str, category: Optional[str] = None) -> dict:
#     async with httpx.AsyncClient(timeout=10) as http:
#         resp = await http.get(KB_URL, params={"q": query, "category": category, "top": 3})
#         resp.raise_for_status()
#         hits = resp.json().get("results", [])
#     return {
#         "found": bool(hits),
#         "snippets": [{"title": h["title"], "text": h["summary"], "url": h["url"]}
#                      for h in hits],
#     }
#
#
# async def itsm_create_ticket(args: dict) -> dict:
#     async with httpx.AsyncClient(timeout=10) as http:
#         resp = await http.post(
#             f"{ITSM_URL}/tickets",
#             headers={"Authorization": f"Bearer {ITSM_TOKEN}"},
#             json={
#                 "requester_id": args["employee_id"],
#                 "category": args["category"],
#                 "priority": args.get("priority", "normal"),
#                 "short_description": args["summary"],
#                 "channel": "voice_agent",
#             },
#         )
#         resp.raise_for_status()
#         ticket = resp.json()
#     return {"ticket_id": ticket["number"], "state": ticket["state"]}
#
#
# async def itsm_get_ticket(ticket_id: str) -> dict:
#     async with httpx.AsyncClient(timeout=10) as http:
#         resp = await http.get(
#             f"{ITSM_URL}/tickets/{ticket_id}",
#             headers={"Authorization": f"Bearer {ITSM_TOKEN}"},
#         )
#         if resp.status_code == 404:
#             return {"found": False}
#         resp.raise_for_status()
#         t = resp.json()
#     return {"found": True, "ticket_id": t["number"], "state": t["state"],
#             "assigned_to": t.get("assigned_to"), "updated": t.get("updated_at")}
