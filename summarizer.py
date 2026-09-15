"""
Final step: generate the user-friendly summary of a document, grounded in
what the knowledge graph actually knows (not just what the LLM guesses from
the text alone). This is the GraphRAG step — graph context is retrieved
first, then handed to Claude alongside the raw document.

Now includes:
- Conversation context from short-term memory
- Reasoning traces for summarization process
- Combined context for multi-turn interactions
"""
import json
from typing import Optional, List, Dict, Any

import anthropic

import config
from queries import GraphQueries
from memory import get_memory_manager, ConversationMemory, ReasoningMemory

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


def summarize(document_text: str, source_id: str, 
              graph_queries: GraphQueries = None,
              conversation_memory: Optional[ConversationMemory] = None,
              reasoning_memory: Optional[ReasoningMemory] = None,
              include_conversation_context: bool = False) -> str:
    """
    Produce the final user-facing summary for one document, using both the
    raw text and graph-verified context (duplicates, overdue status, etc.).
    
    Args:
        document_text: The raw document text to summarize
        source_id: The document's source_id from extraction
        graph_queries: Optional GraphQueries instance
        conversation_memory: Optional ConversationMemory for chat history
        reasoning_memory: Optional ReasoningMemory for reasoning traces
        include_conversation_context: Whether to include conversation history in prompt
        
    Returns:
        str: The generated summary
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
    
    # Build conversation context if enabled
    conversation_context = ""
    if include_conversation_context and conversation_memory:
        messages = conversation_memory.get_context(max_messages=5)
        if messages:
            conversation_context = "\n\n".join(
                [f"{msg['role'].upper()}: {msg['content']}" for msg in messages]
            )
            conversation_context = f"CONVERSATION HISTORY:\n{conversation_context}\n\n"
    
    # Start reasoning trace if enabled
    trace_id = None
    if reasoning_memory:
        trace_id = reasoning_memory.start_trace(
            "summarization",
            document_text,
            document_id=source_id
        )
        reasoning_memory.add_step(
            trace_id,
            "Retrieved graph context for document",
            {"entities_count": len(context.get("entities", [])),
             "flags_count": len(context.get("flags", []))}
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
                    f"{conversation_context}"
                    f"RAW DOCUMENT:\n{document_text}\n\n"
                    f"VERIFIED GRAPH CONTEXT:\n{graph_context_block}"
                ),
            }
        ],
    )
    
    summary = "".join(block.text for block in response.content if block.type == "text")
    
    # Complete reasoning trace if enabled
    if trace_id and reasoning_memory:
        reasoning_memory.add_step(
            trace_id,
            "Generated user-friendly summary",
            {"summary_length": len(summary), "summary_preview": summary[:200]}
        )
        reasoning_memory.complete_trace(
            trace_id,
            summary,
            confidence=0.92
        )
    
    return summary


def summarize_with_memory(document_text: str, source_id: str,
                           conversation_context: Optional[List[Dict]] = None) -> str:
    """
    Summarize with full memory integration.
    
    This is a convenience function that uses the global memory manager
    to include conversation context and capture reasoning traces.
    
    Args:
        document_text: The raw document text
        source_id: The document's source_id
        conversation_context: Optional list of previous messages
        
    Returns:
        str: The generated summary
    """
    memory_manager = get_memory_manager()
    
    # Add conversation context if provided
    if conversation_context:
        for msg in conversation_context:
            memory_manager.conversation.add_message(
                msg.get('role', 'user'),
                msg.get('content', ''),
                document_id=source_id
            )
    
    return summarize(
        document_text,
        source_id,
        conversation_memory=memory_manager.conversation,
        reasoning_memory=memory_manager.reasoning,
        include_conversation_context=True
    )
