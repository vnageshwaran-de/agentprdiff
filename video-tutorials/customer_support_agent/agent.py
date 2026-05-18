"""
customer_support_agent/agent.py

LangGraph ReAct customer support agent.

The agent answers customer queries using three tools:
  - lookup_order      : fetch order status from the (mock) database
  - process_refund    : initiate a refund for an order
  - check_policy      : look up the refund/return policy for an item category

Environment variables
---------------------
GOOGLE_MODEL      Model to use (default: gemini-2.0-flash).
                  Swap to gemini-1.5-flash for the regression demo.
GOOGLE_API_KEY    Your Gemini API key.
LIVE_TOOLS        Set to "true" to hit real backend APIs instead of mocks.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
LIVE_TOOLS = os.getenv("LIVE_TOOLS", "false").lower() == "true"

if not GOOGLE_API_KEY:
    raise EnvironmentError(
        "No API key found. Set GOOGLE_API_KEY in your .env file or environment."
    )

SYSTEM_PROMPT = """You are a helpful customer support agent for ShopFast.
You have access to tools to look up orders, process refunds, and check policies.
Always look up the order first before attempting a refund.
Be concise, empathetic, and provide clear next steps."""

# ---------------------------------------------------------------------------
# Mock data (used when LIVE_TOOLS=false — default for tests)
# ---------------------------------------------------------------------------

_MOCK_ORDERS: dict[str, dict] = {
    "1234": {"status": "delivered", "item": "Wireless Headphones", "category": "electronics", "amount": 79.99},
    "5678": {"status": "in_transit", "item": "Running Shoes", "category": "footwear", "amount": 120.00},
    "9999": {"status": "not_found", "item": None, "category": None, "amount": 0},
}

_MOCK_POLICIES: dict[str, str] = {
    "electronics": "Electronics can be returned within 30 days if unopened. Opened items are eligible for exchange only.",
    "footwear": "Footwear can be returned within 60 days in original condition with tags attached.",
    "default": "Most items can be returned within 30 days with a receipt. Final sale items are non-refundable.",
}

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def lookup_order(order_id: str) -> str:
    """Look up the status and details of a customer order by order ID."""
    if LIVE_TOOLS:
        raise NotImplementedError("Live tool integration not implemented in this tutorial.")

    order = _MOCK_ORDERS.get(order_id)
    if not order or order["status"] == "not_found":
        return json.dumps({"error": f"Order {order_id} not found."})
    return json.dumps({
        "order_id": order_id,
        "status": order["status"],
        "item": order["item"],
        "category": order["category"],
        "amount": order["amount"],
    })


@tool
def process_refund(order_id: str, reason: str) -> str:
    """Initiate a refund for a delivered order. Requires a reason."""
    if LIVE_TOOLS:
        raise NotImplementedError("Live tool integration not implemented in this tutorial.")

    order = _MOCK_ORDERS.get(order_id)
    if not order or order["status"] == "not_found":
        return json.dumps({"error": f"Cannot refund: order {order_id} not found."})
    if order["status"] != "delivered":
        return json.dumps({"error": f"Cannot refund: order {order_id} has status '{order['status']}'. Only delivered orders are eligible."})
    return json.dumps({
        "refund_id": f"REF-{order_id}-001",
        "order_id": order_id,
        "amount": order["amount"],
        "status": "approved",
        "message": f"Refund of ${order['amount']:.2f} approved. You will see it in 3–5 business days.",
    })


@tool
def check_policy(category: str) -> str:
    """Look up the return and refund policy for a given item category."""
    if LIVE_TOOLS:
        raise NotImplementedError("Live tool integration not implemented in this tutorial.")

    policy = _MOCK_POLICIES.get(category.lower(), _MOCK_POLICIES["default"])
    return json.dumps({"category": category, "policy": policy})


TOOLS = [lookup_order, process_refund, check_policy]
TOOL_MAP = {t.name: t for t in TOOLS}

# ---------------------------------------------------------------------------
# LangGraph state + nodes
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _call_model(state: AgentState) -> AgentState:
    llm = ChatGoogleGenerativeAI(model=MODEL, google_api_key=GOOGLE_API_KEY).bind_tools(TOOLS)
    system = {"role": "system", "content": SYSTEM_PROMPT}
    response = llm.invoke([system] + state["messages"])
    return {"messages": [response]}


def _call_tools(state: AgentState) -> AgentState:
    last: AIMessage = state["messages"][-1]
    tool_messages: list[ToolMessage] = []
    for call in last.tool_calls:
        fn = TOOL_MAP[call["name"]]
        result = fn.invoke(call["args"])
        tool_messages.append(
            ToolMessage(content=str(result), tool_call_id=call["id"])
        )
    return {"messages": tool_messages}


def _should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def _build_graph() -> object:
    graph = StateGraph(AgentState)
    graph.add_node("agent", _call_model)
    graph.add_node("tools", _call_tools)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


_GRAPH = _build_graph()

# ---------------------------------------------------------------------------
# Public API — used by suite.py and the smoke test
# ---------------------------------------------------------------------------

def run_agent(query: str) -> tuple[str, list[BaseMessage]]:
    """
    Run the customer support agent on a query.

    Returns
    -------
    (final_text, messages)
        final_text  : the agent's last text response
        messages    : full message history (used by agentprdiff adapters)
    """
    result = _GRAPH.invoke({"messages": [HumanMessage(content=query)]})
    messages: list[BaseMessage] = result["messages"]
    last = messages[-1] if messages else None

    # Gemini 2.5 returns content as a list of parts (including thinking signatures).
    # Extract only the plain text part.
    if last is None:
        final_text = ""
    elif isinstance(last.content, list):
        final_text = " ".join(
            part["text"] for part in last.content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        final_text = last.content

    return final_text, messages


def _extract_tool_calls(messages: list[BaseMessage]) -> list[str]:
    """Return a list of tool names called during the agent run."""
    called = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            called.extend(call["name"] for call in msg.tool_calls)
    return called


def interactive() -> None:
    """Interactive REPL for the ShopFast customer support agent."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    console = Console()

    # --- Welcome banner ---
    console.print(Panel.fit(
        "[bold cyan]ShopFast Customer Support Agent[/bold cyan]\n"
        f"[dim]Model: {MODEL}  •  Tools: lookup_order, process_refund, check_policy[/dim]",
        border_style="cyan",
    ))

    # --- Mock data cheat-sheet ---
    orders_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    orders_table.add_column("Order ID", style="yellow")
    orders_table.add_column("Item")
    orders_table.add_column("Status", style="green")
    orders_table.add_column("Amount")
    for oid, o in _MOCK_ORDERS.items():
        if o["status"] != "not_found":
            orders_table.add_row(oid, o["item"], o["status"], f"${o['amount']:.2f}")
        else:
            orders_table.add_row(oid, "—", "[red]not found[/red]", "—")

    console.print("\n[bold]Available mock orders:[/bold]")
    console.print(orders_table)

    console.print("[bold]Policies:[/bold] electronics (30 days) · footwear (60 days) · default (30 days)\n")
    console.print("[dim]Try asking: 'Refund order 1234' · 'Where is order 5678?' · 'What's your return policy?'[/dim]")
    console.print("[dim]Type [bold]exit[/bold] or [bold]quit[/bold] to leave.\n[/dim]")

    # --- REPL loop ---
    while True:
        try:
            query = console.input("[bold cyan]You ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not query:
            continue
        if query.lower() in {"exit", "quit", "q"}:
            console.print("[dim]Goodbye![/dim]")
            break

        with console.status("[dim]Agent thinking…[/dim]", spinner="dots"):
            try:
                answer, messages = run_agent(query)
            except Exception as exc:
                console.print(f"[red]Error:[/red] {exc}")
                continue

        # Show which tools were called
        tools_used = _extract_tool_calls(messages)
        if tools_used:
            tool_str = " → ".join(f"[green]{t}[/green]" for t in tools_used)
            console.print(f"[dim]Tools:[/dim] {tool_str}")

        # Print the agent's answer
        console.print(Panel(
            Markdown(answer) if answer.startswith(("#", "*", "-", ">", "`")) else Text(answer),
            border_style="blue",
            title="[bold blue]Agent[/bold blue]",
            title_align="left",
        ))
        console.print()


if __name__ == "__main__":
    interactive()
