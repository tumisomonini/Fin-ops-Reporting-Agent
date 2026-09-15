"""
CLI entry point.

Usage:
    python main.py setup                     # apply Neo4j schema (run once)
    python main.py ingest <folder>            # extract + load every .txt file in a folder
    python main.py summarize <file>           # ingest one file and print its summary
    python main.py report                     # print overdue invoices, flags, and vendor spend
    python main.py chat                       # start interactive chat session with memory
    python main.py memory report              # show memory statistics and recent traces
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import config
import schema
from extraction import extract, extract_with_reasoning
from graph_builder import GraphBuilder
from queries import GraphQueries
from summarizer import summarize, summarize_with_memory
from memory import (
    MemoryManager, ConversationMemory, ReasoningMemory, LongTermMemory,
    get_memory_manager, set_memory_manager, reset_memory_manager
)


def cmd_setup():
    schema.apply_schema()


def cmd_ingest(folder: str, enable_reasoning: bool = False):
    """
    Ingest documents from a folder with optional reasoning memory.
    
    Args:
        folder: Path to folder containing .txt files
        enable_reasoning: Whether to capture reasoning traces
    """
    paths = sorted(Path(folder).glob("*.txt"))
    if not paths:
        print(f"No .txt files found in {folder}")
        return
    
    # Initialize memory manager for this operation
    memory_manager = MemoryManager()
    set_memory_manager(memory_manager)
    
    try:
        with GraphBuilder() as builder:
            for path in paths:
                text = path.read_text()
                print(f"Extracting {path.name} ...")
                
                # Use reasoning-enabled extraction if requested
                if enable_reasoning:
                    extraction, trace_id = extract_with_reasoning(text)
                    print(f"  -> loaded as document {extraction['source_id']} "
                          f"({extraction.get('document_type', 'other')})")
                    print(f"  -> reasoning trace: {trace_id}")
                else:
                    extraction = extract(text)
                    print(f"  -> loaded as document {extraction['source_id']} "
                          f"({extraction.get('document_type', 'other')})")
                
                builder.load_extraction(extraction, text)
                
                # Store in long-term memory
                memory_manager.longterm.store_document(
                    extraction['source_id'],
                    text,
                    extraction
                )
    finally:
        reset_memory_manager()


def cmd_summarize(file_path: str, with_memory: bool = False):
    """
    Summarize a single document with optional memory integration.
    
    Args:
        file_path: Path to the document file
        with_memory: Whether to use conversation and reasoning memory
    """
    text = Path(file_path).read_text()
    
    if with_memory:
        # Initialize memory manager for this operation
        memory_manager = MemoryManager()
        set_memory_manager(memory_manager)
        
        try:
            extraction, trace_id = extract_with_reasoning(text)
            with GraphBuilder() as builder:
                builder.load_extraction(extraction, text)
            
            print("\n--- SUMMARY (with memory) ---\n")
            summary = summarize_with_memory(text, extraction["source_id"])
            print(summary)
            
            # Store in long-term memory
            memory_manager.longterm.store_document(
                extraction["source_id"], text, extraction
            )
            
            print(f"\n--- MEMORY TRACE ---")
            print(f"Extraction trace ID: {trace_id}")
            traces = memory_manager.reasoning.get_traces_by_document(extraction["source_id"])
            print(f"Total reasoning traces for this document: {len(traces)}")
        finally:
            reset_memory_manager()
    else:
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


def cmd_chat():
    """
    Start an interactive chat session with memory.
    
    This provides a conversational interface where:
    - Conversation history is maintained (short-term memory)
    - Reasoning traces are captured for each operation
    - Long-term memory from Neo4j is used for context
    """
    memory_manager = MemoryManager()
    set_memory_manager(memory_manager)
    
    try:
        print("\n=== Finance/Ops Agent Chat Mode ===")
        print(f"Session ID: {memory_manager.session_id}")
        print("Type 'quit' or 'exit' to end the session.\n")
        
        while True:
            try:
                user_input = input("\n> ").strip()
                
                if user_input.lower() in ['quit', 'exit', 'bye']:
                    print("Ending chat session.")
                    break
                
                if not user_input:
                    continue
                
                # Add user message to conversation memory
                memory_manager.conversation.add_message(
                    "user",
                    user_input,
                    metadata={"timestamp": datetime.now().isoformat()}
                )
                
                # Process the query
                print("Processing...", end=" ", flush=True)
                
                # Use the summarizer with conversation context
                # For now, we'll use a simple approach - summarize based on graph data
                with GraphQueries() as q:
                    # Check for overdue invoices
                    overdue = q.overdue_invoices()
                    flags = q.open_flags()
                    vendors = q.vendor_spend_rollup()
                    
                    # Build response context
                    response_context = {
                        "overdue_invoices_count": len(overdue),
                        "open_flags_count": len(flags),
                        "vendors_count": len(vendors),
                        "total_vendor_spend": sum(v.get('total_spend', 0) for v in vendors)
                    }
                
                # Generate response using Claude with conversation context
                client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
                
                # Get conversation context
                conversation = memory_manager.conversation.get_context(max_messages=5)
                conversation_text = "\n".join(
                    [f"{msg['role'].upper()}: {msg['content']}" for msg in conversation]
                )
                
                # Start reasoning trace
                trace_id = memory_manager.reasoning.start_trace(
                    "chat_query",
                    user_input,
                    document_id=None
                )
                memory_manager.reasoning.add_step(
                    trace_id,
                    "Processing user query with conversation context",
                    {"query": user_input, "conversation_length": len(conversation)}
                )
                
                response = client.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=1000,
                    system="""You are a finance and operations assistant. 
                    Answer questions based on the conversation history and any provided context.
                    Be concise and helpful. If you don't have enough information, say so.""",
                    messages=[
                        {"role": "user", "content": f"Query: {user_input}\n\nContext: {json.dumps(response_context)}\n\nConversation: {conversation_text}"}
                    ],
                )
                
                assistant_response = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                
                # Complete reasoning trace
                memory_manager.reasoning.add_step(
                    trace_id,
                    "Generated response",
                    {"response_length": len(assistant_response)}
                )
                memory_manager.reasoning.complete_trace(
                    trace_id,
                    assistant_response,
                    confidence=0.9
                )
                
                # Add assistant response to conversation memory
                memory_manager.conversation.add_message(
                    "assistant",
                    assistant_response,
                    metadata={"timestamp": datetime.now().isoformat(), "trace_id": trace_id}
                )
                
                print(assistant_response)
                
            except KeyboardInterrupt:
                print("\nUse 'quit' to exit.")
            except Exception as e:
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()
    
    finally:
        reset_memory_manager()


def cmd_memory_report():
    """
    Display memory statistics and recent traces.
    """
    memory_manager = get_memory_manager()
    
    print("\n=== Memory Statistics ===")
    
    # Conversation memory stats
    print(f"\n--- Short-term Memory (Conversation) ---")
    history = memory_manager.conversation.get_history()
    print(f"Conversation messages: {len(history)}")
    if history:
        print(f"Session ID: {history[0].session_id}")
        print(f"Most recent: {history[-1].role}: {history[-1].content[:100]}...")
    
    # Reasoning memory stats
    print(f"\n--- Reasoning Memory ---")
    traces = list(memory_manager.reasoning._traces.values())
    print(f"Total reasoning traces: {len(traces)}")
    
    if traces:
        # Group by task type
        by_type = {}
        for trace in traces:
            task_type = trace.task_type
            by_type[task_type] = by_type.get(task_type, 0) + 1
        
        print("Traces by type:")
        for task_type, count in by_type.items():
            print(f"  {task_type}: {count}")
        
        # Show most recent traces
        print(f"\nMost recent traces:")
        for trace in sorted(traces, key=lambda t: t.timestamp, reverse=True)[:5]:
            print(f"  [{trace.timestamp[:19]}] {trace.task_type}: {trace.input_text[:80]}...")
            print(f"    -> Steps: {len(trace.reasoning_steps)}, Output length: {len(str(trace.final_output)) if trace.final_output else 0}")
    
    # Long-term memory stats (from Neo4j)
    print(f"\n--- Long-term Memory (Neo4j) ---")
    try:
        with memory_manager.longterm.driver.session() as session:
            # Count documents
            doc_count = session.run("MATCH (d:Document) RETURN count(d) AS count").single()
            print(f"Documents stored: {doc_count['count'] if doc_count else 0}")
            
            # Count vendors
            vendor_count = session.run("MATCH (v:Vendor) RETURN count(v) AS count").single()
            print(f"Vendors stored: {vendor_count['count'] if vendor_count else 0}")
            
            # Count reasoning traces in Neo4j
            trace_count = session.run("MATCH (r:ReasoningTrace) RETURN count(r) AS count").single()
            print(f"Reasoning traces in Neo4j: {trace_count['count'] if trace_count else 0}")
    except Exception as e:
        print(f"Could not query Neo4j: {e}")
    
    print("\n=== Memory Report Complete ===")


if __name__ == "__main__":
    config.require_config()

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    if command == "setup":
        cmd_setup()
    elif command == "ingest":
        if len(sys.argv) >= 3:
            enable_reasoning = "--reasoning" in sys.argv or "-r" in sys.argv
            cmd_ingest(sys.argv[2], enable_reasoning=enable_reasoning)
        else:
            print("Usage: python main.py ingest <folder> [--reasoning]")
            sys.exit(1)
    elif command == "summarize":
        if len(sys.argv) >= 3:
            with_memory = "--memory" in sys.argv or "-m" in sys.argv
            cmd_summarize(sys.argv[2], with_memory=with_memory)
        else:
            print("Usage: python main.py summarize <file> [--memory]")
            sys.exit(1)
    elif command == "report":
        cmd_report()
    elif command == "chat":
        cmd_chat()
    elif command == "memory":
        if len(sys.argv) >= 3 and sys.argv[2] == "report":
            cmd_memory_report()
        else:
            print("Usage: python main.py memory report")
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(1)
