"""Write the API contract to contracts/openapi.json.

    python -m src.protocol.export

The output is the FastAPI OpenAPI document plus every WebSocket message model under
components/schemas, grouped by direction in the top-level `x-websocket-messages` key.
The web client generates its TypeScript types from this file, and CI checks it is current.
"""

import argparse
import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from . import bench_v1

REF_TEMPLATE = "#/components/schemas/{model}"

MESSAGE_GROUPS = {
    "hub_to_cloud": bench_v1.HUB_TO_CLOUD_MODELS,
    "cloud_to_hub": bench_v1.CLOUD_TO_HUB_MODELS,
    "client_to_cloud": bench_v1.CLIENT_TO_CLOUD_MODELS,
    "cloud_to_client": bench_v1.CLOUD_TO_CLIENT_MODELS,
}

# Messages the cloud receives are described as accepted input; messages it sends as produced output
GROUP_MODES = {
    "hub_to_cloud": "validation",
    "cloud_to_hub": "serialization",
    "client_to_cloud": "validation",
    "cloud_to_client": "serialization",
}


def build_contract() -> dict:
    from ..main import app

    document = app.openapi()
    schemas = document.setdefault("components", {}).setdefault("schemas", {})

    _, ws_schema = models_json_schema(
        [(model, GROUP_MODES[group]) for group, group_models in MESSAGE_GROUPS.items() for model in group_models],
        ref_template=REF_TEMPLATE,
    )
    for name, schema in ws_schema.get("$defs", {}).items():
        existing = schemas.get(name)
        if existing is None:
            schemas[name] = schema
        elif set(existing.get("properties", {})) != set(schema.get("properties", {})):
            # Same name, different model: TypeScript types would silently merge them
            raise RuntimeError(f"WebSocket model {name} conflicts with a REST schema of the same name")

    document["x-websocket-messages"] = {
        group: [model.__name__ for model in group_models] for group, group_models in MESSAGE_GROUPS.items()
    }
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="contracts/openapi.json", help="Output path")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_contract(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
