"""
Final step: generate the user-friendly summary of a document, grounded in
what the knowledge graph actually knows (not just what the LLM guesses from
the text alone). This is the GraphRAG step — graph context is retrieved
first, then handed to Claude alongside the raw document.
"""
import json

import anthropic

import config
from queries import GraphQueries

SUMMARIZER_SYSTEM_PROMPT = """You are a finance and operations assistant that helps me quickly \
understand expense reports, finance/ops emails, and invoices. Always pull out the key facts \
(amounts, dates, vendors/parties, categories, and any deadlines or action items) and present them \
in a clear, easy-to-scan way — plain language, no unnecessary jargon.

For any invoice or payment, always state its status relative to today in plain terms (e.g. "overdue \
by X days," "due in X days," "already paid"), never just the raw due date. Always flag anything \
unusual, missing, or that needs my attention — but don't flag routine or already-resolved items. \
Never assume missing information — if a document is incomplete or unclear, say so rather than \
guessing.

You will be given the raw document AND a block of verified graph context (facts already confirmed \
by the knowledge graph, such as duplicate detections, overdue status, and vendor spend totals). \
Treat the graph context as ground truth — it is more reliable than re-deriving these facts yourself \
from the text. Weave it in naturally rather than listing it separately.

Respond with a short plain-language summary first, followed by key details in a simple bulleted or \
lightly tabular format if it helps clarity.
"""


def summarize(document_text: str, source_id: str, graph_queries: GraphQueries = None) -> str:
    """
    Produce the final user-facing summary for one document, using both the
    raw text and graph-verified context (duplicates, overdue status, etc.)
    """
    owns_queries = graph_queries is None
    graph_queries = graph_queries or GraphQueries()
    try:
        context = graph_queries.document_context(source_id)
        overdue = graph_queries.overdue_invoices()
        flags = graph_queries.open_flags()
    finally:
        if owns_queries:
            graph_queries.close()

    graph_context_block = json.dumps(
        {
            "document_entities": context.get("entities", []),
            "document_flags": context.get("flags", []),
            "all_overdue_invoices_in_graph": overdue,
            "all_open_flags_in_graph": flags,
        },
        indent=2,
        default=str,
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1000,
        system=SUMMARIZER_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"RAW DOCUMENT:\n{document_text}\n\n"
                    f"VERIFIED GRAPH CONTEXT:\n{graph_context_block}"
                ),
            }
        ],
    )
    return "".join(block.text for block in response.content if block.type == "text")
