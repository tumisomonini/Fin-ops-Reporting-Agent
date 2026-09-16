"""
Graph schema: constraints and indexes. Run once via `python schema.py`
or automatically the first time graph_builder connects.

Node labels:
  Vendor(name)
  Invoice(invoice_id, amount, currency, issue_date, due_date, status)
  Transaction(id, description, category, amount, currency, date, type)
  Document(source_id, document_type, ingested_on, raw_text)
  Flag(id, type, description)

Relationships:
  (Document)-[:MENTIONS]->(Vendor | Invoice | Transaction)
  (Invoice)-[:BILLED_BY]->(Vendor)
  (Transaction)-[:PAID_TO]->(Vendor)
  (Flag)-[:CONCERNS]->(Invoice | Vendor | Transaction)
  (Document)-[:RAISED]->(Flag)
"""
from neo4j import GraphDatabase

import config

CONSTRAINTS = [
    # --- knowledge graph ---
    "CREATE CONSTRAINT vendor_name IF NOT EXISTS FOR (v:Vendor) REQUIRE v.name IS UNIQUE",
    "CREATE CONSTRAINT invoice_id IF NOT EXISTS FOR (i:Invoice) REQUIRE i.invoice_id IS UNIQUE",
    "CREATE CONSTRAINT transaction_id IF NOT EXISTS FOR (t:Transaction) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT document_source_id IF NOT EXISTS FOR (d:Document) REQUIRE d.source_id IS UNIQUE",
    "CREATE CONSTRAINT flag_id IF NOT EXISTS FOR (f:Flag) REQUIRE f.id IS UNIQUE",
    # --- context graph ---
    "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT topic_name IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT time_period_label IF NOT EXISTS FOR (tp:TimePeriod) REQUIRE tp.label IS UNIQUE",
]


def apply_schema(driver=None):
    owns_driver = driver is None
    if owns_driver:
        driver = GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )
    try:
        with driver.session() as session:
            for statement in CONSTRAINTS:
                session.run(statement)
        print(f"Applied {len(CONSTRAINTS)} schema constraints.")
    finally:
        if owns_driver:
            driver.close()


if __name__ == "__main__":
    config.require_config()
    apply_schema()
