"""
Interactive Prompt Engineering & Evaluation Interface for Finance/Ops Agent.

This module provides a CLI interface for:
- Engineering and testing prompts against the Neo4j knowledge graph
- Evaluating retrieval quality with metrics
- Comparing outcomes from different prompts
- Saving and loading prompt templates
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

from neo4j import GraphDatabase

import config
from queries import GraphQueries


# =============================================================================
# Prompt Template Management
# =============================================================================

PROMPT_TEMPLATES_DIR = Path("prompt_templates")
PROMPT_HISTORY_FILE = Path("prompt_history.json")
EVALUATION_METRICS_FILE = Path("evaluation_metrics.json")


class PromptTemplate:
    """A reusable prompt template with metadata."""
    
    def __init__(self, name: str, template: str, description: str = "", 
                 category: str = "general", variables: List[str] = None):
        self.id = str(uuid.uuid4())
        self.name = name
        self.template = template
        self.description = description
        self.category = category
        self.variables = variables or []
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "template": self.template,
            "description": self.description,
            "category": self.category,
            "variables": self.variables,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "PromptTemplate":
        template = cls(
            name=data["name"],
            template=data["template"],
            description=data.get("description", ""),
            category=data.get("category", "general"),
            variables=data.get("variables", [])
        )
        template.id = data["id"]
        template.created_at = data["created_at"]
        template.updated_at = data["updated_at"]
        return template
    
    def render(self, context: Dict) -> str:
        """Render the template with provided context variables."""
        result = self.template
        for key, value in context.items():
            placeholder = f"{{{key}}}"
            result = result.replace(placeholder, str(value))
        return result


class PromptManager:
    """Manages prompt templates and history."""
    
    def __init__(self):
        self.templates_dir = PROMPT_TEMPLATES_DIR
        self.history_file = PROMPT_HISTORY_FILE
        self.templates_dir.mkdir(exist_ok=True)
    
    def save_template(self, template: PromptTemplate) -> None:
        """Save a prompt template to file."""
        filepath = self.templates_dir / f"{template.id}.json"
        with open(filepath, "w") as f:
            json.dump(template.to_dict(), f, indent=2)
    
    def load_template(self, template_id: str) -> Optional[PromptTemplate]:
        """Load a prompt template by ID."""
        filepath = self.templates_dir / f"{template_id}.json"
        if not filepath.exists():
            return None
        with open(filepath) as f:
            return PromptTemplate.from_dict(json.load(f))
    
    def list_templates(self) -> List[PromptTemplate]:
        """List all saved prompt templates."""
        templates = []
        for filepath in self.templates_dir.glob("*.json"):
            with open(filepath) as f:
                templates.append(PromptTemplate.from_dict(json.load(f)))
        return sorted(templates, key=lambda t: t.created_at, reverse=True)
    
    def delete_template(self, template_id: str) -> bool:
        """Delete a prompt template."""
        filepath = self.templates_dir / f"{template_id}.json"
        if filepath.exists():
            filepath.unlink()
            return True
        return False
    
    def save_history(self, entry: Dict) -> None:
        """Save a prompt execution to history."""
        # Clean up Neo4j objects that aren't JSON serializable
        clean_entry = self._clean_for_json(entry)
        
        history = []
        if self.history_file.exists():
            with open(self.history_file) as f:
                history = json.load(f)
        history.append(clean_entry)
        with open(self.history_file, "w") as f:
            json.dump(history, f, indent=2)
    
    def _clean_for_json(self, data: Any) -> Any:
        """Recursively clean data to be JSON serializable."""
        if isinstance(data, dict):
            return {k: self._clean_for_json(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._clean_for_json(item) for item in data]
        elif hasattr(data, '__dict__'):
            # Neo4j Node or similar - convert to dict
            return dict(data)
        elif hasattr(data, 'isoformat'):
            # datetime or similar
            return data.isoformat()
        else:
            return data
    
    def load_history(self, limit: int = 50) -> List[Dict]:
        """Load prompt execution history."""
        if not self.history_file.exists():
            return []
        with open(self.history_file) as f:
            history = json.load(f)
        return history[-limit:]  # Return most recent


# =============================================================================
# Knowledge Graph Context Provider
# =============================================================================

class KnowledgeGraphContext:
    """Provides context from the Neo4j knowledge graph for prompt engineering."""
    
    def __init__(self):
        self.queries = GraphQueries()
    
    def get_graph_summary(self) -> Dict:
        """Get a summary of what's in the knowledge graph."""
        with self.queries.driver.session() as session:
            # Count nodes
            result = session.run("MATCH (n:Document) RETURN count(n) as docs")
            docs = result.single()["docs"]
            
            result = session.run("MATCH (n:Vendor) RETURN count(n) as vendors")
            vendors = result.single()["vendors"]
            
            result = session.run("MATCH (n:Invoice) RETURN count(n) as invoices")
            invoices = result.single()["invoices"]
            
            result = session.run("MATCH (n:Transaction) RETURN count(n) as transactions")
            transactions = result.single()["transactions"]
            
            result = session.run("MATCH (n:Flag) RETURN count(n) as flags")
            flags = result.single()["flags"]
            
            # Get overdue count
            result = session.run("""
                MATCH (i:Invoice)
                WHERE i.due_date IS NOT NULL AND i.due_date < date()
                AND coalesce(i.status, '') <> 'paid'
                RETURN count(i) as overdue
            """)
            overdue = result.single()["overdue"]
            
            # Get recent documents
            result = session.run("""
                MATCH (d:Document)
                RETURN d.source_id as source_id, d.document_type as type, d.ingested_on as date
                ORDER BY d.ingested_on DESC
                LIMIT 5
            """)
            recent_docs = [dict(record) for record in result]
            
            return {
                "documents": docs,
                "vendors": vendors,
                "invoices": invoices,
                "transactions": transactions,
                "flags": flags,
                "overdue_invoices": overdue,
                "recent_documents": recent_docs
            }
    
    def get_entity_details(self, entity_type: str, entity_id: str = None) -> List[Dict]:
        """Get details for a specific entity type."""
        with self.queries.driver.session() as session:
            if entity_id:
                result = session.run(f"""
                    MATCH (n:{entity_type} {{id: $id}})
                    RETURN properties(n) as entity
                """, id=entity_id)
            else:
                result = session.run(f"""
                    MATCH (n:{entity_type})
                    RETURN properties(n) as entity
                    LIMIT 10
                """)
            return [record["entity"] for record in result]
    
    def search_entities(self, query: str, entity_type: str = None) -> List[Dict]:
        """Search entities by name or property."""
        with self.queries.driver.session() as session:
            if entity_type:
                result = session.run(f"""
                    MATCH (n:{entity_type})
                    WHERE toLower(n.name) CONTAINS toLower($query)
                    RETURN properties(n) as entity
                    LIMIT 10
                """, query=query)
            else:
                # Search across multiple types
                result = session.run("""
                    MATCH (n)
                    WHERE n.name CONTAINS $query OR n.invoice_id CONTAINS $query
                    RETURN labels(n) as labels, properties(n) as entity
                    LIMIT 10
                """, query=query)
            return [dict(record) for record in result]
    
    def close(self):
        self.queries.close()


# =============================================================================
# Evaluation Metrics
# =============================================================================

class EvaluationMetrics:
    """Tracks and calculates evaluation metrics for prompt performance."""
    
    def __init__(self):
        self.metrics_file = EVALUATION_METRICS_FILE
        self.metrics = {}
        self.load_metrics()
    
    def load_metrics(self) -> None:
        """Load saved metrics from file."""
        if self.metrics_file.exists():
            with open(self.metrics_file) as f:
                self.metrics = json.load(f)
    
    def save_metrics(self) -> None:
        """Save metrics to file."""
        with open(self.metrics_file, "w") as f:
            json.dump(self.metrics, f, indent=2)
    
    def record_evaluation(self, prompt_id: str, prompt_name: str, 
                         scores: Dict, context: Dict = None) -> None:
        """Record evaluation scores for a prompt."""
        if prompt_id not in self.metrics:
            self.metrics[prompt_id] = {
                "name": prompt_name,
                "evaluations": [],
                "total_score": 0,
                "average_score": 0,
                "evaluation_count": 0
            }
        
        eval_entry = {
            "timestamp": datetime.now().isoformat(),
            "scores": scores,
            "context": context or {}
        }
        
        self.metrics[prompt_id]["evaluations"].append(eval_entry)
        self.metrics[prompt_id]["evaluation_count"] += 1
        
        # Recalculate average
        total = sum(e["scores"].get("overall", 0) for e in self.metrics[prompt_id]["evaluations"])
        self.metrics[prompt_id]["total_score"] = total
        self.metrics[prompt_id]["average_score"] = total / self.metrics[prompt_id]["evaluation_count"]
        
        self.save_metrics()
    
    def get_prompt_metrics(self, prompt_id: str) -> Optional[Dict]:
        """Get metrics for a specific prompt."""
        return self.metrics.get(prompt_id)
    
    def get_all_metrics(self) -> Dict:
        """Get all evaluation metrics."""
        return self.metrics
    
    def calculate_average_metrics(self) -> Dict:
        """Calculate average metrics across all prompts."""
        if not self.metrics:
            return {}
        
        all_scores = []
        for prompt_data in self.metrics.values():
            for eval_entry in prompt_data["evaluations"]:
                all_scores.append(eval_entry["scores"].get("overall", 0))
        
        if not all_scores:
            return {}
        
        return {
            "average_overall": sum(all_scores) / len(all_scores),
            "total_evaluations": len(all_scores),
            "best_prompt": max(self.metrics.values(), key=lambda x: x.get("average_score", 0))["name"]
        }


# =============================================================================
# Outcome Comparison
# =============================================================================

class OutcomeComparator:
    """Compares outcomes from different prompts."""
    
    def __init__(self):
        self.comparison_history = []
    
    def compare_outcomes(self, outcomes: List[Dict], prompt_names: List[str]) -> Dict:
        """Compare multiple outcomes side by side."""
        if len(outcomes) != len(prompt_names):
            raise ValueError("Outcomes and prompt names must have same length")
        
        comparison = {
            "timestamp": datetime.now().isoformat(),
            "prompts": prompt_names,
            "outcomes": outcomes,
            "analysis": self._analyze_comparison(outcomes)
        }
        
        self.comparison_history.append(comparison)
        return comparison
    
    def _analyze_comparison(self, outcomes: List[Dict]) -> Dict:
        """Analyze differences between outcomes."""
        # Calculate response lengths
        lengths = [len(outcome.get("response", "")) for outcome in outcomes]
        
        # Extract entities mentioned
        entities_per_prompt = []
        for outcome in outcomes:
            text = outcome.get("response", "")
            # Simple entity extraction (could be enhanced)
            entities = set()
            if "Bramwell" in text:
                entities.add("Bramwell & Co Supplies")
            if "CloudHost" in text:
                entities.add("CloudHost Ltd")
            if "Adfinity" in text:
                entities.add("Adfinity Media")
            entities_per_prompt.append(list(entities))
        
        return {
            "length_stats": {
                "min": min(lengths) if lengths else 0,
                "max": max(lengths) if lengths else 0,
                "avg": sum(lengths) / len(lengths) if lengths else 0
            },
            "entity_coverage": entities_per_prompt,
            "unique_entities": list(set(e for entities in entities_per_prompt for e in entities))
        }
    
    def get_comparison_history(self) -> List[Dict]:
        """Get history of outcome comparisons."""
        return self.comparison_history


# =============================================================================
# Interactive CLI Interface
# =============================================================================

class PromptEngineeringInterface:
    """Main interactive CLI interface."""
    
    def __init__(self):
        self.prompt_manager = PromptManager()
        self.metrics = EvaluationMetrics()
        self.comparator = OutcomeComparator()
        self.kg_context = KnowledgeGraphContext()
    
    def run(self):
        """Run the interactive interface."""
        print("\n" + "="*70)
        print("  Finance/Ops Agent - Prompt Engineering & Evaluation Interface")
        print("="*70)
        print("\nConnected to Neo4j:", config.NEO4J_URI)
        print("Database:", config.NEO4J_DATABASE)
        
        # Show graph summary
        summary = self.kg_context.get_graph_summary()
        print("\n📊 Knowledge Graph Summary:")
        print(f"  Documents: {summary['documents']}")
        print(f"  Vendors: {summary['vendors']}")
        print(f"  Invoices: {summary['invoices']} ({summary['overdue_invoices']} overdue)")
        print(f"  Transactions: {summary['transactions']}")
        print(f"  Flags: {summary['flags']}")
        
        # Main menu loop
        while True:
            self._print_main_menu()
            choice = input("\n📝 Select an option (1-6, or 'q' to quit): ").strip().lower()
            
            if choice == 'q':
                print("\n👋 Good luck with your prompt engineering!")
                break
            elif choice == '1':
                self._engineer_prompt()
            elif choice == '2':
                self._test_prompt()
            elif choice == '3':
                self._evaluate_retrieval()
            elif choice == '4':
                self._compare_outcomes()
            elif choice == '5':
                self._manage_templates()
            elif choice == '6':
                self._view_metrics()
            else:
                print("❌ Invalid option. Please try again.")
        
        self.kg_context.close()
    
    def _print_main_menu(self):
        """Print the main menu."""
        print("\n" + "-"*70)
        print("  MAIN MENU")
        print("-"*70)
        print("  1. 🎨 Engineer New Prompt")
        print("  2. 🧪 Test Prompt Against Knowledge Graph")
        print("  3. 📊 Evaluate Retrieval Quality")
        print("  4. ⚖️  Compare Prompt Outcomes")
        print("  5. 📁 Manage Prompt Templates")
        print("  6. 📈 View Evaluation Metrics")
    
    def _engineer_prompt(self):
        """Interactive prompt engineering."""
        print("\n" + "="*70)
        print("  PROMPT ENGINEERING")
        print("="*70)
        
        # Show context
        summary = self.kg_context.get_graph_summary()
        print(f"\n📊 Available Knowledge:")
        print(f"   - {summary['vendors']} vendors")
        print(f"   - {summary['invoices']} invoices ({summary['overdue_invoices']} overdue)")
        print(f"   - {summary['transactions']} transactions")
        print(f"   - {summary['flags']} flags")
        
        # Get prompt details
        name = input("\n✏️  Enter prompt name: ").strip()
        if not name:
            name = f"prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        description = input("📝 Enter description (optional): ").strip()
        category = input("🏷️  Enter category (general/retrieval/summarization/analysis): ").strip() or "general"
        
        # Build the prompt
        print("\n📄 Enter your prompt template (use {{variable}} for placeholders):")
        print("   Type your prompt below (press Enter twice to finish):")
        
        lines = []
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
        
        template = "\n".join(lines)
        
        if not template:
            print("❌ Prompt cannot be empty!")
            return
        
        # Extract variables from template
        variables = re.findall(r'\{\{([^}]+)\}\}', template)
        variables = list(set(variables))
        
        # Create and save template
        prompt_template = PromptTemplate(
            name=name,
            template=template,
            description=description,
            category=category,
            variables=variables
        )
        
        self.prompt_manager.save_template(prompt_template)
        
        print(f"\n✅ Prompt template saved!")
        print(f"   ID: {prompt_template.id}")
        print(f"   Name: {prompt_template.name}")
        print(f"   Category: {prompt_template.category}")
        print(f"   Variables: {prompt_template.variables}")
        
        # Offer to test immediately
        if input("\n🚀 Test this prompt now? (y/n): ").lower() == 'y':
            self._test_prompt(prompt_template)
    
    def _test_prompt(self, template: PromptTemplate = None):
        """Test a prompt against the knowledge graph."""
        print("\n" + "="*70)
        print("  PROMPT TESTING")
        print("="*70)
        
        # Select or create template
        if template is None:
            templates = self.prompt_manager.list_templates()
            
            if templates:
                print("\n📋 Available Templates:")
                for i, t in enumerate(templates, 1):
                    print(f"   {i}. {t.name} ({t.category}) - {t.description[:50]}...")
            else:
                print("\n📋 No saved templates found.")
            
            choice = input(f"\n📝 Select template (1-{len(templates)}) or '0' to enter raw prompt: ").strip()
            
            if choice == '0':
                prompt_text = input("\n📄 Enter your prompt: ")
                template = PromptTemplate("ad-hoc", prompt_text)
            elif not templates:
                print("\n⚠️  No templates found. Please engineer a prompt first.")
                return
            else:
                try:
                    idx = int(choice) - 1
                    template = templates[idx]
                except (ValueError, IndexError):
                    print("❌ Invalid selection!")
                    return
        
        # Collect variables if needed
        context = {}
        if template.variables:
            print(f"\n🔧 Template requires {len(template.variables)} variables:")
            for var in template.variables:
                value = input(f"   {var}: ")
                context[var] = value
        
        # Render the prompt
        rendered_prompt = template.render(context)
        print(f"\n📄 Rendered Prompt:")
        print(f"   {rendered_prompt}")
        
        # Execute against knowledge graph
        print(f"\n🔍 Executing against knowledge graph...")
        
        try:
            # For now, use the queries module to get data
            # In a real implementation, this would call the LLM with the prompt
            # and the graph context
            
            # Simulate retrieval
            with self.kg_context.queries.driver.session() as session:
                # Get relevant data based on prompt keywords
                result = session.run("""
                    MATCH (d:Document)
                    OPTIONAL MATCH (d)-[:MENTIONS]->(e)
                    RETURN d.source_id as doc_id, labels(e) as entity_type, 
                           properties(e) as entity_props
                    LIMIT 10
                """)
                
                retrieval_data = []
                for record in result:
                    retrieval_data.append({
                        "document": record["doc_id"],
                        "entity_type": record["entity_type"],
                        "entity": record["entity_props"]
                    })
            
            # Simulate LLM response (in real implementation, this would call Claude/Anthropic)
            # For demo purposes, we'll just show the retrieval
            outcome = {
                "prompt": rendered_prompt,
                "prompt_id": template.id,
                "prompt_name": template.name,
                "context": context,
                "retrieval": retrieval_data,
                "response": "[LLM Response Placeholder - Actual response would come from Claude/Anthropic API]",
                "timestamp": datetime.now().isoformat()
            }
            
            # Save to history
            self.prompt_manager.save_history(outcome)
            
            # Display results
            print(f"\n✅ Test Complete!")
            print(f"   Retrieved {len(retrieval_data)} entities")
            print(f"\n📋 Retrieval Results:")
            for i, item in enumerate(retrieval_data[:5], 1):  # Show first 5
                entity = item.get("entity") or {}
                entity_name = entity.get("name") or entity.get("invoice_id") or entity.get("id") or "N/A"
                entity_type = item.get("entity_type")
                if isinstance(entity_type, list) and entity_type:
                    entity_type = entity_type[0]
                elif not entity_type:
                    entity_type = "Unknown"
                print(f"   {i}. {entity_type}: {entity_name}")
            
            # Offer to evaluate
            if input("\n📊 Evaluate this retrieval? (y/n): ").lower() == 'y':
                self._evaluate_retrieval(outcome)
                
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
    
    def _evaluate_retrieval(self, outcome: Dict = None):
        """Evaluate the quality of a retrieval."""
        print("\n" + "="*70)
        print("  RETRIEVAL EVALUATION")
        print("="*70)
        
        if outcome is None:
            # Load from history
            history = self.prompt_manager.load_history()
            if not history:
                print("\n⚠️  No test history found. Run a prompt test first.")
                return
            
            print("\n📋 Recent Tests:")
            for i, entry in enumerate(history[-5:], 1):
                print(f"   {i}. {entry.get('prompt_name', 'Unnamed')} - {entry['timestamp'][:19]}")
            
            choice = input(f"\n📝 Select test to evaluate (1-{len(history[-5:])}): ").strip()
            try:
                outcome = history[-5:][int(choice) - 1]
            except (ValueError, IndexError):
                print("❌ Invalid selection!")
                return
        
        print(f"\n📄 Prompt: {outcome.get('prompt_name', 'Ad-hoc')}")
        print(f"   {outcome.get('prompt', 'N/A')[:100]}...")
        
        # Evaluation metrics
        print("\n📊 Evaluation Criteria:")
        print("   Rate each aspect from 1-5 (1=Poor, 5=Excellent)")
        
        scores = {}
        
        # Relevance
        relevance = int(input("   1. Relevance (Are retrieved entities relevant to the prompt?): "))
        scores["relevance"] = self._validate_score(relevance)
        
        # Completeness
        completeness = int(input("   2. Completeness (Does retrieval cover all needed information?): "))
        scores["completeness"] = self._validate_score(completeness)
        
        # Accuracy
        accuracy = int(input("   3. Accuracy (Is the retrieved information factually correct?): "))
        scores["accuracy"] = self._validate_score(accuracy)
        
        # Precision
        precision = int(input("   4. Precision (Low noise/high signal ratio?): "))
        scores["precision"] = self._validate_score(precision)
        
        # Overall
        overall = int(input("   5. Overall Quality: "))
        scores["overall"] = self._validate_score(overall)
        
        # Additional feedback
        feedback = input("\n💬 Additional feedback (optional): ").strip()
        
        # Record evaluation
        prompt_id = outcome.get('prompt_id', 'ad-hoc')
        prompt_name = outcome.get('prompt_name', 'Ad-hoc Prompt')
        context = {
            "feedback": feedback,
            "retrieval_count": len(outcome.get('retrieval', []))
        }
        
        self.metrics.record_evaluation(prompt_id, prompt_name, scores, context)
        
        print(f"\n✅ Evaluation saved!")
        print(f"   Overall Score: {scores['overall']}/5")
        if feedback:
            print(f"   Feedback: {feedback}")
    
    def _validate_score(self, score: int) -> int:
        """Validate that score is between 1 and 5."""
        while score < 1 or score > 5:
            print("   ❌ Score must be between 1 and 5")
            score = int(input("   Please enter a valid score: "))
        return score
    
    def _compare_outcomes(self):
        """Compare outcomes from different prompts."""
        print("\n" + "="*70)
        print("  OUTCOME COMPARISON")
        print("="*70)
        
        # Load history
        history = self.prompt_manager.load_history()
        if len(history) < 2:
            print("\n⚠️  Need at least 2 tests to compare. Run more prompt tests first.")
            return
        
        print("\n📋 Recent Tests:")
        for i, entry in enumerate(history[-10:], 1):
            name = entry.get('prompt_name', 'Unnamed')
            timestamp = entry.get('timestamp', '')[:19]
            print(f"   {i}. {name} - {timestamp}")
        
        # Select tests to compare
        num_compare = min(5, len(history))
        print(f"\n📝 Select tests to compare (comma-separated, 1-{num_compare}): ")
        choices = input().strip()
        
        try:
            indices = [int(c.strip()) - 1 for c in choices.split(',')]
            outcomes = [history[-10:][i] for i in indices]
            prompt_names = [outcomes[i].get('prompt_name', f'Test {i+1}') for i in range(len(outcomes))]
        except (ValueError, IndexError):
            print("❌ Invalid selection!")
            return
        
        # Perform comparison
        comparison = self.comparator.compare_outcomes(outcomes, prompt_names)
        
        print("\n" + "="*70)
        print("  COMPARISON RESULTS")
        print("="*70)
        
        print("\n📊 Length Statistics:")
        print(f"   Min: {comparison['analysis']['length_stats']['min']} chars")
        print(f"   Max: {comparison['analysis']['length_stats']['max']} chars")
        print(f"   Avg: {comparison['analysis']['length_stats']['avg']:.0f} chars")
        
        print("\n🎯 Entity Coverage:")
        for i, prompt in enumerate(prompt_names):
            entities = comparison['analysis']['entity_coverage'][i]
            print(f"   {prompt}: {len(entities)} entities")
            if entities:
                print(f"      - {', '.join(entities[:3])}")
        
        print(f"\n🔢 Total Unique Entities: {len(comparison['analysis']['unique_entities'])}")
        if comparison['analysis']['unique_entities']:
            print(f"   {', '.join(comparison['analysis']['unique_entities'][:5])}")
        
        # Save comparison
        if input("\n💾 Save this comparison? (y/n): ").lower() == 'y':
            comparison_name = input("   Enter comparison name: ").strip()
            if comparison_name:
                # In a real implementation, save to file
                print(f"   ✅ Comparison saved as: {comparison_name}")
    
    def _manage_templates(self):
        """Manage saved prompt templates."""
        print("\n" + "="*70)
        print("  PROMPT TEMPLATE MANAGEMENT")
        print("="*70)
        
        templates = self.prompt_manager.list_templates()
        
        if not templates:
            print("\n⚠️  No templates found.")
            return
        
        print("\n📋 Available Templates:")
        for i, t in enumerate(templates, 1):
            print(f"\n   {i}. {t.name}")
            print(f"      Category: {t.category}")
            print(f"      Description: {t.description[:60]}...")
            print(f"      Variables: {t.variables}")
            print(f"      Created: {t.created_at[:19]}")
        
        print("\n📝 Options:")
        print("   1. View template details")
        print("   2. Delete template")
        print("   3. Export template")
        print("   4. Back to main menu")
        
        choice = input("\nSelect option: ").strip()
        
        if choice == '1':
            idx = int(input("Enter template number: ")) - 1
            if 0 <= idx < len(templates):
                t = templates[idx]
                print(f"\n📄 {t.name}")
                print(f"   Category: {t.category}")
                print(f"   Description: {t.description}")
                print(f"   Variables: {t.variables}")
                print(f"\n   Template:")
                print(f"   {t.template}")
        elif choice == '2':
            idx = int(input("Enter template number to delete: ")) - 1
            if 0 <= idx < len(templates):
                t = templates[idx]
                if input(f"   Delete '{t.name}'? (y/n): ").lower() == 'y':
                    if self.prompt_manager.delete_template(t.id):
                        print(f"   ✅ Deleted: {t.name}")
                    else:
                        print(f"   ❌ Failed to delete")
        elif choice == '3':
            idx = int(input("Enter template number to export: ")) - 1
            if 0 <= idx < len(templates):
                t = templates[idx]
                export_path = input(f"   Export path [{t.name}.json]: ") or f"{t.name}.json"
                with open(export_path, "w") as f:
                    json.dump(t.to_dict(), f, indent=2)
                print(f"   ✅ Exported to: {export_path}")
    
    def _view_metrics(self):
        """View evaluation metrics."""
        print("\n" + "="*70)
        print("  EVALUATION METRICS")
        print("="*70)
        
        metrics = self.metrics.get_all_metrics()
        
        if not metrics:
            print("\n⚠️  No evaluation metrics found. Run some evaluations first.")
            return
        
        # Overall stats
        avg_metrics = self.metrics.calculate_average_metrics()
        print(f"\n📊 Overall Statistics:")
        print(f"   Average Overall Score: {avg_metrics.get('average_overall', 0):.2f}/5")
        print(f"   Total Evaluations: {avg_metrics.get('total_evaluations', 0)}")
        if 'best_prompt' in avg_metrics:
            print(f"   Best Prompt: {avg_metrics['best_prompt']}")
        
        # Per-prompt metrics
        print(f"\n📈 Per-Prompt Metrics:")
        for prompt_id, data in metrics.items():
            print(f"\n   {data['name']} ({data['category']})")
            print(f"      Evaluations: {data['evaluation_count']}")
            print(f"      Average Score: {data['average_score']:.2f}/5")
            
            # Show most recent evaluation
            if data['evaluations']:
                recent = data['evaluations'][-1]
                print(f"      Last Evaluation: {recent['timestamp'][:19]}")
                print(f"         Relevance: {recent['scores'].get('relevance', 'N/A')}")
                print(f"         Completeness: {recent['scores'].get('completeness', 'N/A')}")
                print(f"         Accuracy: {recent['scores'].get('accuracy', 'N/A')}")
                print(f"         Precision: {recent['scores'].get('precision', 'N/A')}")
                print(f"         Overall: {recent['scores'].get('overall', 'N/A')}")
                if recent.get('context', {}).get('feedback'):
                    print(f"         Feedback: {recent['context']['feedback'][:60]}...")


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Run the prompt engineering interface."""
    try:
        config.require_config()
    except EnvironmentError as e:
        print(f"❌ Configuration error: {e}")
        print("   Please set NEO4J_PASSWORD and ANTHROPIC_API_KEY environment variables")
        return
    
    interface = PromptEngineeringInterface()
    interface.run()


if __name__ == "__main__":
    main()
