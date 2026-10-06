import argparse
from pathlib import Path
import uvicorn
from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="Local Kanban Agent Board (one worker)")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port, workers=1, log_level="warning")


if __name__ == "__main__":
    main()
