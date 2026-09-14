"""
Extraction layer: turns a raw finance/ops document (invoice, email, expense
log, etc.) into structured entities and relationships ready to load into
Neo4j.

Uses Claude with a strict JSON-only response so the output can be parsed
directly, no free text around it.
"""
import json
import uuid
from datetime import date

import anthropic

import config

EXTRACTION_SYSTEM_PROMPT = """You extract structured financial/operational entities from a document \
(an invoice, expense report, transaction log, or finance/ops email) for loading into a knowledge graph.

Respond with ONLY a single JSON object — no preamble, no markdown fences, no explanation. If a field is not \
present in the document, omit it rather than guessing. Never invent vendor names, amounts, or dates that \
are not in the text.

JSON schema:
{
  "document_type": "invoice" | "email" | "expense_log" | "other",
  "vendors": [ { "name": str } ],
  "invoices": [
    {
      "invoice_id": str,
      "vendor_name": str,
      "amount": number,
      "currency": str,          // e.g. "ZAR", "USD" — omit if unclear
      "issue_date": str,        // ISO 8601 if determinable, else omit
      "due_date": str,          // ISO 8601 if determinable, else omit
      "status": str             // e.g. "unpaid", "paid", "overdue" if stated
    }
  ],
  "transactions": [
    {
      "description": str,
      "vendor_name": str,       // omit if not tied to a vendor
      "category": str,
      "amount": number,
      "currency": str,
      "date": str,              // ISO 8601 if determinable
      "type": "expense" | "income"
    }
  ],
  "flags": [
    {
      "type": "overdue" | "budget_overrun" | "duplicate" | "missing_info" | "large_anomaly" | "other",
      "description": str,
      "related_entity": str     // e.g. an invoice_id or vendor name this flag concerns
    }
  ]
}
"""


def extract(document_text: str) -> dict:
    """
    Call Claude to extract structured data from a raw document.
    Returns a dict matching the schema above. Adds a generated
    `source_id` so the caller can trace graph nodes back to this document.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": document_text}],
    )

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    # Defensive cleanup in case the model wraps the JSON in fences anyway
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Extraction did not return valid JSON.\nRaw output:\n{raw_text}"
        ) from e

    parsed["source_id"] = str(uuid.uuid4())
    parsed["ingested_on"] = date.today().isoformat()
    return parsed
