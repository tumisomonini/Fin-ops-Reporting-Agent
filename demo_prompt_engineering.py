#!/usr/bin/env python3
"""
Demo script for the Prompt Engineering & Evaluation Interface.

This script demonstrates how to use the prompt_engineer module programmatically
to create, test, and evaluate prompts against the Finance/Ops knowledge graph.
"""

from prompt_engineer import (
    PromptTemplate,
    PromptManager,
    KnowledgeGraphContext,
    EvaluationMetrics,
    OutcomeComparator
)
from datetime import datetime


def demo_prompt_engineering():
    """Run a complete demo of prompt engineering workflow."""
    
    print("="*70)
    print("  PROMPT ENGINEERING DEMO")
    print("="*70)
    
    # Initialize components
    pm = PromptManager()
    kg = KnowledgeGraphContext()
    metrics = EvaluationMetrics()
    comparator = OutcomeComparator()
    
    try:
        # Step 1: Show current knowledge graph state
        print("\n📊 Step 1: Knowledge Graph Summary")
        print("-"*70)
        summary = kg.get_graph_summary()
        print(f"  Documents: {summary['documents']}")
        print(f"  Vendors: {summary['vendors']}")
        print(f"  Invoices: {summary['invoices']} ({summary['overdue_invoices']} overdue)")
        print(f"  Transactions: {summary['transactions']}")
        print(f"  Flags: {summary['flags']}")
        
        # Step 2: Create a prompt template
        print("\n🎨 Step 2: Creating Prompt Templates")
        print("-"*70)
        
        # Template 1: Overdue invoice query
        template1 = PromptTemplate(
            name="Overdue Invoice Query",
            template="""
            List all overdue invoices with the following details:
            - Invoice ID
            - Vendor name
            - Amount and currency
            - Due date
            - Days overdue
            
            Format as a markdown table.
            """,
            description="Query for retrieving overdue invoices in table format",
            category="retrieval"
        )
        pm.save_template(template1)
        print(f"  ✓ Created: {template1.name} (ID: {template1.id[:8]}...)")
        
        # Template 2: Vendor spend analysis
        template2 = PromptTemplate(
            name="Vendor Spend Analysis",
            template="""
            Analyze the spending for vendor {{vendor_name}}:
            
            1. Total spend amount
            2. Number of invoices/transactions
            3. Most recent transaction date
            4. Any flags or issues related to this vendor
            5. Recommendations for managing this vendor relationship
            """,
            description="Detailed analysis of a specific vendor's spend",
            category="analysis",
            variables=["vendor_name"]
        )
        pm.save_template(template2)
        print(f"  ✓ Created: {template2.name} (ID: {template2.id[:8]}...)")
        
        # Step 3: Test prompts
        print("\n🧪 Step 3: Testing Prompts")
        print("-"*70)
        
        # Test template 1 (no variables needed)
        print(f"\n  Testing: {template1.name}")
        rendered1 = template1.render({})
        print(f"  Prompt: {rendered1[:80]}...")
        
        # Simulate retrieval
        with kg.queries.driver.session() as session:
            result = session.run("""
                MATCH (i:Invoice)
                WHERE i.due_date IS NOT NULL AND i.due_date < date()
                AND coalesce(i.status, '') <> 'paid'
                OPTIONAL MATCH (i)-[:BILLED_BY]->(v:Vendor)
                RETURN i.invoice_id as invoice_id, v.name as vendor, 
                       i.amount as amount, i.currency as currency,
                       i.due_date as due_date
                ORDER BY i.due_date
                LIMIT 5
            """)
            retrieval1 = [dict(record) for record in result]
        
        outcome1 = {
            "prompt": rendered1,
            "prompt_id": template1.id,
            "prompt_name": template1.name,
            "context": {},
            "retrieval": retrieval1,
            "response": f"Retrieved {len(retrieval1)} overdue invoices",
            "timestamp": datetime.now().isoformat()
        }
        pm.save_history(outcome1)
        print(f"  ✓ Retrieved {len(retrieval1)} overdue invoices")
        
        # Test template 2 (with variables)
        print(f"\n  Testing: {template2.name}")
        context = {"vendor_name": "CloudHost Ltd"}
        rendered2 = template2.render(context)
        print(f"  Prompt: {rendered2[:80]}...")
        print(f"  Variables: {context}")
        
        # Simulate retrieval
        with kg.queries.driver.session() as session:
            result = session.run("""
                MATCH (v:Vendor {name: $vendor_name})
                OPTIONAL MATCH (i:Invoice)-[:BILLED_BY]->(v)
                OPTIONAL MATCH (t:Transaction)-[:PAID_TO]->(v)
                RETURN v.name as vendor, 
                       collect(DISTINCT i) as invoices,
                       collect(DISTINCT t) as transactions
            """, vendor_name=context["vendor_name"])
            retrieval2 = [dict(record) for record in result]
        
        outcome2 = {
            "prompt": rendered2,
            "prompt_id": template2.id,
            "prompt_name": template2.name,
            "context": context,
            "retrieval": retrieval2,
            "response": f"Retrieved data for {context['vendor_name']}",
            "timestamp": datetime.now().isoformat()
        }
        pm.save_history(outcome2)
        print(f"  ✓ Retrieved data for {context['vendor_name']}")
        
        # Step 4: Evaluate retrievals
        print("\n📊 Step 4: Evaluating Retrievals")
        print("-"*70)
        
        # Evaluate outcome 1
        scores1 = {
            "relevance": 5,
            "completeness": 4,
            "accuracy": 5,
            "precision": 5,
            "overall": 5
        }
        metrics.record_evaluation(
            outcome1["prompt_id"],
            outcome1["prompt_name"],
            scores1,
            {"feedback": "Excellent retrieval - got all overdue invoices"}
        )
        print(f"  ✓ Evaluated: {template1.name} - Overall: {scores1['overall']}/5")
        
        # Evaluate outcome 2
        scores2 = {
            "relevance": 5,
            "completeness": 5,
            "accuracy": 5,
            "precision": 5,
            "overall": 5
        }
        metrics.record_evaluation(
            outcome2["prompt_id"],
            outcome2["prompt_name"],
            scores2,
            {"feedback": "Perfect - all vendor data retrieved"}
        )
        print(f"  ✓ Evaluated: {template2.name} - Overall: {scores2['overall']}/5")
        
        # Step 5: Compare outcomes
        print("\n⚖️  Step 5: Comparing Outcomes")
        print("-"*70)
        
        comparison = comparator.compare_outcomes(
            [outcome1, outcome2],
            [template1.name, template2.name]
        )
        
        print(f"  Length Stats:")
        print(f"    Min: {comparison['analysis']['length_stats']['min']} chars")
        print(f"    Max: {comparison['analysis']['length_stats']['max']} chars")
        print(f"  Unique Entities: {len(comparison['analysis']['unique_entities'])}")
        
        # Step 6: Show metrics
        print("\n📈 Step 6: Evaluation Metrics")
        print("-"*70)
        
        all_metrics = metrics.get_all_metrics()
        for prompt_id, data in all_metrics.items():
            print(f"\n  {data['name']}:")
            print(f"    Evaluations: {data['evaluation_count']}")
            print(f"    Average Score: {data['average_score']:.2f}/5")
        
        avg_metrics = metrics.calculate_average_metrics()
        print(f"\n  Overall:")
        print(f"    Average Score: {avg_metrics['average_overall']:.2f}/5")
        print(f"    Total Evaluations: {avg_metrics['total_evaluations']}")
        
        # Final summary
        print("\n" + "="*70)
        print("  DEMO COMPLETE")
        print("="*70)
        print(f"\n  ✓ Created {len(pm.list_templates())} prompt templates")
        print(f"  ✓ Executed {len(pm.load_history())} prompt tests")
        print(f"  ✓ Recorded {len(all_metrics)} evaluations")
        print(f"  ✓ Performed {len(comparator.get_comparison_history())} comparisons")
        
        print("\n📝 Files created:")
        print(f"  - prompt_templates/ - Contains saved templates")
        print(f"  - prompt_history.json - Test execution history")
        print(f"  - evaluation_metrics.json - Evaluation scores")
        
        print("\n🚀 To run the interactive interface:")
        print("  python prompt_engineer.py")
        
    finally:
        kg.close()


if __name__ == "__main__":
    try:
        import config
        config.require_config()
        demo_prompt_engineering()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
