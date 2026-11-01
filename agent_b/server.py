"""Agent B - Valuation & NAV Agent: HTTP wrapper (agent card + JSON-RPC endpoint)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response

from agent_b.data import Q4Data
from agent_b.handler import ValuationHandler
from common import a2a, jsonio


def create_app(q4: Q4Data, public_url: str) -> FastAPI:
    app = FastAPI(title="Agent B - Valuation & NAV Agent")
    handler = ValuationHandler(q4)
    card = a2a.agent_card(
        name="Valuation & NAV Agent (Agent B)",
        description="Holds CVP's independent Q4 marks. Releases them only after the caller proves its Q4 "
                    "purchases and sales match the valuer's records.",
        url=f"{public_url}/a2a",
        skills=[{"id": "get-marks", "name": "Release Q4 marks", "description":
                 "Two-turn exchange: caller asks for marks, agent asks for purchases/sales, then releases marks.",
                 "tags": ["valuation", "private-equity", "nav"], "examples": ['{"fund": "GPF", "valuation_date": "2022-12-31"}']}])

    def _json(obj, status=200):
        return Response(content=jsonio.dumps(obj), status_code=status, media_type="application/json")

    @app.get(a2a.CARD_PATH)
    def get_card():
        return _json(card)

    @app.post("/a2a")
    async def rpc(request: Request):
        try:
            req = jsonio.loads(await request.body())
        except ValueError:
            return _json(a2a.rpc_error(None, a2a.PARSE_ERROR, "Parse error"))
        return _json(handler.handle_rpc(req))

    app.state.handler = handler
    return app
