"""CLI : python -m stage_radar migrate | run [--dry-run DIR] [--stages a,b]."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from stage_radar import db
from stage_radar.pipeline import STAGES, build_components, load_settings, run_pipeline


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="stage_radar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="applique les migrations SQL")
    run = sub.add_parser("run", help="exécute le pipeline")
    run.add_argument("--stages", default=",".join(STAGES))
    run.add_argument("--dry-run", metavar="DIR",
                     help="écrit le digest dans DIR au lieu de l'envoyer")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL manquant", file=sys.stderr)
        return 2
    with db.connect(url) as conn:
        if args.command == "migrate":
            applied = db.apply_migrations(conn)
            print(f"migrations appliquées : {applied or 'aucune'}")
            return 0
        stages = [s.strip() for s in args.stages.split(",") if s.strip()]
        unknown = set(stages) - set(STAGES)
        if unknown:
            print(f"étapes inconnues : {sorted(unknown)}", file=sys.stderr)
            return 2
        settings = load_settings()
        dry = Path(args.dry_run) if args.dry_run else None
        comps = build_components(settings, os.environ, dry)
        return run_pipeline(conn, comps, settings, stages, date.today())


if __name__ == "__main__":
    sys.exit(main())
