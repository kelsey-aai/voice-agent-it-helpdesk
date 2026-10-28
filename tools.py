# tools.py: the four helpdesk tools, in the OpenAI function format that
# LLM Gateway accepts. They travel with every chat completion request.
CATEGORIES = ["access", "network", "hardware", "software", "security"]


def tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


SEARCH_KB = tool(
    "search_knowledge_base",
    "Search the internal IT knowledge base for how-to steps and known fixes. "
    "Call this for any 'how do I' or 'why is X happening' question: VPN setup, "
    "password policy, software install steps, printer config, known outages. "
    "Do NOT call this to look up a specific ticket; use check_ticket_status for that.",
    {
        "query": {
            "type": "string",
            "description": "The caller's question, rephrased as a search query.",
        },
        "category": {"type": "string", "enum": CATEGORIES},
    },
    ["query"],
)

CREATE_TICKET = tool(
    "create_ticket",
    "Open a new support ticket. Call this only after you have a clear, "
    "one-line problem description and the caller's employee ID. Read the "
    "returned ticket number back to the caller exactly as digits.",
    {
        "employee_id": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "summary": {"type": "string", "description": "One-line problem statement."},
    },
    ["employee_id", "category", "summary"],
)

CHECK_STATUS = tool(
    "check_ticket_status",
    "Look up the status of an existing ticket by its number "
    "(for example INC0012345). Use this whenever the caller asks "
    "'what's the status of my ticket' or gives you a ticket number.",
    {"ticket_id": {"type": "string"}},
    ["ticket_id"],
)

ESCALATE = tool(
    "escalate_to_human",
    "Transfer the caller to a human technician. Call this when the caller "
    "asks for a person, when the issue is a security incident, when a system "
    "is down for many users, or when you cannot resolve the issue after two "
    "attempts. Always write a one-line summary first.",
    {
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
    ["queue", "summary"],
)

HELPDESK_TOOLS = [SEARCH_KB, CREATE_TICKET, CHECK_STATUS, ESCALATE]
