from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from state import AgentState
from config import OPENAI_API_KEY, OPENAI_MODEL, SYSTEM_PROMPT
from graphdb import (
    find_blueprints_for_multiple_resources,
    get_common_blueprints,
    get_mandatory_params_for_all_resources
)

import json
import re

# ── LLM ───────────────────────────────────────────────────────────────────────
llm = ChatOpenAI(
    model       = OPENAI_MODEL,
    api_key     = OPENAI_API_KEY,
    temperature = 0,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def parse_json_safe(text: str):
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    try:
        start = text.index("{")
        end   = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception:
        pass
    try:
        start = text.index("[")
        end   = text.rindex("]") + 1
        return json.loads(text[start:end])
    except Exception:
        pass
    match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


def extract_resource_names(intermediate_json) -> list:
    if not intermediate_json:
        return []
    names = []
    if isinstance(intermediate_json, dict):
        for key, value in intermediate_json.items():
            if isinstance(value, dict) and key not in ("qn_flag", "qn", "metadata"):
                if key not in names:
                    names.append(key)
        return names
    if isinstance(intermediate_json, list):
        for item in intermediate_json:
            if isinstance(item, dict):
                for key, value in item.items():
                    if isinstance(value, dict) and key not in ("qn_flag", "qn", "metadata"):
                        if key not in names:
                            names.append(key)
    return names


# ── Node 1: LLM ───────────────────────────────────────────────────────────────
def llm_node(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    raw      = response.content

    print("\n========== LLM RAW RESPONSE ==========")
    print(repr(raw))
    print("======================================\n")

    intermediate_json = parse_json_safe(raw)
    print(f"intermediate_json parsed: {intermediate_json}\n")

    return {
        "messages"           : [AIMessage(content=raw)],
        "last_response"      : raw,
        "session_id"         : state.get("session_id", ""),
        "intermediate_json"  : intermediate_json,
        # ── Carry persisted fields forward untouched ──────────────────────
        "blueprint_matches"  : None,
        "common_blueprints"  : None,
        "confirmed_blueprint": state.get("confirmed_blueprint"),
        "mandatory_params"   : state.get("mandatory_params"),
        "state_json"         : state.get("state_json"),
    }


# ── Node 2: GraphDB blueprint matching ───────────────────────────────────────
def graphdb_node(state: AgentState) -> AgentState:
    intermediate_json = state.get("intermediate_json")

    if not intermediate_json:
        return {
            "messages"           : state["messages"],
            "session_id"         : state.get("session_id", ""),
            "last_response"      : state.get("last_response", ""),
            "intermediate_json"  : None,
            "blueprint_matches"  : None,
            "common_blueprints"  : None,
            "confirmed_blueprint": state.get("confirmed_blueprint"),
            "mandatory_params"   : state.get("mandatory_params"),
            "state_json"         : state.get("state_json"),
        }

    resource_names    = extract_resource_names(intermediate_json)
    print(f"\nGraphDB query for resources: {resource_names}")

    blueprint_matches = find_blueprints_for_multiple_resources(resource_names)
    common_blueprints = get_common_blueprints(blueprint_matches)

    print(f"Common blueprints: {common_blueprints}")

    return {
        "messages"           : state["messages"],
        "session_id"         : state.get("session_id", ""),
        "last_response"      : state.get("last_response", ""),
        "intermediate_json"  : intermediate_json,
        "blueprint_matches"  : blueprint_matches,
        "common_blueprints"  : common_blueprints,
        "confirmed_blueprint": state.get("confirmed_blueprint"),
        "mandatory_params"   : state.get("mandatory_params"),
        "state_json"         : state.get("state_json"),
    }




# ── Node 3: Fetch mandatory params ───────────────────────────────────────────
def fetch_params_node(state: AgentState) -> AgentState:
    print(f"\n--- fetch_params_node ---")

    # ── Already have mandatory params from previous turn ──────────────────
    if state.get("mandatory_params"):
        print("Mandatory params already loaded — skipping")
        return {
            "messages"           : state["messages"],
            "session_id"         : state.get("session_id", ""),
            "last_response"      : state.get("last_response", ""),
            "intermediate_json"  : state.get("intermediate_json"),
            "blueprint_matches"  : state.get("blueprint_matches"),
            "common_blueprints"  : state.get("common_blueprints"),
            "confirmed_blueprint": state.get("confirmed_blueprint"),
            "mandatory_params"   : state.get("mandatory_params"),
            "state_json"         : state.get("state_json"),
        }

    # ── Derive confirmed_blueprint directly from common_blueprints ─────────
    # Do NOT rely on confirmed_blueprint from check_blueprint_node
    # because same-turn inter-node state has the MemorySaver issue
    common_blueprints = state.get("common_blueprints") or []

    # Also check if it was persisted from a previous turn via the frontend
    confirmed_blueprint = state.get("confirmed_blueprint")

    print(f"common_blueprints   : {common_blueprints}")
    print(f"confirmed_blueprint : {confirmed_blueprint}")

    # Derive from common_blueprints if not already confirmed
    if not confirmed_blueprint:
        if len(common_blueprints) == 1:
            # Derive it right here — don't depend on check_blueprint_node
            confirmed_blueprint = common_blueprints[0]
            print(f"Derived confirmed_blueprint: {confirmed_blueprint}")

        elif len(common_blueprints) > 1:
            msg = (
                f"Multiple blueprints match: {', '.join(common_blueprints)}. "
                f"Please confirm which to proceed with."
            )
            return {
                "messages"           : state["messages"],
                "session_id"         : state.get("session_id", ""),
                "last_response"      : msg,
                "intermediate_json"  : state.get("intermediate_json"),
                "blueprint_matches"  : state.get("blueprint_matches"),
                "common_blueprints"  : common_blueprints,
                "confirmed_blueprint": None,
                "mandatory_params"   : None,
                "state_json"         : state.get("state_json"),
            }

        else:
            msg = "No blueprint found covering all requested resources."
            return {
                "messages"           : state["messages"],
                "session_id"         : state.get("session_id", ""),
                "last_response"      : msg,
                "intermediate_json"  : state.get("intermediate_json"),
                "blueprint_matches"  : state.get("blueprint_matches"),
                "common_blueprints"  : common_blueprints,
                "confirmed_blueprint": None,
                "mandatory_params"   : None,
                "state_json"         : state.get("state_json"),
            }

    # ── Fetch params ───────────────────────────────────────────────────────
    intermediate_json = state.get("intermediate_json")

    if not intermediate_json:
        return {
            "messages"           : state["messages"],
            "session_id"         : state.get("session_id", ""),
            "last_response"      : state.get("last_response", ""),
            "intermediate_json"  : None,
            "blueprint_matches"  : state.get("blueprint_matches"),
            "common_blueprints"  : common_blueprints,
            "confirmed_blueprint": confirmed_blueprint,
            "mandatory_params"   : None,
            "state_json"         : state.get("state_json"),
        }

    resource_names   = extract_resource_names(intermediate_json)
    mandatory_params = get_mandatory_params_for_all_resources(
        confirmed_blueprint,
        resource_names
    )

    print(f"Mandatory params fetched: {json.dumps(mandatory_params, indent=2)}")

    return {
        "messages"           : state["messages"],
        "session_id"         : state.get("session_id", ""),
        "last_response"      : state.get("last_response", ""),
        "intermediate_json"  : intermediate_json,
        "blueprint_matches"  : state.get("blueprint_matches"),
        "common_blueprints"  : common_blueprints,
        "confirmed_blueprint": confirmed_blueprint,
        "mandatory_params"   : mandatory_params,
        "state_json"         : state.get("state_json"),
    }


# ── Node 4: Match params to intermediate JSON ─────────────────────────────────
def match_params_node(state: AgentState) -> AgentState:
        print("-----match_params_node----")
        mandatory_params    = state.get("mandatory_params")
        intermediate_json   = state.get("intermediate_json")
        existing_state_json = state.get("state_json") or {}
        print (f"mandatory parameters in match_params_node :{json.dumps(mandatory_params,indent=2)}")

        if not mandatory_params or not intermediate_json:
            return {
                "messages"           : state["messages"],
                "session_id"         : state.get("session_id", ""),
                "last_response"      : state.get("last_response", ""),
                "intermediate_json"  : intermediate_json,
                "blueprint_matches"  : state.get("blueprint_matches"),
                "common_blueprints"  : state.get("common_blueprints"),
                "confirmed_blueprint": state.get("confirmed_blueprint"),
                "mandatory_params"   : mandatory_params,
                "state_json"         : existing_state_json or None,
            }

        match_prompt = f"""
                You are a JSON field matching assistant.

                 You are given:
    1. An intermediate JSON containing Azure resource configurations provided by the user
    2. A list of mandatory parameter keys per resource type from a blueprint registry
    3. An existing state JSON from previous turns that already has some values matched

    Your task:
    - For each mandatory parameter key check the intermediate JSON for a matching value
    - Use semantic matching — key names and JSON field names may differ slightly
    (e.g. "azuredatafactory_workspace_name" matches "workspaceName" under DataFactory or AzureDataFactory json array key )
    - If a match is found include it as: "param_key": "matched_value"
    - If no match is found include it as: "param_key": null
    - For keys already present in the existing state JSON with a non-null value
    keep the existing value — do not overwrite with null
    - Return ONLY a flat JSON object of all key-value pairs
    - No explanation, no markdown, no extra text

    Intermediate JSON:
    {json.dumps(intermediate_json, indent=2)}

    Mandatory parameter keys per resource:
    {json.dumps(mandatory_params, indent=2)}

    Existing state JSON (do not overwrite non-null values):
    {json.dumps(existing_state_json, indent=2)}

    Return a single flat JSON object.
    """

        response   = llm.invoke([HumanMessage(content=match_prompt)])
        raw        = response.content
        
        print (f"response from LLM call :{raw}")
        state_json = parse_json_safe(raw)

        # Safety merge
        if state_json and existing_state_json:
            for key, val in existing_state_json.items():
                if val is not None and state_json.get(key) is None:
                    state_json[key] = val

        missing = [k for k, v in (state_json or {}).items() if v is None]

        print(f"\nState JSON: {json.dumps(state_json, indent=2)}")
        print(f"Missing params: {missing}")

        last_response = json.dumps({
            "state_json"    : state_json,
            "missing_params": missing if missing else None,
            "status"        : "incomplete" if missing else "ready"
        }, indent=2)

        return {
            "messages"           : state["messages"],
            "session_id"         : state.get("session_id", ""),
            "last_response"      : last_response,
            "intermediate_json"  : intermediate_json,
            "blueprint_matches"  : state.get("blueprint_matches"),
            "common_blueprints"  : state.get("common_blueprints"),
            "confirmed_blueprint": state.get("confirmed_blueprint"),
            "mandatory_params"   : mandatory_params,
            "state_json"         : state_json,
        }


# ── Routers ───────────────────────────────────────────────────────────────────
def should_query_graphdb(state: AgentState) -> str:
    """Skip GraphDB if blueprint already confirmed from previous turn."""
    if state.get("confirmed_blueprint"):
        print("Blueprint already confirmed — skipping GraphDB blueprint query")
        return "skip_to_params"
    if state.get("intermediate_json") is not None:
        return "query_graphdb"
    return "end"


def should_check_blueprint(state: AgentState) -> str:
    if state.get("blueprint_matches"):
        return "check_blueprint"
    return "end"


def should_fetch_params(state: AgentState) -> str:
    print(f"\n--- should_fetch_params router ---")
    print(f"confirmed_blueprint : {state.get('confirmed_blueprint')}")
    print(f"mandatory_params    : {state.get('mandatory_params')}")
    print(f"intermediate_json   : {state.get('intermediate_json')}")
    print(f"----------------------------------\n")

    if state.get("mandatory_params"):
        print("Mandatory params already loaded — skipping to match")
        return "skip_to_match"
    if state.get("confirmed_blueprint"):
        return "fetch_params"
    return "end"

def should_match_params(state: AgentState) -> str:
    print("\n🚨 ROUTER HIT: should_match_params")

    mandatory_params = state.get("mandatory_params")

    print("mandatory_params:", mandatory_params)
    print("type:", type(mandatory_params))

    return "match_params"


# ── Build graph ───────────────────────────────────────────────────────────────
def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("llm",          llm_node)
    builder.add_node("graphdb",      graphdb_node)
    builder.add_node("fetch_params", fetch_params_node)
    builder.add_node("match_params", match_params_node)

    builder.set_entry_point("llm")

    builder.add_conditional_edges(
        "llm",
        should_query_graphdb,
        {
            "query_graphdb"  : "graphdb",
            "skip_to_params" : "fetch_params",
            "end"            : END,
        }
    )

    # graphdb always goes to fetch_params — blueprint check is inside fetch_params
    builder.add_edge("graphdb", "fetch_params")

    builder.add_conditional_edges(
        "fetch_params",
        should_match_params,
        { "match_params": "match_params", "end": END }
    )

    builder.add_edge("match_params", END)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory)


agent_graph = build_graph()