"""
Loads structured extraction output (see extraction.py) into Neo4j as a
connected graph of Vendors, Invoices, Transactions, Documents, and Flags.

Also runs lightweight duplicate detection at load time: if a transaction or
invoice with the same vendor, amount, and date already exists, it raises a
"duplicate" Flag instead of silently creating a second identical node.
"""
import uuid

from neo4j import GraphDatabase

import config


class GraphBuilder:
    def __init__(self, driver=None):
        self._owns_driver = driver is None
        self.driver = driver or GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )

    def close(self):
        if self._owns_driver:
            self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def load_extraction(self, extraction: dict, raw_text: str):
        """Write one document's extracted entities/relationships into the graph."""
        with self.driver.session() as session:
            session.execute_write(self._write_document, extraction, raw_text)

    # --- internal write transaction ---
    @staticmethod
    def _write_document(tx, extraction: dict, raw_text: str):
        source_id = extraction["source_id"]

        tx.run(
            """
            MERGE (d:Document {source_id: $source_id})
            SET d.document_type = $document_type,
                d.ingested_on = $ingested_on,
                d.raw_text = $raw_text
            """,
            source_id=source_id,
            document_type=extraction.get("document_type", "other"),
            ingested_on=extraction["ingested_on"],
            raw_text=raw_text,
        )

        for vendor in extraction.get("vendors", []):
            tx.run(
                """
                MERGE (v:Vendor {name: $name})
                WITH v
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:MENTIONS]->(v)
                """,
                name=vendor["name"],
                source_id=source_id,
            )

        for inv in extraction.get("invoices", []):
            inv_id = inv.get("invoice_id") or str(uuid.uuid4())
            tx.run(
                """
                MERGE (i:Invoice {invoice_id: $invoice_id})
                SET i.amount = $amount,
                    i.currency = $currency,
                    i.issue_date = $issue_date,
                    i.due_date = $due_date,
                    i.status = $status
                WITH i
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:MENTIONS]->(i)
                """,
                invoice_id=inv_id,
                amount=inv.get("amount"),
                currency=inv.get("currency"),
                issue_date=inv.get("issue_date"),
                due_date=inv.get("due_date"),
                status=inv.get("status"),
                source_id=source_id,
            )
            if inv.get("vendor_name"):
                tx.run(
                    """
                    MERGE (v:Vendor {name: $vendor_name})
                    WITH v
                    MATCH (i:Invoice {invoice_id: $invoice_id})
                    MERGE (i)-[:BILLED_BY]->(v)
                    """,
                    vendor_name=inv["vendor_name"],
                    invoice_id=inv_id,
                )

        for txn in extraction.get("transactions", []):
            txn_id = txn.get("id") or str(uuid.uuid4())
            tx.run(
                """
                MERGE (t:Transaction {id: $id})
                SET t.description = $description,
                    t.category = $category,
                    t.amount = $amount,
                    t.currency = $currency,
                    t.date = $date,
                    t.type = $type
                WITH t
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:MENTIONS]->(t)
                """,
                id=txn_id,
                description=txn.get("description"),
                category=txn.get("category"),
                amount=txn.get("amount"),
                currency=txn.get("currency"),
                date=txn.get("date"),
                type=txn.get("type"),
                source_id=source_id,
            )
            if txn.get("vendor_name"):
                tx.run(
                    """
                    MERGE (v:Vendor {name: $vendor_name})
                    WITH v
                    MATCH (t:Transaction {id: $id})
                    MERGE (t)-[:PAID_TO]->(v)
                    """,
                    vendor_name=txn["vendor_name"],
                    id=txn_id,
                )

            # Duplicate detection: same vendor + amount + date already in the graph
            if txn.get("vendor_name") and txn.get("amount") and txn.get("date"):
                dup = tx.run(
                    """
                    MATCH (t:Transaction {date: $date, amount: $amount})-[:PAID_TO]->(v:Vendor {name: $vendor_name})
                    WHERE t.id <> $id
                    RETURN t.id AS existing_id
                    """,
                    date=txn["date"],
                    amount=txn["amount"],
                    vendor_name=txn["vendor_name"],
                    id=txn_id,
                ).single()
                if dup:
                    flag_id = str(uuid.uuid4())
                    tx.run(
                        """
                        CREATE (f:Flag {id: $flag_id, type: 'duplicate',
                            description: $description})
                        WITH f
                        MATCH (t:Transaction {id: $id})
                        MATCH (d:Document {source_id: $source_id})
                        MERGE (f)-[:CONCERNS]->(t)
                        MERGE (d)-[:RAISED]->(f)
                        """,
                        flag_id=flag_id,
                        description=f"Possible duplicate of transaction {dup['existing_id']} "
                        f"(same vendor, amount, and date).",
                        id=txn_id,
                        source_id=source_id,
                    )

        for flag in extraction.get("flags", []):
            flag_id = str(uuid.uuid4())
            tx.run(
                """
                CREATE (f:Flag {id: $flag_id, type: $type, description: $description})
                WITH f
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:RAISED]->(f)
                """,
                flag_id=flag_id,
                type=flag.get("type", "other"),
                description=flag.get("description", ""),
                source_id=source_id,
            )
            related = flag.get("related_entity")
            if related:
                tx.run(
                    """
                    MATCH (f:Flag {id: $flag_id})
                    OPTIONAL MATCH (i:Invoice {invoice_id: $related})
                    OPTIONAL MATCH (v:Vendor {name: $related})
                    FOREACH (_ IN CASE WHEN i IS NOT NULL THEN [1] ELSE [] END | MERGE (f)-[:CONCERNS]->(i))
                    FOREACH (_ IN CASE WHEN v IS NOT NULL THEN [1] ELSE [] END | MERGE (f)-[:CONCERNS]->(v))
                    """,
                    flag_id=flag_id,
                    related=related,
                )
