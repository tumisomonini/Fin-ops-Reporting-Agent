"""
Extraction layer: turns a raw finance/ops document (invoice, email, expense
log, etc.) into structured entities and relationships ready to load into
Neo4j.

Uses Claude with a strict JSON-only response so the output can be parsed
directly, no free text around it.

Now includes reasoning memory to track the extraction thought process.
"""
import json
import uuid
from datetime import date
from typing import Optional, Dict, Any

import anthropic

import config
from memory import get_memory_manager, ReasoningMemory

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


def extract(document_text: str, 
           reasoning_memory: Optional[ReasoningMemory] = None,
           enable_reasoning_trace: bool = False) -> dict:
    """
    Call Claude to extract structured data from a raw document.
    Returns a dict matching the schema above. Adds a generated
    `source_id` so the caller can trace graph nodes back to this document.
    
    Args:
        document_text: The raw document text to extract from
        reasoning_memory: Optional ReasoningMemory instance for tracing
        enable_reasoning_trace: Whether to capture detailed reasoning steps
        
    Returns:
        dict: Extracted data with source_id and ingested_on fields
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    
    # Generate a source_id first so we can reference it in reasoning
    source_id = str(uuid.uuid4())
    
    # Start reasoning trace if enabled
    trace_id = None
    if enable_reasoning_trace and reasoning_memory:
        trace_id = reasoning_memory.start_trace(
            "extraction", 
            document_text, 
            document_id=source_id
        )
        reasoning_memory.add_step(
            trace_id, 
            "Starting extraction - identifying document type and structure"
        )

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

    parsed["source_id"] = source_id
    parsed["ingested_on"] = date.today().isoformat()
    
    # Complete reasoning trace if enabled
    if trace_id and reasoning_memory:
        reasoning_memory.add_step(
            trace_id,
            "Successfully extracted structured data from document",
            {"vendors_count": len(parsed.get("vendors", [])),
             "invoices_count": len(parsed.get("invoices", [])),
             "transactions_count": len(parsed.get("transactions", [])),
             "flags_count": len(parsed.get("flags", []))}
        )
        reasoning_memory.complete_trace(
            trace_id,
            parsed,
            confidence=0.95  # High confidence in successful extraction
        )
    
    return parsed


def extract_with_reasoning(document_text: str) -> tuple[dict, str]:
    """
    Extract with full reasoning trace enabled.
    
    This is a convenience function that enables reasoning memory and returns
    both the extracted data and the trace ID for audit purposes.
    
    Args:
        document_text: The raw document text to extract from
        
    Returns:
        tuple: (extracted_data, trace_id)
    """
    memory_manager = get_memory_manager()
    result = extract(document_text, 
                    reasoning_memory=memory_manager.reasoning,
                    enable_reasoning_trace=True)
    
    # Find the trace for this document
    traces = memory_manager.reasoning.get_traces_by_document(result["source_id"])
    trace_id = traces[-1].id if traces else None
    
    return result, trace_id
