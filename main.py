from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from langchain_core.messages import HumanMessage
from graphdb import test_graphdb_connection
from graph import agent_graph, parse_json_safe

import uuid

app = FastAPI(title="Azure Infra Chatbot — Agentic Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    text                : str
    session_id          : Optional[str]  = None
    # ── Persisted fields — frontend sends these back each turn ──────────────
    confirmed_blueprint : Optional[str]  = None
    mandatory_params    : Optional[dict] = None
    state_json          : Optional[dict] = None


class ChatResponse(BaseModel):
    session_id          : str
    response            : str
    data                : Optional[dict] = None
    blueprint_matches   : Optional[dict] = None
    common_blueprints   : Optional[list] = None
    confirmed_blueprint : Optional[str]  = None
    mandatory_params    : Optional[dict] = None
    state_json          : Optional[dict] = None


@app.on_event("startup")
async def startup():
    print("Testing GraphDB connection on startup...")
    test_graphdb_connection()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    session_id = request.session_id or str(uuid.uuid4())
    config     = {"configurable": {"thread_id": session_id}}

    try:
        result = agent_graph.invoke(
            {
                "messages"          : [HumanMessage(content=request.text)],
                "session_id"        : session_id,
                "last_response"     : "",
                # ── Always reset these every turn ──────────────────────────
                "intermediate_json" : None,
                "blueprint_matches" : None,
                "common_blueprints" : None,
                # ── Persist these — carry forward from previous turn ───────
                "confirmed_blueprint": request.confirmed_blueprint,
                "mandatory_params"   : request.mandatory_params,
                "state_json"         : request.state_json,
            },
            config=config
        )

        return ChatResponse(
            session_id          = session_id,
            response            = result["last_response"],
            data                = parse_json_safe(result["last_response"]),
            blueprint_matches   = result.get("blueprint_matches"),
            common_blueprints   = result.get("common_blueprints"),
            confirmed_blueprint = result.get("confirmed_blueprint"),
            mandatory_params    = result.get("mandatory_params"),
            state_json          = result.get("state_json"),
        )

    except Exception as e:
        print(f"ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/session/{session_id}")
def clear_session(session_id: str):
    return {"status": "session cleared", "session_id": session_id}