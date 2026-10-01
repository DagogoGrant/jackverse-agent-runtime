#!/usr/bin/env python3
"""Export Caseworker OpenAPI schema deterministically from FastAPI app."""

from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure src is in python path
repo_root = Path(__file__).resolve().parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from caseworker.api.app import create_app


def export_openapi() -> Path:
    app = create_app()
    schema = app.openapi()
    
    out_path = repo_root / "web" / "openapi.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, sort_keys=True)
        f.write("\n")
        
    print(f"Exported OpenAPI schema to {out_path} ({len(json.dumps(schema))} bytes)")
    return out_path


if __name__ == "__main__":
    export_openapi()
