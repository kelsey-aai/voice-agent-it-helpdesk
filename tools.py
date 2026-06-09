# tools.py
#
# The four helpdesk tools, as JSON Schema function definitions passed in the
# Voice Agent API session config (`session.tools`). The agent emits a
# `tool.call` event when the LLM decides to use one; you run the work and send
# back a `tool.result`.
#
# The tool `description` is the single highest-leverage thing you write — it's
# the only instruction the model gets at call time. Say *when to call this, and
# when not to*, in plain language.

SEARCH_KB = {
    "type": "function",
    "name": "search_knowledge_base",
    "description": (
        "Search the internal IT knowledge base for how-to steps and known fixes. "
        "Call this for any 'how do I' or 'why is X happening' question — VPN setup, "
        "password policy, software install steps, printer config, known outages. "
        "Do NOT call this to look up a specific ticket; use check_ticket_status for that."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The user's question, rephrased as a search query.",
            },
            "category": {
                "type": "string",
                "enum": ["access", "network", "hardware", "software", "security"],
            },
        },
        "required": ["query"],
    },
    "execution_mode": "interactive",
    "timeout_seconds": 15,
}

CREATE_TICKET = {
    "type": "function",
    "name": "create_ticket",
    "description": (
        "Open a new support ticket. Call this only after you have a clear, "
        "one-line problem description and the caller's employee ID. Read the "
        "returned ticket number back to the caller exactly as digits."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "employee_id": {"type": "string"},
            "category": {
                "type": "string",
                "enum": ["access", "network", "hardware", "software", "security"],
            },
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "summary": {"type": "string", "description": "One-line problem statement."},
        },
        "required": ["employee_id", "category", "summary"],
    },
    "execution_mode": "interactive",
    "timeout_seconds": 15,
}

CHECK_STATUS = {
    "type": "function",
    "name": "check_ticket_status",
    "description": (
        "Look up the status of an existing ticket by its number "
        "(for example INC0012345). Use this whenever the caller asks "
        "'what's the status of my ticket' or gives you a ticket number."
    ),
    "parameters": {
        "type": "object",
        "properties": {"ticket_id": {"type": "string"}},
        "required": ["ticket_id"],
    },
    "execution_mode": "interactive",
    "timeout_seconds": 15,
}

ESCALATE = {
    "type": "function",
    "name": "escalate_to_human",
    "description": (
        "Transfer the caller to a human technician. Call this when the caller "
        "asks for a person, when the issue is a security incident, when a system "
        "is down for many users, or when you cannot resolve the issue after two "
        "attempts. Always write a one-line summary first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "queue": {
                "type": "string",
                "enum": ["service_desk", "identity_access", "network", "security", "on_call"],
                "description": "Which team should take the call.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "One sentence a technician can read in two seconds. Example: "
                    "'Dana Okafor (emp 4471) locked out of SSO after a password "
                    "reset, MFA not arriving, needs urgent access.'"
                ),
            },
        },
        "required": ["queue", "summary"],
    },
    # `hold` keeps the agent silent while the transfer happens, instead of the
    # interactive "let me check" filler used by the three data tools. This is
    # the documented pattern for transfers/escalations.
    "execution_mode": "hold",
}

HELPDESK_TOOLS = [SEARCH_KB, CREATE_TICKET, CHECK_STATUS, ESCALATE]
