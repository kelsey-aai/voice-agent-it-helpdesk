# tool_loop.py
#
# The tool execution contract: the agent sends a `tool.call`, and you return a
# `tool.result` keyed by the same `call_id`, with the result encoded as a JSON
# *string* (not a raw object). You return results when the agent is idle — i.e.
# when `reply.done` is the latest event — so you never cut off audio that's
# still playing.
import json

from integrations import itsm_create_ticket, itsm_get_ticket, kb_search


async def run_tool(call: dict) -> dict:
    """Dispatch one tool.call to its handler. Returns a plain dict."""
    name = call["name"]

    # The Voice Agent API delivers `arguments` as a dict, ready to use directly.
    # We stay tolerant in case a string ever arrives (e.g. from another caller).
    args = call.get("arguments") or {}
    if isinstance(args, str):
        args = json.loads(args or "{}")

    if name == "search_knowledge_base":
        return await kb_search(args["query"], args.get("category"))
    if name == "create_ticket":
        return await itsm_create_ticket(args)
    if name == "check_ticket_status":
        return await itsm_get_ticket(args["ticket_id"])
    return {"error": f"unknown tool {name}"}


async def flush_pending(va_ws, pending, last_type):
    """Return results only at a reply.done boundary, so we never
    cut off audio the agent is still speaking."""
    if last_type != "reply.done" or not pending:
        return
    for call in pending:
        result = await run_tool(call)
        await va_ws.send(json.dumps({
            "type": "tool.result",
            "call_id": call["call_id"],
            "result": json.dumps(result),   # result must be a JSON STRING
        }))
    pending.clear()
