"""python -m carbon_cost.app [--db PATH] [--port 5000]"""
import argparse
from pathlib import Path

from carbon_cost import db
from carbon_cost.app import create_app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the carbon-cost web interface locally.")
    ap.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)
    create_app(args.db).run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
