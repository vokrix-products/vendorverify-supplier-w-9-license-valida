import json

from processor import process_bytes, process_file


DEMO_CSV = """Supplier Legal Name,DBA/Trade Name,Taxpayer ID (EIN/SSN),Tax Classification,License Number,License Type,License State,License Expiration,Contact Email,Notes
Acme Industrial Supply LLC,Acme Supply,12-3456789,LLC,BL-99213,General Business,CA,2027-04-30,ap@acme.example,clean record
Brightline Electric Inc,Brightline,98-7654321,C Corporation,EC-40188,Electrical Contractor,NV,2020-01-15,ops@brightline.example,license lapsed
Cobalt Mechanical Partners,Cobalt,45-6789012,Partnership,,,TX,2028-09-01,ar@cobalt.example,no license number on file
"""


def main() -> None:
    print("=== VendorVerify demo ===")
    records = process_bytes(DEMO_CSV, source="demo_suppliers.csv")
    assert isinstance(records, list), "process_bytes must return a list"
    assert records, "demo expected at least one record"
    for record in records:
        assert set(("title", "status", "details", "due_date")).issubset(record.keys())
        print(json.dumps(record, indent=2, default=str))
    print("=== %d record(s) extracted ===" % len(records))

    try:
        with open("demo_suppliers.csv", "w", encoding="utf-8") as handle:
            handle.write(DEMO_CSV)
        file_records = process_file("demo_suppliers.csv")
        assert isinstance(file_records, list)
        assert len(file_records) == len(records)
        print("process_file round-trip OK: %d record(s)" % len(file_records))
    except OSError:
        print("process_file skipped (filesystem not writable)")


if __name__ == "__main__":
    main()
