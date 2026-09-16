"""
Memory System for Finance/Ops Reporting Agent.

Provides three types of memory:
1. Short-term Memory (ConversationMemory): In-session chat history
2. Long-term Memory (LongTermMemory): Persistent storage via Neo4j
3. Reasoning Memory (ReasoningMemory): Stores reasoning traces and decision paths

Usage:
    from memory import ConversationMemory, ReasoningMemory, LongTermMemory
    
    # Initialize memory systems
    conversation_memory = ConversationMemory()
    reasoning_memory = ReasoningMemory()
    longterm_memory = LongTermMemory()
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field, asdict

# LangChain imports (compatible with both 0.x and 1.x)
try:
    from langchain.memory import ConversationBufferMemory as LangChainConversationBuffer
    from langchain.schema import BaseMemory
except ImportError:
    # LangChain 1.x uses different import paths
    try:
        from langchain_community.memory import ConversationBufferMemory as LangChainConversationBuffer
        from langchain_core.memory import BaseMemory
    except ImportError:
        # Fallback - create a minimal interface
        LangChainConversationBuffer = None
        BaseMemory = object

import config
from neo4j import GraphDatabase


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class MemoryEntry:
    """Base class for memory entries."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    session_id: Optional[str] = None
    source: str = "unknown"  # e.g., "extraction", "summarizer", "user_query"
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "MemoryEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ConversationEntry(MemoryEntry):
    """Entry for conversation history (short-term memory)."""
    role: str = "user"  # "user" or "assistant"
    content: str = ""
    document_id: Optional[str] = None  # Reference to source document
    metadata: Dict = field(default_factory=dict)


@dataclass
class ReasoningEntry(MemoryEntry):
    """Entry for reasoning traces (reasoning memory)."""
    task_type: str = "unknown"  # e.g., "extraction", "summarization", "query"
    input_text: str = ""
    reasoning_steps: List[str] = field(default_factory=list)
    intermediate_results: Dict = field(default_factory=dict)
    final_output: Any = None
    confidence: Optional[float] = None
    document_id: Optional[str] = None
    
    def add_step(self, step: str, result: Any = None) -> None:
        """Add a reasoning step."""
        self.reasoning_steps.append(step)
        if result is not None:
            self.intermediate_results[len(self.reasoning_steps) - 1] = result


@dataclass
class LongTermEntry(MemoryEntry):
    """Entry for long-term memory (stored in Neo4j)."""
    entity_type: str = "unknown"  # e.g., "document", "vendor", "invoice"
    entity_id: str = ""
    properties: Dict = field(default_factory=dict)
    relationships: List[Dict] = field(default_factory=list)


# =============================================================================
# Short-term Memory (Conversation Memory)
# =============================================================================

class ConversationMemory:
    """
    Short-term memory for conversation history within a session.
    
    Stores the current conversation thread and provides context for
    multi-turn interactions. Can optionally persist to JSON file.
    
    Usage:
        memory = ConversationMemory(session_id="user_123")
        memory.add_message("user", "What are the overdue invoices?")
        memory.add_message("assistant", "Here are the overdue invoices...")
        context = memory.get_context()
    """
    
    def __init__(self, session_id: Optional[str] = None, 
                 persist_file: Optional[str] = None,
                 max_history: int = 100):
        """
        Initialize conversation memory.
        
        Args:
            session_id: Unique identifier for this conversation session
            persist_file: Optional file path to persist conversation history
            max_history: Maximum number of messages to keep in history
        """
        self.session_id = session_id or str(uuid.uuid4())
        self.persist_file = Path(persist_file) if persist_file else None
        self.max_history = max_history
        self._history: List[ConversationEntry] = []
        self._langchain_memory = None
        
        # Load existing conversation if persist_file exists
        if self.persist_file and self.persist_file.exists():
            self._load_history()
    
    def add_message(self, role: str, content: str, 
                    document_id: Optional[str] = None,
                    metadata: Optional[Dict] = None) -> ConversationEntry:
        """
        Add a message to the conversation history.
        
        Args:
            role: "user" or "assistant"
            content: The message content
            document_id: Optional reference to a document
            metadata: Optional additional metadata
            
        Returns:
            The created ConversationEntry
        """
        entry = ConversationEntry(
            role=role,
            content=content,
            session_id=self.session_id,
            document_id=document_id,
            metadata=metadata or {},
            source="conversation"
        )
        self._history.append(entry)
        
        # Trim history if too long
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]
        
        # Persist if configured
        if self.persist_file:
            self._save_history()
        
        return entry
    
    def get_context(self, max_messages: Optional[int] = None) -> List[Dict]:
        """
        Get conversation context as a list of message dicts.
        
        Args:
            max_messages: Maximum number of recent messages to return
            
        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        messages = self._history
        if max_messages:
            messages = messages[-max_messages:]
        
        return [{"role": m.role, "content": m.content} for m in messages]
    
    def get_history(self) -> List[ConversationEntry]:
        """Get all conversation entries."""
        return self._history.copy()
    
    def get_langchain_memory(self):
        """
        Get a LangChain-compatible ConversationBufferMemory.
        
        Returns:
            LangChain ConversationBufferMemory instance, or None if LangChain not available
        """
        if LangChainConversationBuffer is None:
            return None
            
        if self._langchain_memory is None:
            self._langchain_memory = LangChainConversationBuffer(
                memory_key="history",
                return_messages=True
            )
            # Load existing history into LangChain memory
            for entry in self._history:
                self._langchain_memory.chat_memory.messages.append(
                    self._entry_to_langchain_message(entry)
                )
        return self._langchain_memory
    
    def clear(self) -> None:
        """Clear conversation history."""
        self._history = []
        self._langchain_memory = None
        if self.persist_file and self.persist_file.exists():
            self.persist_file.unlink()
    
    def _entry_to_langchain_message(self, entry: ConversationEntry):
        """Convert ConversationEntry to LangChain message format."""
        if LangChainConversationBuffer is None:
            # LangChain not available, return a simple dict
            return {"role": entry.role, "content": entry.content}
        
        try:
            from langchain.schema import HumanMessage, AIMessage, SystemMessage
            
            message_class = {
                "user": HumanMessage,
                "assistant": AIMessage,
                "system": SystemMessage
            }.get(entry.role, HumanMessage)
            
            return message_class(content=entry.content)
        except ImportError:
            # Fallback if imports fail
            return {"role": entry.role, "content": entry.content}
    
    def _save_history(self) -> None:
        """Save conversation history to file."""
        if self.persist_file:
            data = [entry.to_dict() for entry in self._history]
            with open(self.persist_file, 'w') as f:
                json.dump(data, f, indent=2)
    
    def _load_history(self) -> None:
        """Load conversation history from file."""
        if self.persist_file and self.persist_file.exists():
            with open(self.persist_file, 'r') as f:
                data = json.load(f)
            self._history = [ConversationEntry.from_dict(d) for d in data]


# =============================================================================
# Reasoning Memory
# =============================================================================

class ReasoningMemory:
    """
    Reasoning memory for storing chain-of-thought and decision paths.
    
    Captures intermediate reasoning steps during extraction, summarization,
    and query operations for auditability and debugging.
    
    Usage:
        memory = ReasoningMemory()
        
        # Start a reasoning trace
        trace_id = memory.start_trace("extraction", input_text, document_id)
        
        # Add reasoning steps
        memory.add_step(trace_id, "Identified vendor names", vendors)
        memory.add_step(trace_id, "Extracted invoice amounts", amounts)
        
        # Complete the trace
        memory.complete_trace(trace_id, final_output, confidence=0.95)
    """
    
    def __init__(self, persist_file: Optional[str] = None,
                 store_in_neo4j: bool = True):
        """
        Initialize reasoning memory.
        
        Args:
            persist_file: Optional file path to persist reasoning traces
            store_in_neo4j: Whether to also store traces in Neo4j
        """
        self.persist_file = Path(persist_file) if persist_file else None
        self.store_in_neo4j = store_in_neo4j
        self._traces: Dict[str, ReasoningEntry] = {}
        self._neo4j_driver = None
        
        # Load existing traces if persist_file exists
        if self.persist_file and self.persist_file.exists():
            self._load_traces()
    
    def start_trace(self, task_type: str, input_text: str,
                    document_id: Optional[str] = None) -> str:
        """
        Start a new reasoning trace.
        
        Args:
            task_type: Type of task (e.g., "extraction", "summarization")
            input_text: The input text being processed
            document_id: Optional reference to a document
            
        Returns:
            Trace ID for adding steps
        """
        trace_id = str(uuid.uuid4())
        entry = ReasoningEntry(
            id=trace_id,
            task_type=task_type,
            input_text=input_text,
            document_id=document_id,
            source="reasoning_trace",
            session_id=self._get_session_id()
        )
        self._traces[trace_id] = entry
        return trace_id
    
    def add_step(self, trace_id: str, step_description: str,
                 result: Any = None) -> None:
        """
        Add a reasoning step to an existing trace.
        
        Args:
            trace_id: The trace ID from start_trace()
            step_description: Description of this reasoning step
            result: Optional intermediate result
        """
        if trace_id in self._traces:
            self._traces[trace_id].add_step(step_description, result)
    
    def complete_trace(self, trace_id: str, final_output: Any,
                       confidence: Optional[float] = None) -> Optional[ReasoningEntry]:
        """
        Complete a reasoning trace.
        
        Args:
            trace_id: The trace ID
            final_output: The final output of the reasoning process
            confidence: Optional confidence score (0.0-1.0)
            
        Returns:
            The completed ReasoningEntry, or None if trace_id not found
        """
        if trace_id not in self._traces:
            return None
        
        entry = self._traces[trace_id]
        entry.final_output = final_output
        entry.confidence = confidence
        
        # Store in Neo4j if configured
        if self.store_in_neo4j:
            self._store_in_neo4j(entry)
        
        # Persist to file if configured
        if self.persist_file:
            self._save_traces()
        
        return entry
    
    def get_all_traces(self) -> list:
        """Return all reasoning traces as a list."""
        return list(self._traces.values())

    def get_trace(self, trace_id: str) -> Optional[ReasoningEntry]:
        """Get a specific reasoning trace by ID."""
        return self._traces.get(trace_id)
    
    def get_traces_by_document(self, document_id: str) -> List[ReasoningEntry]:
        """Get all reasoning traces for a specific document."""
        return [e for e in self._traces.values() if e.document_id == document_id]
    
    def get_traces_by_type(self, task_type: str) -> List[ReasoningEntry]:
        """Get all reasoning traces of a specific type."""
        return [e for e in self._traces.values() if e.task_type == task_type]
    
    def clear(self) -> None:
        """Clear all reasoning traces."""
        self._traces = {}
        if self.persist_file and self.persist_file.exists():
            self.persist_file.unlink()
    
    def _get_session_id(self) -> str:
        """Get or create a session ID."""
        if not hasattr(self, '_session_id'):
            self._session_id = str(uuid.uuid4())
        return self._session_id
    
    def _store_in_neo4j(self, entry: ReasoningEntry) -> None:
        """Store a reasoning trace in Neo4j."""
        try:
            if self._neo4j_driver is None:
                self._neo4j_driver = GraphDatabase.driver(
                    config.NEO4J_URI,
                    auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
                )
            with self._neo4j_driver.session() as session:
                session.execute_write(self._write_reasoning_trace, entry)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to persist reasoning trace %s to Neo4j", entry.id, exc_info=True
            )
    
    @staticmethod
    def _write_reasoning_trace(tx, entry: ReasoningEntry) -> None:
        """Write a reasoning trace to Neo4j."""
        steps = [
            {"description": s, "index": i}
            for i, s in enumerate(entry.reasoning_steps)
        ]
        tx.run(
            """
            MERGE (r:ReasoningTrace {id: $id})
            SET r.timestamp = $timestamp,
                r.task_type = $task_type,
                r.input_text = $input_text,
                r.final_output = $final_output,
                r.confidence = $confidence,
                r.document_id = $document_id,
                r.session_id = $session_id
            WITH r
            FOREACH (step IN $steps |
                CREATE (s:ReasoningStep {
                    description: step.description,
                    index: step.index
                })
                CREATE (r)-[:HAS_STEP]->(s)
            )
            """,
            id=entry.id,
            timestamp=entry.timestamp,
            task_type=entry.task_type,
            input_text=entry.input_text[:5000],
            final_output=str(entry.final_output)[:5000] if entry.final_output else None,
            confidence=entry.confidence,
            document_id=entry.document_id,
            session_id=entry.session_id,
            steps=steps,
        )
    
    def _save_traces(self) -> None:
        """Save reasoning traces to file."""
        if self.persist_file:
            data = {trace_id: entry.to_dict() 
                    for trace_id, entry in self._traces.items()}
            with open(self.persist_file, 'w') as f:
                json.dump(data, f, indent=2)
    
    def _load_traces(self) -> None:
        """Load reasoning traces from file."""
        if self.persist_file and self.persist_file.exists():
            with open(self.persist_file, 'r') as f:
                data = json.load(f)
            self._traces = {
                trace_id: ReasoningEntry.from_dict(entry_data)
                for trace_id, entry_data in data.items()
            }


# =============================================================================
# Long-term Memory (Neo4j Wrapper)
# =============================================================================

class LongTermMemory:
    """
    Long-term memory wrapper around Neo4j knowledge graph.
    
    Provides persistent storage for:
    - Documents and their extracted entities
    - Conversation history (optionally)
    - Reasoning traces (optionally)
    
    Usage:
        memory = LongTermMemory()
        
        # Store a document with its entities
        memory.store_document(document_id, raw_text, extraction_result)
        
        # Retrieve document context
        context = memory.get_document_context(document_id)
        
        # Search for related documents
        related = memory.find_related_documents(document_id)
    """
    
    def __init__(self, driver=None):
        """
        Initialize long-term memory.
        
        Args:
            driver: Optional Neo4j driver. If None, creates a new one.
        """
        self._owns_driver = driver is None
        self.driver = driver or GraphDatabase.driver(
            config.NEO4J_URI,
            auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )
    
    def close(self) -> None:
        """Close the Neo4j driver."""
        if self._owns_driver:
            self.driver.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *exc):
        self.close()
    
    def store_document(self, source_id: str, raw_text: str, 
                       extraction: Dict, document_type: str = "other") -> None:
        """
        Store a document and its extracted entities in Neo4j.
        
        This is a convenience method that wraps the graph_builder functionality
        for memory operations.
        
        Args:
            source_id: Unique document ID
            raw_text: The raw document text
            extraction: The extraction result from extraction.py
            document_type: Type of document (invoice, email, etc.)
        """
        from graph_builder import GraphBuilder
        
        # Add memory metadata to extraction
        extraction['_memory_stored_at'] = datetime.now().isoformat()
        
        with GraphBuilder(self.driver) as builder:
            builder.load_extraction(extraction, raw_text)
    
    def get_document_context(self, source_id: str) -> Dict:
        """
        Get all context stored in the graph for a document.
        
        Args:
            source_id: The document's source ID
            
        Returns:
            Dict with document info, entities, and relationships
        """
        from queries import GraphQueries
        
        with GraphQueries(self.driver) as q:
            context = q.document_context(source_id)
            return context
    
    def find_related_documents(self, source_id: str, 
                                relationship_type: Optional[str] = None) -> List[Dict]:
        """
        Find documents related to the specified document.
        
        Args:
            source_id: The document's source ID
            relationship_type: Optional relationship type to filter by
            
        Returns:
            List of related document info dicts
        """
        with self.driver.session() as session:
            query = """
                MATCH (d1:Document {source_id: $source_id})-[r]->(entity)
                MATCH (d2:Document)-[:MENTIONS]->(entity)
                WHERE d1 <> d2
            """
            if relationship_type:
                query += f" AND type(r) = '{relationship_type}'"
            
            query += """
                RETURN d2.source_id AS source_id,
                       d2.document_type AS document_type,
                       d2.ingested_on AS ingested_on,
                       type(r) AS relationship_type
                LIMIT 10
            """
            
            result = session.run(query, source_id=source_id)
            return [dict(record) for record in result]
    
    def store_conversation(self, session_id: str, messages: List[Dict]) -> None:
        """
        Store conversation history in Neo4j.
        
        Args:
            session_id: The conversation session ID
            messages: List of message dicts with 'role', 'content', 'timestamp'
        """
        with self.driver.session() as session:
            for msg in messages:
                session.execute_write(self._write_conversation_message, session_id, msg)
    
    @staticmethod
    def _write_conversation_message(tx, session_id: str, message: Dict) -> None:
        """Write a conversation message to Neo4j."""
        msg_id = str(uuid.uuid4())
        tx.run(
            """
            MERGE (s:ConversationSession {id: $session_id})
            CREATE (m:ConversationMessage {
                id: $msg_id,
                role: $role,
                content: $content,
                timestamp: $timestamp
            })
            MERGE (s)-[:HAS_MESSAGE]->(m)
            """,
            session_id=session_id,
            msg_id=msg_id,
            role=message.get('role', 'unknown'),
            content=message.get('content', ''),
            timestamp=message.get('timestamp', datetime.now().isoformat())
        )
    
    def get_conversation_history(self, session_id: str, 
                                  limit: int = 50) -> List[Dict]:
        """
        Get conversation history from Neo4j.
        
        Args:
            session_id: The conversation session ID
            limit: Maximum number of messages to return
            
        Returns:
            List of message dicts
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (s:ConversationSession {id: $session_id})-[:HAS_MESSAGE]->(m:ConversationMessage)
                RETURN m.role AS role,
                       m.content AS content,
                       m.timestamp AS timestamp
                ORDER BY m.timestamp
                LIMIT $limit
                """,
                session_id=session_id,
                limit=limit
            )
            return [dict(record) for record in result]
    
    def search_memory(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Full-text search across all memory (documents, conversations, reasoning).
        
        Args:
            query: Search query
            limit: Maximum number of results
            
        Returns:
            List of matching entries
        """
        # This would require Neo4j full-text indexes to be set up
        # For now, return a simple search
        with self.driver.session() as session:
            result = session.run(
                """
                CALL db.index.fulltext.queryNodes('memory_search', $query) YIELD node, score
                RETURN labels(node) AS labels,
                       properties(node) AS properties,
                       score
                ORDER BY score DESC
                LIMIT $limit
                """,
                query=query,
                limit=limit
            )
            return [dict(record) for record in result]


# =============================================================================
# Memory Manager (Combined Interface)
# =============================================================================

class MemoryManager:
    """
    Unified interface for all memory systems.
    
    Combines short-term (conversation), long-term (Neo4j), and reasoning memory
    into a single convenient interface.
    
    Usage:
        memory = MemoryManager(session_id="user_123")
        
        # Conversation memory
        memory.conversation.add_message("user", "What's the status?")
        context = memory.conversation.get_context()
        
        # Reasoning memory
        trace_id = memory.reasoning.start_trace("extraction", input_text)
        memory.reasoning.add_step(trace_id, "Step 1", result)
        memory.reasoning.complete_trace(trace_id, output)
        
        # Long-term memory
        memory.longterm.store_document(source_id, text, extraction)
        context = memory.longterm.get_document_context(source_id)
    """
    
    def __init__(self, session_id: Optional[str] = None,
                 conversation_persist: Optional[str] = None,
                 reasoning_persist: Optional[str] = None,
                 driver=None):
        """
        Initialize the unified memory manager.
        
        Args:
            session_id: Unique session identifier
            conversation_persist: Optional file to persist conversation history
            reasoning_persist: Optional file to persist reasoning traces
            driver: Optional Neo4j driver
        """
        self.session_id = session_id or str(uuid.uuid4())
        
        # Initialize individual memory systems
        self.conversation = ConversationMemory(
            session_id=self.session_id,
            persist_file=conversation_persist
        )
        
        self.reasoning = ReasoningMemory(
            persist_file=reasoning_persist,
            store_in_neo4j=True
        )
        
        self.longterm = LongTermMemory(driver=driver)
    
    def close(self) -> None:
        """Close all memory systems."""
        self.longterm.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *exc):
        self.close()
    
    def get_full_context(self, document_id: Optional[str] = None,
                         max_conversation: int = 10) -> Dict:
        """
        Get complete context combining all memory types.
        
        Args:
            document_id: Optional document ID to get context for
            max_conversation: Maximum number of conversation messages
            
        Returns:
            Dict with all context information
        """
        context = {
            "session_id": self.session_id,
            "conversation": self.conversation.get_context(max_conversation),
            "reasoning_traces": list(self.reasoning._traces.values())
        }
        
        if document_id:
            context["document_context"] = self.longterm.get_document_context(document_id)
            context["related_documents"] = self.longterm.find_related_documents(document_id)
        
        return context


# =============================================================================
# Global Memory Instance
# =============================================================================

# Global memory manager instance (can be replaced with custom instance)
_memory_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """Get the global memory manager instance."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


def set_memory_manager(manager: MemoryManager) -> None:
    """Set the global memory manager instance."""
    global _memory_manager
    _memory_manager = manager


def reset_memory_manager() -> None:
    """Reset the global memory manager instance."""
    global _memory_manager
    if _memory_manager is not None:
        _memory_manager.close()
    _memory_manager = None
