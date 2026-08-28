from flask import Blueprint, current_app, jsonify, request

from ..services.ai_data_query_service import AgentService

agent_bp = Blueprint("agent", __name__)


@agent_bp.post("/agent")
def handle_agent():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("question"), str):
        return jsonify({"error": "`question` (string) is required"}), 400

    service = AgentService(current_app.config)
    result = service.run_agent(payload)
    return jsonify(result)


@agent_bp.get("/agent/capabilities")
def capabilities():
    from ..services.ai_data_query_service import get_capabilities_response

    role = request.args.get("role", "")
    return jsonify({"html": get_capabilities_response(role)})
