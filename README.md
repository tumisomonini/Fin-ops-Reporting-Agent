# Finance/Ops Report Summarizer — Python + Neo4j

A GraphRAG-style pipeline: documents (invoices, expense logs, finance/ops
emails) are extracted into structured entities via Claude, loaded into a
Neo4j knowledge graph, and then summarized in plain language — with the
graph providing verified context (overdue status, duplicate detection,
vendor spend totals) rather than letting the LLM guess at those facts.

## Architecture

```
document (.txt) → extraction.py (Claude, structured JSON)
                → graph_builder.py (writes to Neo4j: Vendor, Invoice,
                  Transaction, Document, Flag nodes + relationships,
                  including duplicate detection at load time)
                → queries.py (Cypher: overdue invoices, vendor spend
                  rollups, open flags, large transactions)
                → summarizer.py (Claude, using raw text + verified
                  graph context → final user-friendly summary)
```

## Setup

1. **Neo4j**: spin up a free instance ([Neo4j Aura](https://neo4j.com/cloud/aura-free/)
   is the easiest) or run one locally.
2. **Anthropic API key**: from [console.anthropic.com](https://console.anthropic.com).
3. Copy `.env.example` to `.env` and fill in your credentials:
   ```
   cp .env.example .env
   ```
4. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
5. Apply the graph schema (run once):
   ```
   python main.py setup
   ```

## Usage

**Ingest a batch of documents** (loads them into the graph, no summary printed):
```
python main.py ingest test_documents/
```

**Summarize a single document** (extracts, loads into the graph, then prints
the user-friendly summary grounded in graph context):
```
python main.py summarize test_documents/01_vendor_spend_email.txt
```

**Print a standing report** of everything currently in the graph:
```
python main.py report
```
Shows overdue invoices, open flags (including any duplicates detected
across documents), and a vendor spend rollup.

**Prompt Engineering Interface** (for developing and testing prompts):
```
python prompt_engineer.py
```
This launches an interactive CLI for:
- Engineering and saving prompt templates
- Testing prompts against the Neo4j knowledge graph
- Evaluating retrieval quality with metrics
- Comparing outcomes from different prompts
- Managing prompt templates and viewing metrics

See [PROMPT_ENGINEERING_GUIDE.md](PROMPT_ENGINEERING_GUIDE.md) for details.

## Test documents

`test_documents/` contains all 10 scenarios used to validate this against
the no-code v2 prompt: a clean vendor-spend email, a standard invoice, an
expense log, a missing-info invoice, a duplicate charge, a messy forwarded
email chain, a currency-mismatch expense claim, a vague email, a large
anomaly buried in routine data, and a conflicting-deadlines email.

## Why a graph here (and not just a prompt)

The no-code Claude Project version re-derives "is this overdue?" or "is
this a duplicate?" from scratch every single time, purely from what's in
the current message. This version checks those facts against everything
previously ingested — so a duplicate charge is caught even if it was
submitted in a *different* document weeks apart, and "which invoices are
overdue right now" is a real query answerable at any time, not just
whatever happens to be in the current chat.

## Notes / next steps

- Duplicate detection currently keys on exact (vendor, amount, date) match
  for transactions — could be extended to fuzzy vendor-name matching
  (e.g. entity resolution for "CloudHost Ltd" vs "Cloudhost (Pty) Ltd").
- Currency is stored per-entity but not normalized/converted — worth adding
  an FX-rate lookup if multi-currency documents (see test doc 7) become
  common.
- `main.py summarize` re-extracts and reloads every time; for a production
  version you'd want to check whether a document was already ingested
  (e.g. by hashing its text) before re-writing it.
