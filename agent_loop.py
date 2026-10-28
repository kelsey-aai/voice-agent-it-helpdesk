# agent_loop.py: one assistant turn. The LLM sees the tools on every request,
# your code runs the ones it calls, and the results go back into the same
# conversation until the model answers in words.
import json
from typing import List, Optional, Tuple

from integrations import itsm_create_ticket, itsm_get_ticket, kb_search
from tools import HELPDESK_TOOLS

LLM_MODEL = "claude-haiku-4-5-20251001"  # any tool-capable model on LLM Gateway
MAX_TOOL_ROUNDS = 4
HANDOFF_LINE = "Let me get a technician on the line for you. One moment."


def _arguments(tool_call) -> dict:
    args = tool_call.function.arguments
    if isinstance(args, str):
        args = json.loads(args or "{}")
    return args or {}


async def run_tool(name: str, args: dict) -> dict:
    if name == "search_knowledge_base":
        return await kb_search(args["query"], args.get("category"))
    if name == "create_ticket":
        return await itsm_create_ticket(args)
    if name == "check_ticket_status":
        return await itsm_get_ticket(args["ticket_id"])
    return {"error": f"unknown tool {name}"}


async def respond(llm, messages: List[dict]) -> Tuple[str, Optional[dict]]:
    """Returns (what to say, escalation). `escalation` is the escalate_to_human
    arguments when the model hands the call off, otherwise None.
    `messages` is updated in place."""
    for _ in range(MAX_TOOL_ROUNDS):
        completion = await llm.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=HELPDESK_TOOLS,
            max_tokens=300,
        )
        # Some providers put the text and the tool calls in separate choices.
        text = " ".join(
            c.message.content for c in completion.choices if c.message.content
        ).strip()
        calls = [
            tc for c in completion.choices for tc in (c.message.tool_calls or [])
        ]

        # escalate_to_human is terminal: no result goes back to the model,
        # because the transfer ends this conversation.
        for tc in calls:
            if tc.function.name == "escalate_to_human":
                return HANDOFF_LINE, _arguments(tc)

        if not calls:
            messages.append({"role": "assistant", "content": text})
            return text, None

        # Run every tool first, then record the calls and their results
        # together, so an interrupted turn never leaves a call unanswered.
        results = [await run_tool(tc.function.name, _arguments(tc)) for tc in calls]
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": json.dumps(_arguments(tc)),
                        },
                    }
                    for tc in calls
                ],
            }
        )
        for tc, result in zip(calls, results):
            print(f"[tool] {tc.function.name} -> {result}")
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)}
            )

    return "Sorry, I lost track of that. Could you tell me once more what's going on?", None
