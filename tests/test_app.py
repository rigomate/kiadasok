import tempfile
import unittest
from pathlib import Path

from app import (
    create_category,
    export_rules,
    export_categories,
    find_near_duplicates,
    import_csv,
    import_categories,
    import_rules,
    initialize_database,
    rule_matches,
    get_data,
    update_category,
)


SAMPLE = "15.06.2026;15.06.2026;VISA DM DROGERIE SAGT DANKE;Lastschrift;details;-5,80;EUR\n"


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.sqlite3"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_import_is_idempotent_and_preserves_cents(self):
        first = import_csv(SAMPLE, "sample.csv", self.database_path)
        second = import_csv(SAMPLE, "again.csv", self.database_path)
        transactions = get_data(self.database_path)["transactions"]
        self.assertEqual(first, {"imported": 1, "duplicates": 0, "rejected": 0})
        self.assertEqual(second, {"imported": 0, "duplicates": 1, "rejected": 0})
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["amount_cents"], -580)

    def test_rules_match_case_insensitively(self):
        rule = {"field": "counterparty", "operator": "contains", "value": "dm drogerie"}
        transaction = {"counterparty": "VISA DM DROGERIE SAGT DANKE"}
        self.assertTrue(rule_matches(rule, transaction))

    def test_malformed_row_is_rejected(self):
        result = import_csv(SAMPLE + "not;a;valid;transaction\n", "bad.csv", self.database_path)
        self.assertEqual(result["rejected"], 1)

    def test_ing_metadata_and_reference_column_import_and_deduplicate(self):
        direct_export = (
            "Umsatzanzeige;Datei erstellt am: 08.11.2026 21:19\n"
            "\nIBAN;DE00 0000 0000 0000 0000 00\n"
            "\nIn der CSV-Datei finden Sie alle bereits gebuchten Umsaetze.\n"
            "\nBuchung;Wertstellungsdatum;Auftraggeber/Empfänger;Buchungstext;"
            "Verwendungszweck;Referenz;Betrag;Währung\n"
            "15.06.2026;15.06.2026;VISA DM DROGERIE SAGT DANKE;Lastschrift;"
            "details;ref-123;-5,80;EUR\n"
        )
        first = import_csv(direct_export, "direct.csv", self.database_path)
        legacy = import_csv(SAMPLE, "legacy.csv", self.database_path)
        self.assertEqual(first, {"imported": 1, "duplicates": 0, "rejected": 0})
        self.assertEqual(legacy, {"imported": 0, "duplicates": 1, "rejected": 0})

    def test_ing_upload_with_replacement_character_headers_is_accepted(self):
        direct_export = (
            "Buchung;Wertstellungsdatum;Auftraggeber/Empf\ufffdnger;Buchungstext;"
            "Verwendungszweck;Referenz;Betrag;W\ufffdhrung\n"
            "15.06.2026;15.06.2026;Kaffeehaus;Lastschrift;Beleg;ref-1;-5,80;EUR\n"
        )
        result = import_csv(direct_export, "corrupted-header.csv", self.database_path)
        self.assertEqual(result, {"imported": 1, "duplicates": 0, "rejected": 0})

    def test_filter_export_import_is_portable_and_deduplicated(self):
        create_category(
            {"name": "Custom Shop", "color": "#ad6c76"}, self.database_path
        )
        filters = {
            "version": 1,
            "rules": [
                {"category": "Custom Shop", "field": "counterparty", "operator": "contains", "value": "DM"},
                {"category": "Dining", "field": "counterparty", "operator": "starts_with", "value": "CAFE"},
            ],
        }
        import_rules(filters, self.database_path)
        exported = export_rules(self.database_path)
        target_dir = tempfile.TemporaryDirectory()
        try:
            target_path = Path(target_dir.name) / "target.sqlite3"
            initialize_database(target_path)
            self.assertEqual(import_rules(exported, target_path), {"imported": 2, "duplicates": 0})
            self.assertEqual(import_rules(exported, target_path), {"imported": 0, "duplicates": 2})
            target_data = get_data(target_path)
            self.assertIn("Custom Shop", [category["name"] for category in target_data["categories"]])
            self.assertEqual([rule["category"] for rule in target_data["rules"]], ["Custom Shop", "Dining"])
        finally:
            target_dir.cleanup()

    def test_invalid_filter_import_is_atomic(self):
        payload = {
            "version": 1,
            "rules": [
                {"category": "Groceries", "field": "counterparty", "operator": "contains", "value": "DM"},
                {"category": "Missing", "field": "counterparty", "operator": "contains", "value": "X"},
            ],
        }
        with self.assertRaises(ValueError):
            import_rules(payload, self.database_path)
        self.assertEqual(get_data(self.database_path)["rules"], [])

    def test_category_rename_preserves_filters_across_restart(self):
        category_id = create_category(
            {"name": "Fresh Food", "color": "#527b55"}, self.database_path
        )
        import_rules(
            {
                "version": 1,
                "rules": [
                    {"category": "Fresh Food", "field": "counterparty", "operator": "contains", "value": "MARKET"}
                ],
            },
            self.database_path,
        )
        update_category(
            category_id, {"name": "Market", "color": "#c67644"}, self.database_path
        )
        initialize_database(self.database_path)
        data = get_data(self.database_path)
        self.assertIn("Market", [category["name"] for category in data["categories"]])
        self.assertNotIn("Fresh Food", [category["name"] for category in data["categories"]])
        self.assertEqual(export_rules(self.database_path)["rules"][0]["category"], "Market")

    def test_category_names_are_unique_case_insensitively(self):
        with self.assertRaises(ValueError):
            create_category(
                {"name": "groceries", "color": "#527b55"}, self.database_path
            )

    def test_category_json_round_trip_is_idempotent_and_updates_colors(self):
        create_category(
            {"name": "Coffee Shops", "color": "#c67644"}, self.database_path
        )
        exported = export_categories(self.database_path)
        target_dir = tempfile.TemporaryDirectory()
        try:
            target_path = Path(target_dir.name) / "target.sqlite3"
            initialize_database(target_path)
            self.assertEqual(
                import_categories(exported, target_path),
                {"added": 1, "updated": 0, "unchanged": 9},
            )
            self.assertEqual(
                import_categories(exported, target_path),
                {"added": 0, "updated": 0, "unchanged": 10},
            )
            recolored = {"version": 1, "categories": [{"name": "Coffee Shops", "color": "#123456"}]}
            self.assertEqual(
                import_categories(recolored, target_path),
                {"added": 0, "updated": 1, "unchanged": 0},
            )
            category = next(category for category in get_data(target_path)["categories"] if category["name"] == "Coffee Shops")
            self.assertEqual(category["color"], "#123456")
        finally:
            target_dir.cleanup()

    def test_invalid_category_json_is_atomic(self):
        payload = {
            "version": 1,
            "categories": [
                {"name": "New Group", "color": "#123456"},
                {"name": "Broken", "color": "red"},
            ],
        }
        with self.assertRaises(ValueError):
            import_categories(payload, self.database_path)
        self.assertNotIn("New Group", [category["name"] for category in get_data(self.database_path)["categories"]])

    def test_near_duplicates_allow_one_cent_but_not_two(self):
        transactions = [
            {
                "record_id": 1,
                "fingerprint": "first",
                "booked_on": "2025-12-17",
                "value_on": "2025-12-17",
                "counterparty": "Alexander Pavlov",
                "tx_type": "Echtzeitüberweisung",
                "details": "Koszi",
                "amount_cents": -272000,
                "currency": "EUR",
            },
            {
                "record_id": 2,
                "fingerprint": "second",
                "booked_on": "2025-12-17",
                "value_on": "2025-12-17",
                "counterparty": "Alexander Pavlov",
                "tx_type": "Echtzeit\ufffdberweisung",
                "details": "Koszi",
                "amount_cents": -272001,
                "currency": "EUR",
            },
            {
                "record_id": 3,
                "fingerprint": "third",
                "booked_on": "2025-12-17",
                "value_on": "2025-12-17",
                "counterparty": "Alexander Pavlov",
                "tx_type": "Echtzeitüberweisung",
                "details": "Koszi",
                "amount_cents": -272002,
                "currency": "EUR",
            },
        ]
        groups = find_near_duplicates(transactions)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["representative"]["fingerprint"], "first")
        self.assertEqual([row["fingerprint"] for row in groups[0]["duplicates"]], ["second"])
        self.assertIsNone(transactions[2]["duplicate_of"])


if __name__ == "__main__":
    unittest.main()