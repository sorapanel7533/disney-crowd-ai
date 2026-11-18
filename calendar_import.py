"""One-time, repeatable importer and pre-import backup utility for calendar data."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from calendar_store import import_calendar_to_db, read_and_validate_calendar


DB_NAMES = {"Disneyland": "disneyland.db", "DisneySea": "disneysea.db"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def backup_databases(
    db_dir: str | Path,
    backup_dir: str | Path,
    manifest_path: str | Path,
    *,
    source_commit: str = "",
) -> dict[str, object]:
    """Make consistent SQLite backups and a verifiable immutable pre-import manifest."""
    source_root, destination = Path(db_dir), Path(backup_dir)
    destination.mkdir(parents=True, exist_ok=True)
    file_manifest: dict[str, object] = {}
    for park, filename in DB_NAMES.items():
        source = source_root / filename
        if not source.is_file():
            raise FileNotFoundError(f"既存DBがないためバックアップを中止しました: {source}")
        source_conn = _read_only_connection(source)
        backup = destination / f"{source.stem}-pre-calendar-import.db"
        try:
            before = source_conn.execute("PRAGMA integrity_check").fetchone()[0]
            if before != "ok":
                raise sqlite3.DatabaseError(f"バックアップ前の整合性確認に失敗: {filename}: {before}")
            target_conn = sqlite3.connect(backup, timeout=30)
            try:
                source_conn.backup(target_conn)
                after = target_conn.execute("PRAGMA integrity_check").fetchone()[0]
                if after != "ok":
                    raise sqlite3.DatabaseError(f"バックアップの整合性確認に失敗: {backup.name}: {after}")
            finally:
                target_conn.close()
        finally:
            source_conn.close()
        file_manifest[filename] = {
            "park": park,
            "source_sha256": _sha256(source),
            "backup_sha256": _sha256(backup),
            "backup_file": backup.name,
            "backup_size_bytes": backup.stat().st_size,
            "integrity": "ok",
        }

    manifest = {
        "checkpoint_created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_crowd_data_commit": source_commit,
        "databases": file_manifest,
        "purpose": "Pre-import backup for the supplied calendar snapshot; no database has been changed yet.",
    }
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def validate_files(ticket_csv: str | Path, show_csv: str | Path) -> dict[str, object]:
    _, _, quality, ticket_sha, show_sha = read_and_validate_calendar(ticket_csv, show_csv)
    return {"quality": quality, "ticket_sha256": ticket_sha, "shows_sha256": show_sha}


def import_databases(
    db_dir: str | Path,
    ticket_csv: str | Path,
    show_csv: str | Path,
    *,
    source_archive: str,
) -> list[dict[str, object]]:
    directory = Path(db_dir)
    results = []
    for park, filename in DB_NAMES.items():
        results.append(import_calendar_to_db(
            directory / filename,
            park,
            ticket_csv,
            show_csv,
            source_archive=source_archive,
        ))
    return results


def import_status(
    db_dir: str | Path,
    ticket_csv: str | Path,
    show_csv: str | Path,
) -> dict[str, object]:
    tickets, shows, _, ticket_sha, shows_sha = read_and_validate_calendar(ticket_csv, show_csv)
    directory = Path(db_dir)
    parks = {}
    for park, filename in DB_NAMES.items():
        code = "TDL" if park == "Disneyland" else "TDS"
        expected_tickets = sum(row["park"] == code for row in tickets)
        expected_shows = sum(row["park"] == code for row in shows)
        batch_id = hashlib.sha256(f"{ticket_sha}:{shows_sha}:{park}".encode("ascii")).hexdigest()
        path = directory / filename
        complete = False
        if path.is_file():
            with _read_only_connection(path) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and {
                    "official_calendar_ticket_prices", "official_calendar_shows", "calendar_import_batches"
                }.issubset(tables):
                    ticket_count = conn.execute(
                        "SELECT COUNT(*) FROM official_calendar_ticket_prices WHERE park=?", (park,),
                    ).fetchone()[0]
                    show_count = conn.execute(
                        "SELECT COUNT(*) FROM official_calendar_shows WHERE park=?", (park,),
                    ).fetchone()[0]
                    batch = conn.execute(
                        "SELECT 1 FROM calendar_import_batches WHERE batch_id=? AND ticket_sha256=? AND shows_sha256=?",
                        (batch_id, ticket_sha, shows_sha),
                    ).fetchone()
                    complete = bool(batch) and ticket_count == expected_tickets and show_count == expected_shows
        parks[park] = {
            "complete": complete,
            "expected_ticket_rows": expected_tickets,
            "expected_show_rows": expected_shows,
        }
    return {"already_imported": all(row["complete"] for row in parks.values()), "parks": parks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="create and verify pre-import database copies")
    backup.add_argument("--db-dir", required=True)
    backup.add_argument("--backup-dir", required=True)
    backup.add_argument("--manifest", required=True)
    backup.add_argument("--source-commit", default="")

    validate = subparsers.add_parser("validate", help="validate the supplied CSVs without writing a database")
    validate.add_argument("--ticket-csv", required=True)
    validate.add_argument("--show-csv", required=True)

    apply = subparsers.add_parser("import", help="import validated rows into both existing park databases")
    apply.add_argument("--db-dir", required=True)
    apply.add_argument("--ticket-csv", required=True)
    apply.add_argument("--show-csv", required=True)
    apply.add_argument("--source-archive", default="disney_stage8_codex_import.zip")

    status = subparsers.add_parser("status", help="check whether the exact supplied snapshot is already stored")
    status.add_argument("--db-dir", required=True)
    status.add_argument("--ticket-csv", required=True)
    status.add_argument("--show-csv", required=True)
    status.add_argument("--github-output", default="")

    args = parser.parse_args(argv)
    if args.command == "backup":
        result = backup_databases(args.db_dir, args.backup_dir, args.manifest, source_commit=args.source_commit)
    elif args.command == "validate":
        result = validate_files(args.ticket_csv, args.show_csv)
    elif args.command == "status":
        result = import_status(args.db_dir, args.ticket_csv, args.show_csv)
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as output:
                output.write(f"already_imported={str(result['already_imported']).lower()}\n")
    else:
        result = import_databases(
            args.db_dir, args.ticket_csv, args.show_csv, source_archive=args.source_archive,
        )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
