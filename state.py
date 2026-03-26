from typing import Annotated, Optional
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages           : Annotated[list, add_messages]
    session_id         : str
    last_response      : str
    intermediate_json  : Optional[dict]
    blueprint_matches  : Optional[dict]
    common_blueprints  : Optional[list]
    confirmed_blueprint: Optional[str]   # the single confirmed blueprint_id
    mandatory_params   : Optional[dict]  # { resource_name: [param_key, ...] }
    state_json         : Optional[dict]  # final key-value pairs matched from intermediate json