# Ledger

A local-first spending dashboard for ING CSV exports. Transactions are stored in SQLite and never changed by categorization. Imports are deduplicated using a SHA-256 fingerprint of the complete normalized transaction, so overlapping exports and repeated uploads do not add a transaction twice.

## Run

From this directory, start Docker Compose:

```sh
docker compose up --build -d
```

Open <http://localhost:8000>. The existing `ing/` folder is mounted read-only and its CSV files are imported at startup. The database lives in the persistent `spendings-data` Docker volume. Further exports can be added with **Import CSV**; only new transactions are inserted. The container publishes only on localhost.

## Sort and categorize

Open Transactions and drag a row onto a category. Ledger creates a merchant-contains filter and opens its editor. Refine the field, match type, value, or category; matching transactions and the dashboard totals are recalculated immediately. Filters can also be edited or deleted from the Filters view. The newest matching filter takes priority.

Filters can be moved to another deployment from the Filters view with **Export JSON** and **Import JSON**. The versioned JSON contains category names and filter conditions, not database IDs. Imports validate before applying, preserve rule priority, and skip exact filters that already exist.

Open **Categories** to add categories or edit their names and colors. Renaming a category keeps its existing filters linked to it.

## Tests

```sh
python -m unittest discover -s tests
```