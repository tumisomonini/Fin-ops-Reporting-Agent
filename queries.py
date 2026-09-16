"""
Read-only Cypher queries that give the summarizer graph-grounded context:
overdue invoices, vendor spend rollups, duplicate flags, and large
transactions. This is the "GraphRAG" retrieval step — the LLM only sees
context that the graph has actually verified, rather than re-deriving it
from scratch each time.
"""
from datetime import date

from neo4j import GraphDatabase

import config


class GraphQueries:
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

    def overdue_invoices(self, as_of: str = None) -> list[dict]:
        as_of = as_of or date.today().isoformat()
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (i:Invoice)-[:BILLED_BY]->(v:Vendor)
                WHERE i.due_date IS NOT NULL AND i.due_date < $as_of
                  AND coalesce(i.status, '') <> 'paid'
                RETURN i.invoice_id AS invoice_id, v.name AS vendor,
                       i.amount AS amount, i.currency AS currency,
                       i.due_date AS due_date,
                       duration.between(date(i.due_date), date($as_of)).days AS days_overdue
                ORDER BY days_overdue DESC
                """,
                as_of=as_of,
            )
            return [dict(record) for record in result]

    def vendor_spend_rollup(self) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (v:Vendor)
                OPTIONAL MATCH (i:Invoice)-[:BILLED_BY]->(v)
                OPTIONAL MATCH (t:Transaction)-[:PAID_TO]->(v)
                WITH v,
                     coalesce(sum(DISTINCT i.amount), 0) AS invoice_total,
                     coalesce(sum(DISTINCT t.amount), 0) AS transaction_total
                RETURN v.name AS vendor,
                       invoice_total + transaction_total AS total_spend
                ORDER BY total_spend DESC
                """
            )
            return [dict(record) for record in result]

    def open_flags(self) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (f:Flag)
                OPTIONAL MATCH (f)-[:CONCERNS]->(entity)
                RETURN f.type AS type, f.description AS description,
                       labels(entity) AS entity_labels,
                       coalesce(entity.invoice_id, entity.name, entity.id) AS entity_ref
                ORDER BY f.type
                """
            )
            return [dict(record) for record in result]

    def large_transactions(self, threshold: float = None) -> list[dict]:
        threshold = threshold if threshold is not None else config.LARGE_TRANSACTION_ZAR
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (t:Transaction)
                WHERE t.amount >= $threshold
                OPTIONAL MATCH (t)-[:PAID_TO]->(v:Vendor)
                RETURN t.id AS id, t.description AS description, t.amount AS amount,
                       t.currency AS currency, t.date AS date, v.name AS vendor
                ORDER BY t.amount DESC
                """,
                threshold=threshold,
            )
            return [dict(record) for record in result]

    def document_context(self, source_id: str) -> dict:
        """Everything the graph knows that was mentioned in one specific document,
        including context-layer nodes (people, topics, time period, thread)."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (d:Document {source_id: $source_id})
                OPTIONAL MATCH (d)-[:MENTIONS]->(entity)
                OPTIONAL MATCH (d)-[:RAISED]->(f:Flag)
                OPTIONAL MATCH (d)-[:AUTHORED_BY]->(author:Person)
                OPTIONAL MATCH (d)-[:REFERENCES]->(ref:Person)
                OPTIONAL MATCH (d)-[:TAGGED]->(topic:Topic)
                OPTIONAL MATCH (d)-[:COVERS]->(tp:TimePeriod)
                RETURN d.document_type AS document_type,
                       collect(DISTINCT {labels: labels(entity), props: properties(entity)}) AS entities,
                       collect(DISTINCT properties(f)) AS flags,
                       collect(DISTINCT author.name) AS authors,
                       collect(DISTINCT ref.name) AS references,
                       collect(DISTINCT topic.name) AS topics,
                       tp.label AS time_period
                """,
                source_id=source_id,
            )
            record = result.single()
            return dict(record) if record else {}

    def related_documents(self, source_id: str) -> list[dict]:
        """Documents sharing a vendor with source_id, with shared vendor names."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (d:Document {source_id: $source_id})-[:MENTIONS]->(v:Vendor)
                MATCH (other:Document)-[:MENTIONS]->(v)
                WHERE other.source_id <> $source_id
                RETURN other.source_id AS source_id,
                       other.document_type AS document_type,
                       other.ingested_on AS ingested_on,
                       collect(DISTINCT v.name) AS shared_vendors
                ORDER BY other.ingested_on DESC
                """,
                source_id=source_id,
            )
            return [dict(r) for r in result]
