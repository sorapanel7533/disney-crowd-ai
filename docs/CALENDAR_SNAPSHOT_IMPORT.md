# Calendar snapshot import

The rows in `data/calendar-import/` came from the user-supplied `disney_stage8_codex_import.zip`.
The import uses the two CSV files only; the README and workbook were reviewed but are not imported.

## Data and limits

- Ticket prices and park hours: 274 rows for Tokyo Disneyland and Tokyo DisneySea, dated 2026-11-18 through 2027-04-03. The ticket type is adult one-day passport only. The CSV records 250 published prices and 24 `not_listed` values. Blank prices remain blank and are never converted to zero or inferred.
- Ticket and hours source: five calendar screenshots captured on 2026-11-18, as described in the supplied README. The original screenshot filename and capture date remain in the imported catalog.
- Show schedules: 836 rows for 2026-11-18 through 2026-12-31, read from official monthly show pages on 2026-11-18. Original schedule text, official page URL, and retrieval date remain in the catalog.
- `suspended`, `no_performance`, and `reservation_required` are separate states. They are not converted into guessed show times.
- The snapshot is historical data and may become outdated. The dashboard labels its check date. Automatic official-source retrieval remains enabled; newer successfully retrieved official data takes priority. On request failure the saved snapshot remains available.

## Import and recovery

The `Import supplied official calendar snapshot` workflow validates the CSVs before accessing the durable databases. It creates consistent SQLite backups of both park databases and uploads them as a 90-day Actions artifact. It also commits a checkpoint manifest to `crowd-data` before writing imported rows; that manifest identifies the exact prior `crowd-data` commit and source database hashes, so the pre-import databases remain recoverable from Git history.

Only the two park databases are updated. Import keys are `(date, park, ticket_type)` and `(date, park, show_name)`. Existing observations, predictions, measured submissions, and other unrelated rows are preserved. Re-running the same import does not add duplicate rows; the workflow skips the exact snapshot after verifying both databases.
