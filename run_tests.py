import unittest

import processor


VALID_CSV = (
    "Supplier Legal Name,Taxpayer ID,License Number,License Expiration\n"
    "Sunrise Fabrication LLC,12-3456789,BL-1001,2999-12-31\n"
)

EXPIRED_CSV = (
    "Supplier Legal Name,Taxpayer ID,License Number,License Expiration\n"
    "Brightline Electric Inc,98-7654321,EC-40188,2001-01-15\n"
)

MISSING_CSV = (
    "Supplier Legal Name,Taxpayer ID,License Number,License Expiration,Notes\n"
    ",,,,no W-9 or license on file\n"
)

FLAGGED_CSV = (
    "Supplier Legal Name,Taxpayer ID,License Number,License Expiration\n"
    "Cobalt Mechanical Partners,45-6789012,,2028-09-01\n"
)


class ProcessorTests(unittest.TestCase):
    def test_csv_extraction_returns_list(self):
        records = processor.process_bytes(VALID_CSV, source="valid.csv")
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 1)

    def test_csv_extraction_normalizes_fields(self):
        record = processor.process_bytes(VALID_CSV, source="valid.csv")[0]
        self.assertEqual(record["title"], "Sunrise Fabrication LLC")
        details = record["details"]
        self.assertEqual(details["supplier_name"], "Sunrise Fabrication LLC")
        self.assertEqual(details["license_number"], "BL-1001")
        self.assertEqual(details["expiration_date"], "2999-12-31")
        self.assertEqual(record["due_date"], "2999-12-31")

    def test_taxpayer_id_is_masked(self):
        record = processor.process_bytes(VALID_CSV, source="valid.csv")[0]
        self.assertEqual(record["details"]["taxpayer_id"], "*****6789")

    def test_status_valid(self):
        record = processor.process_bytes(VALID_CSV, source="valid.csv")[0]
        self.assertEqual(record["status"], processor.STATUS_VALID)
        self.assertIn(record["status"], processor.ALL_STATUSES)

    def test_status_expired(self):
        record = processor.process_bytes(EXPIRED_CSV, source="expired.csv")[0]
        self.assertEqual(record["status"], processor.STATUS_EXPIRED)

    def test_status_flagged_when_license_missing(self):
        record = processor.process_bytes(FLAGGED_CSV, source="flagged.csv")[0]
        self.assertEqual(record["status"], processor.STATUS_FLAGGED)

    def test_missing_data_fallback(self):
        records = processor.process_bytes(MISSING_CSV, source="missing.csv")
        self.assertIsInstance(records, list)
        self.assertTrue(records)
        for record in records:
            self.assertEqual(record["status"], processor.STATUS_MISSING)
            self.assertEqual(record["due_date"], "")
            self.assertEqual(record["title"], "missing.csv")

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(processor.process_bytes("", source="empty.csv"), [])
        self.assertEqual(processor.process_file("/nonexistent/path/does-not-exist.csv"), [])

    def test_detail_shape(self):
        record = processor.process_bytes(VALID_CSV, source="valid.csv")[0]
        self.assertEqual(
            set(record.keys()),
            {"title", "status", "details", "due_date"},
        )
        self.assertIsInstance(record["details"], dict)
        self.assertIn("reasons", record["details"])
        self.assertIn("source", record["details"])

    def test_unknown_status_is_coerced(self):
        self.assertEqual(processor._coerce_status("expired"), processor.STATUS_EXPIRED)
        self.assertEqual(processor._coerce_status("VALID"), processor.STATUS_VALID)
        self.assertEqual(processor._coerce_status("something odd"), processor.STATUS_FLAGGED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
