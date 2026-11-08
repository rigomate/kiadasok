import csv
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", "/data/spendings.sqlite3"))
IMPORT_DIR = Path(os.environ.get("IMPORT_DIR", "/imports"))
CATEGORIES = [
    ("Groceries", "#527b55"),
    ("Dining", "#c67644"),
    ("Home", "#7184a5"),
    ("Transport", "#528d9a"),
    ("Shopping", "#ad6c76"),
    ("Subscriptions", "#7770a0"),
    ("Health", "#659386"),
    ("Income", "#54795c"),
    ("Other", "#777b77"),
]
RULE_FIELDS = {"counterparty", "details", "type"}
RULE_OPERATORS = {"contains", "equals", "starts_with"}
DUPLICATE_AMOUNT_TOLERANCE_CENTS = 1


def connect(database_path=DATABASE_PATH):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path=DATABASE_PATH):
    with connect(database_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                fingerprint TEXT PRIMARY KEY,
                booked_on TEXT NOT NULL,
                value_on TEXT NOT NULL,
                counterparty TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                details TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL REFERENCES categories(id),
                field TEXT NOT NULL,
                operator TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS import_runs (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                imported_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                rejected_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            );
            """
        )
        if not db.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
            db.executemany(
                "INSERT INTO categories(name, color) VALUES (?, ?)", CATEGORIES
            )


def validate_category(payload):
    name = str(payload.get("name", "")).strip()
    color = str(payload.get("color", "")).strip()
    if not name or len(name) > 40:
        raise ValueError("Category names must be between 1 and 40 characters")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Choose a valid category color")
    return name, color


def create_category(payload, database_path=DATABASE_PATH):
    name, color = validate_category(payload)
    with connect(database_path) as db:
        if db.execute(
            "SELECT 1 FROM categories WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone():
            raise ValueError("A category with that name already exists")
        cursor = db.execute(
            "INSERT INTO categories(name, color) VALUES (?, ?)", (name, color)
        )
        return cursor.lastrowid


def update_category(category_id, payload, database_path=DATABASE_PATH):
    name, color = validate_category(payload)
    with connect(database_path) as db:
        if not db.execute("SELECT 1 FROM categories WHERE id = ?", (category_id,)).fetchone():
            raise ValueError("Category not found")
        if db.execute(
            "SELECT 1 FROM categories WHERE name = ? COLLATE NOCASE AND id != ?",
            (name, category_id),
        ).fetchone():
            raise ValueError("A category with that name already exists")
        db.execute(
            "UPDATE categories SET name = ?, color = ? WHERE id = ?",
            (name, color, category_id),
        )
    return category_id


def parse_amount(value):
    normalized = value.strip().replace(".", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError("Invalid amount") from error
    return int(amount * 100)


def transaction_fingerprint(fields):
    canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def import_csv(content, filename, database_path=DATABASE_PATH):
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = content.decode("cp1252")

    imported = duplicates = rejected = 0
    column_indexes = None
    seen_transactions = False
    legacy_indexes = {
        "booked_on": 0,
        "value_on": 1,
        "counterparty": 2,
        "tx_type": 3,
        "details": 4,
        "amount": 5,
        "currency": 6,
    }
    header_fields = {
        "booked_on": "buchung",
        "value_on": "wertstellungsdatum",
        "counterparty": "auftraggeber/empfänger",
        "tx_type": "buchungstext",
        "details": "verwendungszweck",
        "amount": "betrag",
        "currency": "währung",
    }
    with connect(database_path) as db:
        for row in csv.reader(content.splitlines(), delimiter=";"):
            if not row or all(not value.strip() for value in row):
                continue
            first_cell = row[0].strip().lstrip("\ufeff").casefold()
            if first_cell == "buchung":
                normalized_header = [value.strip().casefold() for value in row]
                try:
                    column_indexes = {
                        field: normalized_header.index(label)
                        for field, label in header_fields.items()
                    }
                except ValueError:
                    column_indexes = {}
                    rejected += 1
                continue
            if column_indexes == {}:
                rejected += 1
                continue
            try:
                datetime.strptime(row[0].strip(), "%d.%m.%Y")
            except ValueError:
                if seen_transactions:
                    rejected += 1
                continue
            seen_transactions = True
            indexes = column_indexes or legacy_indexes
            if len(row) <= max(indexes.values()):
                rejected += 1
                continue
            try:
                booked_on = datetime.strptime(row[indexes["booked_on"]].strip(), "%d.%m.%Y").date().isoformat()
                value_on = datetime.strptime(row[indexes["value_on"]].strip(), "%d.%m.%Y").date().isoformat()
                amount_cents = parse_amount(row[indexes["amount"]])
                fields = [
                    booked_on,
                    value_on,
                    row[indexes["counterparty"]].strip(),
                    row[indexes["tx_type"]].strip(),
                    row[indexes["details"]].strip(),
                    amount_cents,
                    row[indexes["currency"]].strip(),
                ]
            except (ValueError, InvalidOperation):
                rejected += 1
                continue
            cursor = db.execute(
                """INSERT OR IGNORE INTO transactions
                (fingerprint, booked_on, value_on, counterparty, tx_type, details,
                 amount_cents, currency) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (transaction_fingerprint(fields), *fields),
            )
            if cursor.rowcount:
                imported += 1
            else:
                duplicates += 1

        db.execute(
            """INSERT INTO import_runs
            (filename, imported_count, duplicate_count, rejected_count, imported_at)
            VALUES (?, ?, ?, ?, ?)""",
            (
                Path(filename).name,
                imported,
                duplicates,
                rejected,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return {"imported": imported, "duplicates": duplicates, "rejected": rejected}


def rule_matches(rule, transaction):
    candidate = transaction[rule["field"]].casefold()
    value = rule["value"].strip().casefold()
    if not value:
        return False
    if rule["operator"] == "contains":
        return value in candidate
    if rule["operator"] == "equals":
        return value == candidate
    return candidate.startswith(value)


def normalize_duplicate_text(value):
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(normalized.split())


def find_near_duplicates(transactions):
    buckets = defaultdict(list)
    for transaction in transactions:
        transaction["duplicate_of"] = None
        transaction["duplicate_difference_cents"] = 0
        key = (
            transaction["booked_on"],
            transaction["value_on"],
            normalize_duplicate_text(transaction["counterparty"]),
            normalize_duplicate_text(transaction["details"]),
            normalize_duplicate_text(transaction["currency"]),
            transaction["amount_cents"] < 0,
        )
        buckets[key].append(transaction)

    duplicate_groups = []
    for candidates in buckets.values():
        candidates.sort(key=lambda transaction: transaction.get("record_id", 0))
        representatives = []
        groups_by_fingerprint = {}
        for transaction in candidates:
            representative = next(
                (
                    candidate
                    for candidate in representatives
                    if abs(candidate["amount_cents"] - transaction["amount_cents"])
                    <= DUPLICATE_AMOUNT_TOLERANCE_CENTS
                ),
                None,
            )
            if representative is None:
                representatives.append(transaction)
                continue
            difference = abs(representative["amount_cents"] - transaction["amount_cents"])
            transaction["duplicate_of"] = representative["fingerprint"]
            transaction["duplicate_difference_cents"] = difference
            group = groups_by_fingerprint.get(representative["fingerprint"])
            if group is None:
                group = {"representative": representative, "duplicates": []}
                groups_by_fingerprint[representative["fingerprint"]] = group
                duplicate_groups.append(group)
            group["duplicates"].append(transaction)
    return duplicate_groups


def get_data(database_path=DATABASE_PATH):
    with connect(database_path) as db:
        categories = [dict(row) for row in db.execute("SELECT * FROM categories ORDER BY id")]
        rules = [
            dict(row)
            for row in db.execute(
                """SELECT rules.*, categories.name AS category, categories.color
                   FROM rules JOIN categories ON categories.id = rules.category_id
                   ORDER BY rules.updated_at DESC, rules.id DESC"""
            )
        ]
        transactions = [
            dict(row)
            for row in db.execute(
                """SELECT rowid AS record_id, fingerprint, booked_on, value_on, counterparty, tx_type,
                          details, amount_cents, currency
                   FROM transactions ORDER BY booked_on DESC, rowid DESC"""
            )
        ]
        imports = [
            dict(row)
            for row in db.execute(
                """SELECT filename, imported_count, duplicate_count, rejected_count,
                          imported_at FROM import_runs ORDER BY id DESC LIMIT 10"""
            )
        ]

    for transaction in transactions:
        transaction["category"] = "Unsorted"
        transaction["category_color"] = "#777b77"
        for rule in rules:
            if rule_matches(rule, transaction):
                transaction["category"] = rule["category"]
                transaction["category_color"] = rule["color"]
                break
    duplicate_groups = find_near_duplicates(transactions)
    for transaction in transactions:
        transaction.pop("record_id", None)
    return {
        "transactions": transactions,
        "categories": categories,
        "rules": rules,
        "imports": imports,
        "duplicates": duplicate_groups,
    }


def validate_rule(payload):
    field = payload.get("field")
    operator = payload.get("operator")
    value = str(payload.get("value", "")).strip()
    if field not in RULE_FIELDS or operator not in RULE_OPERATORS or not value:
        raise ValueError("Choose a field, match type, and non-empty filter value")
    return field, operator, value


def export_rules(database_path=DATABASE_PATH):
    with connect(database_path) as db:
        categories = [
            {"name": row["name"], "color": row["color"]}
            for row in db.execute("SELECT name, color FROM categories ORDER BY id")
        ]
        rules = [
            {
                "category": row["category"],
                "field": row["field"],
                "operator": row["operator"],
                "value": row["value"],
            }
            for row in db.execute(
                """SELECT categories.name AS category, rules.field, rules.operator, rules.value
                   FROM rules JOIN categories ON categories.id = rules.category_id
                   ORDER BY rules.updated_at DESC, rules.id DESC"""
            )
        ]
    return {"version": 1, "categories": categories, "rules": rules}


def import_rules(payload, database_path=DATABASE_PATH):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unsupported filter file version")
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        raise ValueError("Filter file must contain a rules list")
    raw_categories = payload.get("categories", [])
    if not isinstance(raw_categories, list):
        raise ValueError("Filter file categories must be a list")

    validated_categories = []
    for category in raw_categories:
        if not isinstance(category, dict):
            raise ValueError("Each category must be an object")
        validated_categories.append(validate_category(category))

    validated = []
    for rule in raw_rules:
        if not isinstance(rule, dict):
            raise ValueError("Each filter must be an object")
        field, operator, value = validate_rule(rule)
        category = rule.get("category")
        if not isinstance(category, str) or not category.strip():
            raise ValueError("Each filter must have a category")
        validated.append((category.strip(), field, operator, value))

    imported = duplicates = 0
    with connect(database_path) as db:
        category_ids = {
            row["name"].casefold(): row["id"]
            for row in db.execute("SELECT id, name FROM categories")
        }
        categories_to_add = {}
        for name, color in validated_categories:
            if name.casefold() not in category_ids:
                categories_to_add.setdefault(name.casefold(), (name, color))
        if any(category.casefold() not in category_ids and category.casefold() not in categories_to_add for category, *_ in validated):
            raise ValueError("Filter file includes an unknown category")
        for key, (name, color) in categories_to_add.items():
            cursor = db.execute(
                "INSERT INTO categories(name, color) VALUES (?, ?)", (name, color)
            )
            category_ids[key] = cursor.lastrowid
        existing = {
            (row["category"], row["field"], row["operator"], row["value"].casefold())
            for row in db.execute(
                """SELECT categories.name AS category, rules.field, rules.operator, rules.value
                   FROM rules JOIN categories ON categories.id = rules.category_id"""
            )
        }
        to_insert = []
        for rule in validated:
            key = (*rule[:3], rule[3].casefold())
            if key in existing:
                duplicates += 1
            else:
                existing.add(key)
                to_insert.append(rule)
        now = datetime.now(timezone.utc).isoformat()
        for category, field, operator, value in reversed(to_insert):
            db.execute(
                """INSERT INTO rules
                   (category_id, field, operator, value, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                     (category_ids[category.casefold()], field, operator, value, now, now),
            )
            imported += 1
    return {"imported": imported, "duplicates": duplicates}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload, download=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 25_000_000:
            raise ValueError("Request is too large")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/rules/export":
            return self.send_json(200, export_rules(), "ledger-filters.json")
        if path == "/api/data":
            return self.send_json(200, get_data())
        if path == "/" or path.startswith("/static/"):
            name = "index.html" if path == "/" else Path(unquote(path)).name
            file_path = STATIC_DIR / name
            if file_path.is_file() and file_path.parent == STATIC_DIR:
                content = file_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8" if name.endswith(".html") else "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/import":
                content = payload.get("content", "")
                if not content.strip():
                    raise ValueError("The selected file is empty")
                result = import_csv(content, payload.get("name", "upload.csv"))
                return self.send_json(200, result)
            if path == "/api/rules":
                field, operator, value = validate_rule(payload)
                now = datetime.now(timezone.utc).isoformat()
                with connect() as db:
                    category = db.execute(
                        "SELECT id FROM categories WHERE name = ?", (payload.get("category"),)
                    ).fetchone()
                    if not category:
                        raise ValueError("Unknown category")
                    cursor = db.execute(
                        """INSERT INTO rules
                        (category_id, field, operator, value, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (category["id"], field, operator, value, now, now),
                    )
                    return self.send_json(201, {"id": cursor.lastrowid})
            if path == "/api/rules/import":
                return self.send_json(200, import_rules(payload))
            if path == "/api/categories":
                category_id = create_category(payload)
                return self.send_json(201, {"id": category_id})
        except (ValueError, json.JSONDecodeError) as error:
            return self.send_json(400, {"error": str(error)})
        self.send_json(404, {"error": "Not found"})

    def do_PUT(self):
        path = urlparse(self.path).path
        if path.startswith("/api/categories/"):
            try:
                category_id = int(path.rsplit("/", 1)[1])
                payload = self.read_json()
                update_category(category_id, payload)
                return self.send_json(200, {"ok": True})
            except (ValueError, json.JSONDecodeError) as error:
                return self.send_json(400, {"error": str(error)})
        if not path.startswith("/api/rules/"):
            return self.send_json(404, {"error": "Not found"})
        try:
            rule_id = int(path.rsplit("/", 1)[1])
            payload = self.read_json()
            field, operator, value = validate_rule(payload)
            now = datetime.now(timezone.utc).isoformat()
            with connect() as db:
                category = db.execute(
                    "SELECT id FROM categories WHERE name = ?", (payload.get("category"),)
                ).fetchone()
                if not category:
                    raise ValueError("Unknown category")
                cursor = db.execute(
                    """UPDATE rules SET category_id = ?, field = ?, operator = ?, value = ?,
                       updated_at = ? WHERE id = ?""",
                    (category["id"], field, operator, value, now, rule_id),
                )
                if not cursor.rowcount:
                    return self.send_json(404, {"error": "Rule not found"})
            return self.send_json(200, {"ok": True})
        except (ValueError, json.JSONDecodeError) as error:
            return self.send_json(400, {"error": str(error)})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/rules/"):
            return self.send_json(404, {"error": "Not found"})
        try:
            rule_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            return self.send_json(400, {"error": "Invalid rule id"})
        with connect() as db:
            cursor = db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        self.send_json(200 if cursor.rowcount else 404, {"ok": bool(cursor.rowcount)})

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))


def main():
    initialize_database()
    if IMPORT_DIR.is_dir():
        for csv_path in sorted(IMPORT_DIR.glob("*.csv")):
            result = import_csv(csv_path.read_bytes(), csv_path.name)
            print(f"Imported {csv_path.name}: {result}")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Listening on 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()