#!/usr/bin/env python3
"""
Knowledge Graph Evaluation Script
Evaluates the current state of the financial knowledge graph in Neo4j.
"""
from neo4j import GraphDatabase
import config
import json
from datetime import datetime


def evaluate_knowledge_graph():
    """Perform a comprehensive evaluation of the knowledge graph."""
    # Connect to Neo4j
    driver = GraphDatabase.driver(
        config.NEO4J_URI,
        auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
    )

    print('Connected to Neo4j at:', config.NEO4J_URI)
    print('Database:', config.NEO4J_DATABASE)
    print()

    with driver.session(database=config.NEO4J_DATABASE) as session:
        # Get graph summary for our specific financial data
        print('=' * 70)
        print('FINANCIAL KNOWLEDGE GRAPH EVALUATION')
        print('=' * 70)
        
        # Count financial nodes
        financial_labels = ['Document', 'Vendor', 'Invoice', 'Transaction', 'Flag']
        
        print('\n📊 FINANCIAL NODE COUNTS:')
        node_counts = {}
        for label in financial_labels:
            result = session.run(f'MATCH (n:{label}) RETURN count(n) as cnt')
            count = result.single()['cnt']
            node_counts[label] = count
            print(f'  {label}: {count}')
        
        # Count financial relationships
        financial_rel_types = ['MENTIONS', 'BILLED_BY', 'PAID_TO', 'RAISED', 'CONCERNS']
        
        print('\n🔗 FINANCIAL RELATIONSHIP COUNTS:')
        rel_counts = {}
        for rel_type in financial_rel_types:
            result = session.run(f'MATCH ()-[r:{rel_type}]->() RETURN count(r) as cnt')
            count = result.single()['cnt']
            rel_counts[rel_type] = count
            print(f'  {rel_type}: {count}')
        
        # Get overdue invoices
        print('\n💰 OVERDUE INVOICES:')
        result = session.run("""
            MATCH (i:Invoice)-[:BILLED_BY]->(v:Vendor)
            WHERE i.due_date IS NOT NULL AND i.due_date < date()
              AND coalesce(i.status, '') <> 'paid'
            RETURN i.invoice_id AS invoice_id, v.name AS vendor,
                   i.amount AS amount, i.currency AS currency,
                   i.due_date AS due_date,
                   duration.between(date(i.due_date), date()).days AS days_overdue
            ORDER BY days_overdue DESC
        """)
        overdue_count = 0
        overdue_list = []
        for record in result:
            overdue_count += 1
            overdue_list.append(dict(record))
            print(f"  {record['invoice_id']}: {record['vendor']} - {record['amount']} {record['currency']} ({record['days_overdue']} days overdue)")
        if overdue_count == 0:
            print('  None')
        
        # Get vendor spend
        print('\n🏢 VENDOR SPEND ROLLUP:')
        result = session.run("""
            MATCH (v:Vendor)
            OPTIONAL MATCH (i:Invoice)-[:BILLED_BY]->(v)
            OPTIONAL MATCH (t:Transaction)-[:PAID_TO]->(v)
            WITH v,
                 coalesce(sum(DISTINCT i.amount), 0) AS invoice_total,
                 coalesce(sum(DISTINCT t.amount), 0) AS transaction_total
            RETURN v.name AS vendor,
                   invoice_total + transaction_total AS total_spend
            ORDER BY total_spend DESC
        """)
        vendor_spend = []
        for record in result:
            vendor_spend.append(dict(record))
            print(f"  {record['vendor']}: {record['total_spend']}")
        
        # Get flags
        print('\n⚠️  FLAGS:')
        result = session.run("""
            MATCH (f:Flag)
            OPTIONAL MATCH (f)-[:CONCERNS]->(entity)
            RETURN f.type AS type, f.description AS description,
                   labels(entity) AS entity_labels,
                   coalesce(entity.invoice_id, entity.name, entity.id) AS entity_ref
            ORDER BY f.type
        """)
        flags = []
        for record in result:
            flags.append(dict(record))
            entity_ref = record['entity_ref'] if record['entity_ref'] else 'N/A'
            print(f"  {record['type']}: {record['description'][:60]}... -> {entity_ref}")
        
        # Get large transactions
        print('\n💸 LARGE TRANSACTIONS (>50000):')
        result = session.run("""
            MATCH (t:Transaction)
            WHERE t.amount >= 50000
            OPTIONAL MATCH (t)-[:PAID_TO]->(v:Vendor)
            RETURN t.id AS id, t.description AS description, t.amount AS amount,
                   t.currency AS currency, t.date AS date, v.name AS vendor
            ORDER BY t.amount DESC
        """)
        large_txns = []
        for record in result:
            large_txns.append(dict(record))
            print(f"  {record['id']}: {record['amount']} {record['currency']} - {record['vendor']}")
        
        # Get recent documents
        print('\n📄 RECENT DOCUMENTS:')
        result = session.run("""
            MATCH (d:Document)
            WHERE d.source_id IS NOT NULL
            RETURN d.source_id as source_id, d.document_type as document_type, d.ingested_on as date
            ORDER BY d.ingested_on DESC
            LIMIT 10
        """)
        recent_docs = []
        count = 0
        for record in result:
            count += 1
            recent_docs.append(dict(record))
            source_id = record['source_id'] if record['source_id'] else 'N/A'
            doc_type = record['document_type'] if record['document_type'] else 'N/A'
            doc_date = record['date'] if record['date'] else 'N/A'
            print(f"  {source_id} ({doc_type}) - {doc_date}")
        if count == 0:
            print('  None found')
        
        # Check for orphaned nodes (nodes without relationships)
        print('\n🔍 ORPHANED FINANCIAL NODES:')
        orphaned = {}
        for label in financial_labels:
            result = session.run(f'MATCH (n:{label}) WHERE NOT (n)--() RETURN count(n) as cnt')
            count = result.single()['cnt']
            orphaned[label] = count
            if count > 0:
                print(f'  {label}: {count} orphaned nodes')
        
        # Check schema constraints for our labels
        print('\n🔒 SCHEMA CONSTRAINTS FOR FINANCIAL DATA:')
        try:
            result = session.run('SHOW CONSTRAINTS')
            constraints = []
            for record in result:
                constraint_str = str(record)
                constraints.append(constraint_str)
                # Check if it relates to our financial labels
                if any(label in constraint_str for label in financial_labels):
                    print(f'  {constraint_str}')
            if not any(label in str(constraints) for label in financial_labels):
                print('  No constraints found for financial labels')
        except Exception as e:
            print(f'  Could not retrieve constraints: {e}')
            constraints = []
        
        # Check schema indexes
        print('\n🔍 SCHEMA INDEXES FOR FINANCIAL DATA:')
        try:
            result = session.run('SHOW INDEXES')
            indexes = []
            for record in result:
                index_str = str(record)
                indexes.append(index_str)
                # Check if it relates to our financial labels
                if any(label in index_str for label in financial_labels):
                    print(f'  {index_str}')
            if not any(label in str(indexes) for label in financial_labels):
                print('  No indexes found for financial labels')
        except Exception as e:
            print(f'  Could not retrieve indexes: {e}')
            indexes = []
        
        # Graph density metrics
        print('\n📈 GRAPH DENSITY METRICS:')
        total_nodes = sum(node_counts.values())
        total_rels = sum(rel_counts.values())
        if total_nodes > 0:
            avg_degree = (2 * total_rels) / total_nodes
            print(f'  Total financial nodes: {total_nodes}')
            print(f'  Total financial relationships: {total_rels}')
            print(f'  Average degree: {avg_degree:.2f}')
            
            # Connectivity ratio
            max_possible_rels = total_nodes * (total_nodes - 1) / 2
            connectivity_ratio = total_rels / max_possible_rels if max_possible_rels > 0 else 0
            print(f'  Connectivity ratio: {connectivity_ratio:.6f}')
        
        # Return evaluation summary
        evaluation = {
            'timestamp': datetime.now().isoformat(),
            'node_counts': node_counts,
            'relationship_counts': rel_counts,
            'overdue_invoices': overdue_list,
            'vendor_spend': vendor_spend,
            'flags': flags,
            'large_transactions': large_txns,
            'recent_documents': recent_docs,
            'orphaned_nodes': orphaned,
            'constraints': constraints,
            'indexes': indexes,
            'total_financial_nodes': total_nodes,
            'total_financial_relationships': total_rels
        }
        
        return evaluation


if __name__ == '__main__':
    try:
        config.require_config()
    except EnvironmentError as e:
        print(f"Configuration error: {e}")
        print("Please set NEO4J_PASSWORD and ANTHROPIC_API_KEY environment variables")
        exit(1)
    
    evaluation = evaluate_knowledge_graph()
    
    # Save to file
    with open('kg_evaluation.json', 'w') as f:
        json.dump(evaluation, f, indent=2, default=str)
    
    print(f'\n✅ Evaluation saved to kg_evaluation.json')
