from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, END
from sqlalchemy.orm import Session
from agent.tools import search_memory_tool, draft_content_tool, flag_contradiction_tool
from agent.limits import get_active_beliefs, check_against_beliefs, has_relevant_memory


class AgentState(TypedDict):
    org_id: str
    task: str
    task_type: str
    memory_context: Annotated[list, operator.add]
    result: str
    blocked: bool
    block_reason: str


def load_memory_node(state: AgentState, db: Session) -> dict:
    memory = search_memory_tool(state["org_id"], state["task"], db)
    if not memory:
        return {
            "memory_context": [],
            "blocked": True,
            "block_reason": "No relevant memory found. Cannot act without verified facts. Please add relevant memory first.",
        }
    return {"memory_context": memory, "blocked": False, "block_reason": ""}


def check_limits_node(state: AgentState, db: Session) -> dict:
    if state["blocked"]:
        return {}

    beliefs = get_active_beliefs(state["org_id"], db)
    check = check_against_beliefs(state["task"], beliefs)

    if check["conflict"]:
        return {
            "blocked": True,
            "block_reason": f"Action blocked by Belief: {check['belief']}",
        }

    return {"blocked": False}


def execute_node(state: AgentState) -> dict:
    if state["blocked"]:
        return {"result": f"BLOCKED: {state['block_reason']}"}

    task_type = state["task_type"]

    if task_type == "draft":
        result = draft_content_tool(state["task"], state["memory_context"])
    elif task_type == "flag":
        check = flag_contradiction_tool(state["task"], state["memory_context"])
        if check.get("contradiction"):
            result = f"CONTRADICTION DETECTED: {check['reason']}"
        else:
            result = f"No contradiction found. {check.get('reason', '')}"
    else:
        result = "Unknown task type. Use 'draft' or 'flag'."

    return {"result": result}


def should_execute(state: AgentState) -> str:
    if state["blocked"]:
        return "blocked"
    return "execute"


def build_agent(db: Session):
    graph = StateGraph(AgentState)

    graph.add_node("load_memory", lambda s: load_memory_node(s, db))
    graph.add_node("check_limits", lambda s: check_limits_node(s, db))
    graph.add_node("execute", execute_node)

    graph.set_entry_point("load_memory")
    graph.add_edge("load_memory", "check_limits")
    graph.add_conditional_edges(
        "check_limits",
        should_execute,
        {"execute": "execute", "blocked": "execute"},
    )
    graph.add_edge("execute", END)

    return graph.compile()


def run_agent(org_id: str, task: str, task_type: str, db: Session) -> dict:
    agent = build_agent(db)
    result = agent.invoke({
        "org_id": org_id,
        "task": task,
        "task_type": task_type,
        "memory_context": [],
        "result": "",
        "blocked": False,
        "block_reason": "",
    })
    return {
        "result": result["result"],
        "blocked": result["blocked"],
        "block_reason": result["block_reason"],
        "memory_used": result["memory_context"],
    }