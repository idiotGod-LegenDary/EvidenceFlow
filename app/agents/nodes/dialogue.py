from app.agents.state import AgentState
from app.business.dialogue import DialogueSlots, resolve_turn
from app.core.request_scope import get_db_session
from app.repositories.models import DialogueRow


async def dialogue_node(state: AgentState) -> dict:
    db = get_db_session()
    session_id = state["session_id"]
    row = await db.get(DialogueRow, session_id)
    previous = DialogueSlots.model_validate_json(row.slots_json) if row else DialogueSlots()
    slots, query, clarification = resolve_turn(state["user_query"], previous)
    if row is None:
        row = DialogueRow(session_id=session_id, slots_json="{}")
        db.add(row)
    row.slots_json = slots.model_dump_json()
    return {
        "raw_user_query": state["user_query"],
        "user_query": query,
        "clarification": clarification,
        "dialogue_slots": slots.model_dump(),
    }


async def clarify_node(state: AgentState) -> dict:
    return {"final_answer": state["clarification"], "citations": [], "pending_action": None}
