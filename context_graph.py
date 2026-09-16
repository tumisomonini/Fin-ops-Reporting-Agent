"""
Context graph layer — enriches the knowledge graph with *who*, *when*, and
*what topic* context that the entity graph alone cannot answer.

New node labels
---------------
  Person(name)                  — authors and recipients extracted from documents
  Topic(name)                   — finance/ops topic tags (e.g. "overdue", "budget_overrun")
  TimePeriod(label, year, quarter, month)  — calendar buckets derived from document dates

New relationships
-----------------
  (Document)-[:AUTHORED_BY]->(Person)
  (Document)-[:REFERENCES]->(Person)
  (Document)-[:COVERS]->(TimePeriod)
  (Document)-[:TAGGED]->(Topic)
  (Document)-[:REPLIES_TO]->(Document)   — email thread linkage (same subject, different doc)
  (Document)-[:SHARES_VENDOR]->(Document) — two docs that mention the same vendor

Context queries (ContextQueries class)
---------------------------------------
  document_thread(source_id)     — full reply chain for a document
  documents_by_person(name)      — all docs authored or referencing a person
  documents_by_topic(topic)      — all docs tagged with a topic
  documents_by_period(label)     — all docs in a time bucket
  related_documents(source_id)   — docs sharing a vendor with this one
  context_summary()              — aggregate view: topics × period × person counts
"""
import re
import uuid
from datetime import date, datetime
from typing import Optional

from neo4j import GraphDatabase

import config

# ---------------------------------------------------------------------------
# Topic taxonomy — map flag types and keywords to canonical topic tags
# ---------------------------------------------------------------------------
_KEYWORD_TOPICS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\boverdue\b|\bpast.?due\b|\bdays? overdue\b", re.I), "overdue"),
    (re.compile(r"\bduplicate\b|\bduplicated\b", re.I), "duplicate"),
    (re.compile(r"\bbudget.?overrun\b|\bover.?budget\b|\bover the.{0,10}budget\b", re.I), "budget_overrun"),
    (re.compile(r"\bmissing.?info\b|\bincomplete\b|\bnot.?stated\b", re.I), "missing_info"),
    (re.compile(r"\banomaly\b|\bunusual\b|\blarge.{0,10}transaction\b", re.I), "large_anomaly"),
    (re.compile(r"\bcurrency\b|\bfx\b|\bexchange.?rate\b", re.I), "currency_issue"),
    (re.compile(r"\breconcil\b|\bpetty.?cash\b", re.I), "reconciliation"),
    (re.compile(r"\bpayment\b|\bpaid\b|\bsettle\b", re.I), "payment"),
    (re.compile(r"\binvoice\b|\binv-\w+\b", re.I), "invoice"),
    (re.compile(r"\bexpense\b|\bspend\b|\bspending\b", re.I), "expense"),
    (re.compile(r"\bdeadline\b|\bdue.?date\b|\bby.{0,10}friday\b|\bend.?of.?week\b", re.I), "deadline"),
    (re.compile(r"\bvendor\b|\bsupplier\b", re.I), "vendor"),
]

_FLAG_TYPE_TOPICS = {
    "overdue": "overdue",
    "duplicate": "duplicate",
    "budget_overrun": "budget_overrun",
    "missing_info": "missing_info",
    "large_anomaly": "large_anomaly",
    "other": "other",
}


def topics_from_text(text: str, flags: list[dict]) -> set[str]:
    """Derive topic tags from raw text and extracted flags."""
    topics: set[str] = set()
    for pattern, tag in _KEYWORD_TOPICS:
        if pattern.search(text):
            topics.add(tag)
    for flag in flags:
        ft = flag.get("type", "")
        if ft in _FLAG_TYPE_TOPICS:
            topics.add(_FLAG_TYPE_TOPICS[ft])
    return topics


# ---------------------------------------------------------------------------
# Person extraction — pull names from common email header patterns
# ---------------------------------------------------------------------------
_FROM_RE = re.compile(r"^From:\s*(.+?)(?:\s*<[^>]+>)?\s*$", re.M)
_TO_RE = re.compile(r"^To:\s*(.+?)(?:\s*<[^>]+>)?\s*$", re.M)
_SIGNED_RE = re.compile(r"(?:^|\n)(?:Thanks|Regards|Cheers|Best),?\s*\n([A-Z][a-z]+(?: [A-Z][a-z.]+)?)", re.M)
_HI_RE = re.compile(r"^(?:Hi|Hello|Hey|Dear)\s+([A-Z][a-z]+(?: [A-Z][a-z.]+)?)[,\s]", re.M)


def _clean_name(raw: str) -> str:
    return raw.strip().strip('"').strip("'")


def extract_people(text: str) -> dict[str, list[str]]:
    """
    Returns {"authors": [...], "recipients": [...]} extracted from email headers
    and sign-off patterns.  Names are title-cased and de-duplicated.
    """
    authors: list[str] = []
    recipients: list[str] = []

    for m in _FROM_RE.finditer(text):
        name = _clean_name(m.group(1))
        if name:
            authors.append(name.title())

    for m in _TO_RE.finditer(text):
        name = _clean_name(m.group(1))
        if name:
            recipients.append(name.title())

    for m in _SIGNED_RE.finditer(text):
        name = _clean_name(m.group(1))
        if name and name not in authors:
            authors.append(name.title())

    for m in _HI_RE.finditer(text):
        name = _clean_name(m.group(1))
        if name and name not in recipients:
            recipients.append(name.title())

    return {
        "authors": list(dict.fromkeys(authors)),
        "recipients": list(dict.fromkeys(recipients)),
    }


# ---------------------------------------------------------------------------
# Time period derivation
# ---------------------------------------------------------------------------

def time_period_from_date(iso_date: Optional[str]) -> Optional[dict]:
    """
    Given an ISO-8601 date string (or None), return a TimePeriod dict:
      { label, year, quarter, month }
    """
    if not iso_date:
        return None
    try:
        d = date.fromisoformat(iso_date[:10])
    except ValueError:
        return None
    q = (d.month - 1) // 3 + 1
    label = f"Q{q} {d.year}"
    return {"label": label, "year": d.year, "quarter": q, "month": d.month}


def _earliest_date(extraction: dict) -> Optional[str]:
    """Pick the earliest meaningful date from the extraction."""
    candidates: list[str] = []
    for inv in extraction.get("invoices", []):
        for key in ("issue_date", "due_date"):
            if inv.get(key):
                candidates.append(inv[key])
    for txn in extraction.get("transactions", []):
        if txn.get("date"):
            candidates.append(txn["date"])
    if not candidates:
        return extraction.get("ingested_on")
    return min(candidates)


# ---------------------------------------------------------------------------
# Subject normalisation for thread detection
# ---------------------------------------------------------------------------
_SUBJECT_RE = re.compile(r"^Subject:\s*(.+)$", re.M)
_THREAD_PREFIX_RE = re.compile(r"^(?:re|fwd?|fw):\s*", re.I)


def _normalise_subject(text: str) -> Optional[str]:
    m = _SUBJECT_RE.search(text)
    if not m:
        return None
    subject = m.group(1).strip()
    subject = _THREAD_PREFIX_RE.sub("", subject).strip().lower()
    return subject or None


# ---------------------------------------------------------------------------
# ContextBuilder — writes context nodes/relationships into Neo4j
# ---------------------------------------------------------------------------

class ContextBuilder:
    """
    Writes context-layer nodes and relationships for one document.
    Designed to be called *after* GraphBuilder.load_extraction() so the
    Document node already exists.

    Usage (inside a GraphBuilder transaction block or standalone):
        with ContextBuilder() as ctx:
            ctx.load_context(extraction, raw_text)
    """

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

    def load_context(self, extraction: dict, raw_text: str) -> None:
        source_id = extraction["source_id"]
        people = extract_people(raw_text)
        topics = topics_from_text(raw_text, extraction.get("flags", []))
        period = time_period_from_date(_earliest_date(extraction))
        subject = _normalise_subject(raw_text)

        with self.driver.session() as session:
            session.execute_write(
                self._write_context,
                source_id, people, list(topics), period, subject,
            )

    @staticmethod
    def _write_context(
        tx,
        source_id: str,
        people: dict,
        topics: list[str],
        period: Optional[dict],
        subject: Optional[str],
    ) -> None:
        # --- Person nodes ---
        for name in people["authors"]:
            tx.run(
                """
                MERGE (p:Person {name: $name})
                WITH p
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:AUTHORED_BY]->(p)
                """,
                name=name, source_id=source_id,
            )
        for name in people["recipients"]:
            tx.run(
                """
                MERGE (p:Person {name: $name})
                WITH p
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:REFERENCES]->(p)
                """,
                name=name, source_id=source_id,
            )

        # --- Topic nodes ---
        for topic in topics:
            tx.run(
                """
                MERGE (t:Topic {name: $name})
                WITH t
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:TAGGED]->(t)
                """,
                name=topic, source_id=source_id,
            )

        # --- TimePeriod node ---
        if period:
            tx.run(
                """
                MERGE (tp:TimePeriod {label: $label})
                SET tp.year = $year, tp.quarter = $quarter, tp.month = $month
                WITH tp
                MATCH (d:Document {source_id: $source_id})
                MERGE (d)-[:COVERS]->(tp)
                """,
                label=period["label"], year=period["year"],
                quarter=period["quarter"], month=period["month"],
                source_id=source_id,
            )

        # --- Thread linkage: REPLIES_TO ---
        if subject:
            tx.run(
                """
                MATCH (d:Document {source_id: $source_id})
                SET d._subject = $subject
                WITH d
                MATCH (other:Document)
                WHERE other.source_id <> $source_id
                  AND other._subject = $subject
                MERGE (d)-[:REPLIES_TO]->(other)
                """,
                source_id=source_id, subject=subject,
            )

        # --- Shared-vendor linkage: SHARES_VENDOR ---
        tx.run(
            """
            MATCH (d:Document {source_id: $source_id})-[:MENTIONS]->(v:Vendor)
            MATCH (other:Document)-[:MENTIONS]->(v)
            WHERE other.source_id <> $source_id
            MERGE (d)-[:SHARES_VENDOR]->(other)
            """,
            source_id=source_id,
        )


# ---------------------------------------------------------------------------
# ContextQueries — read-only retrieval over the context layer
# ---------------------------------------------------------------------------

class ContextQueries:
    """
    Read-only queries over the context graph layer.

    Usage:
        with ContextQueries() as cq:
            thread = cq.document_thread(source_id)
            related = cq.related_documents(source_id)
    """

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

    def document_thread(self, source_id: str) -> list[dict]:
        """All documents in the same email thread as source_id."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (d:Document {source_id: $source_id})
                WHERE d._subject IS NOT NULL
                MATCH (other:Document)
                WHERE other._subject = d._subject
                RETURN other.source_id AS source_id,
                       other.document_type AS document_type,
                       other.ingested_on AS ingested_on
                ORDER BY other.ingested_on
                """,
                source_id=source_id,
            )
            return [dict(r) for r in result]

    def documents_by_person(self, name: str) -> list[dict]:
        """All documents authored by or referencing a person (case-insensitive)."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (p:Person)
                WHERE toLower(p.name) CONTAINS toLower($name)
                MATCH (d:Document)-[r:AUTHORED_BY|REFERENCES]->(p)
                RETURN d.source_id AS source_id,
                       d.document_type AS document_type,
                       d.ingested_on AS ingested_on,
                       type(r) AS relationship,
                       p.name AS person
                ORDER BY d.ingested_on DESC
                """,
                name=name,
            )
            return [dict(r) for r in result]

    def documents_by_topic(self, topic: str) -> list[dict]:
        """All documents tagged with a topic."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (d:Document)-[:TAGGED]->(t:Topic {name: $topic})
                RETURN d.source_id AS source_id,
                       d.document_type AS document_type,
                       d.ingested_on AS ingested_on
                ORDER BY d.ingested_on DESC
                """,
                topic=topic,
            )
            return [dict(r) for r in result]

    def documents_by_period(self, label: str) -> list[dict]:
        """All documents covering a time period (e.g. 'Q3 2024')."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (d:Document)-[:COVERS]->(tp:TimePeriod {label: $label})
                RETURN d.source_id AS source_id,
                       d.document_type AS document_type,
                       d.ingested_on AS ingested_on
                ORDER BY d.ingested_on DESC
                """,
                label=label,
            )
            return [dict(r) for r in result]

    def related_documents(self, source_id: str) -> list[dict]:
        """Documents that share at least one vendor with source_id."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (d:Document {source_id: $source_id})-[:SHARES_VENDOR]->(other:Document)
                MATCH (d)-[:MENTIONS]->(v:Vendor)<-[:MENTIONS]-(other)
                RETURN other.source_id AS source_id,
                       other.document_type AS document_type,
                       other.ingested_on AS ingested_on,
                       collect(DISTINCT v.name) AS shared_vendors
                ORDER BY other.ingested_on DESC
                """,
                source_id=source_id,
            )
            return [dict(r) for r in result]

    def context_summary(self) -> dict:
        """
        Aggregate counts across the context layer — useful for the standing
        report and for grounding the summarizer.
        """
        with self.driver.session() as session:
            topics = session.run(
                """
                MATCH (d:Document)-[:TAGGED]->(t:Topic)
                RETURN t.name AS topic, count(d) AS doc_count
                ORDER BY doc_count DESC
                """
            )
            periods = session.run(
                """
                MATCH (d:Document)-[:COVERS]->(tp:TimePeriod)
                RETURN tp.label AS period, tp.year AS year, tp.quarter AS quarter, count(d) AS doc_count
                ORDER BY year DESC, quarter DESC
                """
            )
            people = session.run(
                """
                MATCH (d:Document)-[r:AUTHORED_BY|REFERENCES]->(p:Person)
                RETURN p.name AS person, count(d) AS doc_count
                ORDER BY doc_count DESC
                LIMIT 20
                """
            )
            threads = session.run(
                """
                MATCH (d:Document)-[:REPLIES_TO]->(other:Document)
                RETURN d._subject AS subject, count(d) AS depth
                ORDER BY depth DESC
                LIMIT 10
                """
            )
            return {
                "topics": [dict(r) for r in topics],
                "periods": [dict(r) for r in periods],
                "people": [dict(r) for r in people],
                "threads": [dict(r) for r in threads],
            }
