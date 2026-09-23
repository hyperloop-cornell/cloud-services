"""The committed contract must match the code (regenerate with `python -m src.protocol.export`)."""

import json
from pathlib import Path

from src.protocol.export import build_contract

CONTRACT = Path(__file__).parent.parent / "contracts" / "openapi.json"


def test_contract_is_current():
    committed = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(build_contract())), (
        "contracts/openapi.json is out of date; run: python -m src.protocol.export"
    )


def test_contract_lists_websocket_messages():
    contract = build_contract()
    schemas = contract["components"]["schemas"]
    for group in contract["x-websocket-messages"].values():
        for name in group:
            assert name in schemas
