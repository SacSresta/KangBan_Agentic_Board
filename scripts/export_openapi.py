"""Generate the actual FastAPI schema without starting the queue."""
import argparse
import json
from pathlib import Path
from kanban.app import create_app

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the committed schema needs regeneration")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    path = root / "openapi.json"
    generated = json.dumps(create_app(serve_ui=False).openapi(), indent=2) + "\n"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != generated:
            parser.exit(1, "openapi.json is stale. Run scripts/export_openapi.py and npm run types.\n")
        print("OpenAPI schema is current.")
    else:
        path.write_text(generated, encoding="utf-8")


if __name__ == "__main__":
    main()
