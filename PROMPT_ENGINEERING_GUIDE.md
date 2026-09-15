# Prompt Engineering & Evaluation Interface Guide

This guide explains how to use the **interactive prompt engineering interface** (`prompt_engineer.py`) for the Finance/Ops Report Summarizer agent.

## Overview

The interface provides a comprehensive workflow for:

1. **Prompt Engineering** - Create, edit, and manage reusable prompt templates
2. **Prompt Testing** - Test prompts against the Neo4j knowledge graph
3. **Evaluation** - Score and track retrieval quality with metrics
4. **Comparison** - Compare outcomes from different prompts side-by-side
5. **Metrics Tracking** - View historical performance data

## Quick Start

```bash
# Run the interface
python prompt_engineer.py
```

The interface will connect to your Neo4j database and show a summary of available knowledge.

## Features

### 1. Engineer New Prompt

Create reusable prompt templates with variables for dynamic content.

**Steps:**
1. Select option `1` from the main menu
2. Enter a name for your prompt (e.g., "Overdue Invoice Query")
3. Add a description (optional)
4. Select a category (general/retrieval/summarization/analysis)
5. Enter your prompt template with `{{variable}}` placeholders
6. Press Enter twice to finish

**Example Template:**
```
Analyze the financial status of vendor {{vendor_name}}.
Include their total spend, overdue invoices, and any flags.
Provide recommendations for follow-up actions.
```

**Variables:** `vendor_name`

The template is saved and can be reused with different values for the variables.

### 2. Test Prompt Against Knowledge Graph

Execute a prompt and retrieve relevant data from Neo4j.

**Steps:**
1. Select option `2` from the main menu
2. Choose an existing template or enter a raw prompt
3. If using a template, provide values for any variables
4. View the retrieval results

**Output Includes:**
- Rendered prompt
- Retrieved entities (Documents, Vendors, Invoices, Transactions, Flags)
- Entity count

### 3. Evaluate Retrieval Quality

Score the quality of a retrieval using standard metrics.

**Metrics:**
- **Relevance (1-5):** Are retrieved entities relevant to the prompt?
- **Completeness (1-5):** Does retrieval cover all needed information?
- **Accuracy (1-5):** Is the retrieved information factually correct?
- **Precision (1-5):** Low noise/high signal ratio?
- **Overall (1-5):** Overall quality score

**Steps:**
1. Select option `3` from the main menu
2. Choose a recent test from history
3. Rate each metric from 1-5
4. Add optional feedback

All evaluations are saved and can be reviewed later.

### 4. Compare Prompt Outcomes

Compare the results from multiple prompts side-by-side.

**Steps:**
1. Select option `4` from the main menu
2. View recent tests
3. Select multiple tests to compare (comma-separated)
4. View comparison results:
   - Response length statistics
   - Entity coverage per prompt
   - Unique entities across all prompts

This helps identify which prompts perform best for specific use cases.

### 5. Manage Prompt Templates

View, delete, or export saved prompt templates.

**Options:**
- **View Details:** See full template with all metadata
- **Delete:** Remove a template
- **Export:** Save a template to a JSON file

### 6. View Evaluation Metrics

Track performance over time and identify best-performing prompts.

**Shows:**
- Overall statistics (average score, total evaluations)
- Per-prompt metrics (evaluation count, average score)
- Most recent evaluation details for each prompt

## Directory Structure

```
Fin-ops-Reporting-Agent/
├── prompt_engineer.py          # Main interface
├── prompt_templates/           # Saved prompt templates (auto-created)
│   └── <uuid>.json            # Individual template files
├── prompt_history.json         # History of prompt executions
└── evaluation_metrics.json     # Evaluation scores and metrics
```

## Example Workflow

### Use Case: Optimizing Overdue Invoice Retrieval

1. **Engineer Prompt A:**
   ```
   List all overdue invoices with vendor names and amounts.
   ```
   
2. **Engineer Prompt B:**
   ```
   Find invoices that are overdue and need immediate attention.
   Include vendor details and days overdue.
   ```

3. **Test Both Prompts:**
   - Run Prompt A and evaluate retrieval
   - Run Prompt B and evaluate retrieval

4. **Compare Outcomes:**
   - Compare entity coverage
   - Check which retrieved more relevant information

5. **Review Metrics:**
   - See which prompt scored higher
   - Identify strengths and weaknesses

6. **Iterate:**
   - Refine the lower-scoring prompt
   - Test again and re-evaluate

## Integration with LLM

The interface currently simulates LLM responses. To integrate with actual LLM (Claude/Anthropic):

1. Modify the `_test_prompt` method in `PromptEngineeringInterface`
2. Add actual API call to Anthropic
3. Pass the rendered prompt + graph context to the LLM
4. Store the actual LLM response in the outcome

**Example Integration:**

```python
import anthropic

# In _test_prompt method, replace the placeholder with:
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
response = client.messages.create(
    model=config.CLAUDE_MODEL,
    max_tokens=2000,
    messages=[{"role": "user", "content": rendered_prompt}],
)
llm_response = "".join(block.text for block in response.content if block.type == "text")
```

## Evaluation Best Practices

### Scoring Guidelines

| Score | Relevance | Completeness | Accuracy | Precision |
|-------|-----------|-------------|----------|-----------|
| 5 | All entities highly relevant | All needed info retrieved | All facts correct | No noise, all high-signal |
| 4 | Most entities relevant | Most info retrieved | Mostly correct | Minimal noise |
| 3 | Some relevant, some not | Partial coverage | Some errors | Moderate noise |
| 2 | Few relevant entities | Missing key info | Some errors | High noise |
| 1 | No relevant entities | Minimal coverage | Many errors | All noise |

### What to Evaluate

1. **Prompt Clarity:** Is the prompt clear and specific?
2. **Retrieval Quality:** Does the graph return the right entities?
3. **Response Quality:** (When LLM integrated) Is the response accurate and useful?
4. **Consistency:** Does the same prompt produce consistent results?

## Advanced Usage

### Template Variables

Use `{{variable}}` syntax for dynamic content:

```
Prompt: "Show me all transactions for vendor {{vendor_name}} in the date range {{start_date}} to {{end_date}}."

Variables: vendor_name, start_date, end_date
```

When testing, you'll be prompted to enter values for each variable.

### Entity Search

Before engineering prompts, you can search the knowledge graph:

```python
# In a Python shell
from prompt_engineer import KnowledgeGraphContext
kg = KnowledgeGraphContext()
results = kg.search_entities("CloudHost")
print(results)
kg.close()
```

This helps you understand what entities are available.

### Bulk Testing

For batch testing multiple prompts:

```python
from prompt_engineer import PromptManager, KnowledgeGraphContext

pm = PromptManager()
kg = KnowledgeGraphContext()

templates = pm.list_templates()
for template in templates:
    # Test each template
    outcome = test_template(template)  # Implement your test function
    pm.save_history(outcome)
    
kg.close()
```

## Troubleshooting

### Common Issues

1. **Configuration Errors**
   - Ensure `NEO4J_PASSWORD` and `ANTHROPIC_API_KEY` are set
   - Check `.env` file or export environment variables

2. **No Knowledge Graph Data**
   - Run `python main.py ingest test_documents` first
   - Verify data with `python main.py report`

3. **Prompt Not Working**
   - Check for typos in template variables
   - Verify entities exist in the knowledge graph
   - Try a simpler prompt first

4. **Evaluation Metrics Not Saving**
   - Check file permissions for `evaluation_metrics.json`
   - Ensure the directory exists

## Files Created/Modified

- **New:** `prompt_engineer.py` - Main interface module
- **New:** `prompt_templates/` - Directory for saved templates
- **New:** `prompt_history.json` - Test history storage
- **New:** `evaluation_metrics.json` - Evaluation data storage
- **Modified:** `graph_builder.py` - Fixed missing invoice_id handling

## Future Enhancements

1. **LLM Integration:** Connect to actual Claude/Anthropic API
2. **Auto-Evaluation:** Automatically score retrievals using heuristics
3. **A/B Testing:** Statistical comparison of prompt variants
4. **Version Control:** Track changes to prompt templates
5. **Collaboration:** Share prompts and evaluations with team members
6. **Visualization:** Graphical representation of metrics and comparisons

## Support

For issues or questions about the prompt engineering interface:

1. Check this guide for common issues
2. Review the code in `prompt_engineer.py`
3. Test with simpler prompts first
4. Verify your Neo4j connection and data
