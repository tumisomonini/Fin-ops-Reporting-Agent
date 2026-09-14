"""
CLI entry point.

Usage:
    python main.py setup                     # apply Neo4j schema (run once)
    python main.py ingest <folder>            # extract + load every .txt file in a folder
    python main.py summarize <file>           # ingest one file and print its summary
    python main.py report                     # print overdue invoices, flags, and vendor spend
"""
import sys
from pathlib import Path

import config
import schema
from extraction import extract
from graph_builder import GraphBuilder
from queries import GraphQueries
from summarizer import summarize


def cmd_setup():
    schema.apply_schema()


def cmd_ingest(folder: str):
    paths = sorted(Path(folder).glob("*.txt"))
    if not paths:
        print(f"No .txt files found in {folder}")
        return
    with GraphBuilder() as builder:
        for path in paths:
            text = path.read_text()
            print(f"Extracting {path.name} ...")
            extraction = extract(text)
            builder.load_extraction(extraction, text)
            print(f"  -> loaded as document {extraction['source_id']} "
                  f"({extraction.get('document_type', 'other')})")


def cmd_summarize(file_path: str):
    text = Path(file_path).read_text()
    extraction = extract(text)
    with GraphBuilder() as builder:
        builder.load_extraction(extraction, text)
    print("\n--- SUMMARY ---\n")
    print(summarize(text, extraction["source_id"]))


def cmd_report():
    with GraphQueries() as q:
        print("\n=== Overdue Invoices ===")
        for row in q.overdue_invoices():
            print(f"  {row['invoice_id']} — {row['vendor']} — "
                  f"{row['currency']} {row['amount']} — {row['days_overdue']} days overdue")

        print("\n=== Open Flags ===")
        for row in q.open_flags():
            print(f"  [{row['type']}] {row['description']} "
                  f"(re: {row['entity_ref']})")

        print("\n=== Vendor Spend Rollup ===")
        for row in q.vendor_spend_rollup():
            print(f"  {row['vendor']}: {row['total_spend']}")


if __name__ == "__main__":
    config.require_config()

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    if command == "setup":
        cmd_setup()
    elif command == "ingest" and len(sys.argv) == 3:
        cmd_ingest(sys.argv[2])
    elif command == "summarize" and len(sys.argv) == 3:
        cmd_summarize(sys.argv[2])
    elif command == "report":
        cmd_report()
    else:
        print(__doc__)
        sys.exit(1)
