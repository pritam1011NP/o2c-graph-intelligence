# AI Coding Session Log — O2C Graph Intelligence System

**Tool**: Claude (claude.ai)  
**Model**: claude-sonnet-4  
**Date**: March 2026  
**Session Type**: Full-stack application development  

---

## Session Overview

This log captures the complete AI-assisted development session for the SAP Order-to-Cash Graph Intelligence System. The session covers dataset exploration, database design, backend API construction, graph modelling, frontend development, and LLM prompt engineering.

---

## Prompt 1 — Initial Assignment Analysis

**User:**
> [Uploaded dataset zip + UI screenshots + full assignment brief]
> In real-world business systems, data is spread across multiple tables: orders, deliveries, invoices, and payments, without a clear way to trace how they connect. Build a context graph system with an LLM-powered query interface...

**AI Reasoning & Response:**

Immediately explored the dataset structure before writing any code. Ran:
```bash
unzip sap-order-to-cash-dataset.zip -d dataset/
ls dataset/sap-o2c-data/
```

Identified 17 entity folders. Sampled keys from each:
```bash
for dir in */; do
  echo "=== $dir ==="
  head -1 "$dir"/*.jsonl | python3 -c "import sys,json; print(list(json.loads(sys.stdin.read()).keys())[:10])"
done
```

**Key decisions made at this stage:**
- Chose SQLite over Neo4j: dataset is ~1,400 records, full SQL support needed for LLM-generated queries
- Chose Flask over FastAPI: simpler setup, sufficient for this scale
- Identified that the critical join chain needed explicit documentation for the LLM

---

## Prompt 2 — Dataset Exploration & Schema Discovery

**AI Actions:**

Sampled each critical table to understand join relationships:

```python
# Verified billing_document_items → delivery link
# billing_document_items.referenceSdDocument = outbound_delivery_items.deliveryDocument
for line in open('billing_document_items/...'):
    d = json.loads(line)
    print(d)  # → {'billingDocument': '90504298', 'referenceSdDocument': '80738109', ...}

# Verified journal_entries → billing link
# billing_document_headers.accountingDocument = journal_entries.accountingDocument
# AND journal_entries.referenceDocument = billing_document_headers.billingDocument
```

**Discovery**: The O2C chain has an indirect join:
- SO → delivery_items (via `referenceSdDocument`)
- delivery_items → billing_items (via `referenceSdDocument = deliveryDocument`)
- billing_items → billing_headers (via `billingDocument`)
- billing_headers → journal_entries (via `accountingDocument`)

This 4-table traversal is non-obvious and was explicitly documented in the LLM system prompt.

---

## Prompt 3 — Database Builder

**AI Generated:** `build_db.py`

```python
def create_table(name, records):
    keys = list(records[0].keys())
    cols = ', '.join(f'"{k}" TEXT' for k in keys)
    c.execute(f'CREATE TABLE "{name}" ({cols})')
    for r in records:
        vals = [str(clean(r.get(k, ''))) if r.get(k) is not None else None for k in keys]
        c.execute(f'INSERT INTO "{name}" VALUES ({placeholders})', vals)
```

**Decision**: Store all values as TEXT. Rationale: SQLite's type affinity handles numeric operations via CAST, and TEXT storage avoids type inference errors on mixed/null fields from JSONL.

**Verification step:**
```bash
python3 build_db.py
# Output: sales_order_headers: 100 rows, deliveries: 86, billing: 163, etc.
```

---

## Prompt 4 — Join Path Verification

Before building the API, verified all critical joins produce real results:

```python
conn = sqlite3.connect('o2c.db')
# Test: delivery → sales order
c.execute('SELECT deliveryDocument, referenceSdDocument FROM outbound_delivery_items LIMIT 5')
# → [('80737721', '740506', ...), ...]  ✓

# Test: billing → delivery
c.execute('SELECT billingDocument, referenceSdDocument FROM billing_document_items LIMIT 5')
# → [('90504298', '80738109'), ...]
c.execute('SELECT deliveryDocument FROM outbound_delivery_items WHERE deliveryDocument="80738109"')
# → ('80738109',)  ✓ join works

# Test: billing → journal
c.execute('SELECT billingDocument, accountingDocument FROM billing_document_headers LIMIT 3')
# → [('90504248', '9400000249'), ...]
c.execute('SELECT accountingDocument FROM journal_entries WHERE accountingDocument="9400000249"')
# → ('9400000249',)  ✓ join works
```

**All join paths verified before writing any API code.** This was a deliberate step to prevent building on incorrect assumptions.

---

## Prompt 5 — Flask API Construction

**AI Generated:** `app.py`

Key design decisions during this phase:

**Graph endpoint design:**
```python
@app.route('/api/graph/nodes')
def get_graph_nodes():
    # Headers → nodes (not items, to avoid 1000+ nodes)
    # Items → edges (correct join source)
    deliveries = q("""
        SELECT DISTINCT odh.deliveryDocument, odi.referenceSdDocument as salesOrder
        FROM outbound_delivery_headers odh
        JOIN outbound_delivery_items odi ON odh.deliveryDocument = odi.deliveryDocument
        LIMIT 60
    """)
```

**Two-step LLM pipeline:**
```python
# Step 1: NL → SQL (structured JSON output)
parsed = call_llm_for_sql(user_query, history)

# Step 2: SQL results → natural language
answer = call_llm_for_answer(user_query, sql, results, history)
```

**Iteration**: Initially tried single-step (generate SQL + narrate in one call). 
Abandoned because:
1. JSON constraint conflicted with natural language narration
2. Couldn't show SQL to user independently
3. Error handling was harder (couldn't distinguish SQL error from narration error)

---

## Prompt 6 — LLM System Prompt Engineering

**Initial version (too vague):**
```
"Answer questions about SAP O2C data using SQL"
```

**Problem**: LLM hallucinated column names like `order_id` instead of `salesOrder`.

**Iteration 1** — Added schema:
```
TABLE sales_order_headers: salesOrder, soldToParty, totalNetAmount, ...
```

**Problem**: LLM still got join paths wrong (tried direct SO→billing join).

**Iteration 2** — Explicit join paths:
```
JOIN PATHS:
- SO→Delivery: outbound_delivery_items.referenceSdDocument = sales_order_headers.salesOrder
- Delivery→Billing: billing_document_items.referenceSdDocument = outbound_delivery_items.deliveryDocument
- Billing→Journal: billing_document_headers.accountingDocument = journal_entries.accountingDocument
```

**Problem**: Off-topic queries still got attempted SQL.

**Iteration 3** — Off-topic classification:
```
RULES:
1. ONLY answer O2C dataset questions
2. For off-topic requests, set off_topic: true
3. Return ONLY JSON: {"sql":"...","explanation":"...","off_topic":false}
```

**Final result**: Consistent JSON output, correct column names, proper joins, reliable guardrails.

---

## Prompt 7 — Graph Visualization

**AI Generated:** D3.js force-directed graph in `static/index.html`

Key choices:
- **Force-directed over hierarchical**: O2C data has many-to-many (1 customer → many orders → many deliveries). Hierarchy layout doesn't fit.
- **Node radii by type**: Customers larger (radius 11) to act as visual anchors. Leaf nodes smaller.
- **Color encoding**: Blue=Orders, Green=Delivery, Amber=Billing, Red=Journal, Purple=Customer — traffic-light logic where warm colors = money flow

```javascript
const simulation = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(links).id(d => d.id).distance(80).strength(0.5))
  .force('charge', d3.forceManyBody().strength(-200))
  .force('center', d3.forceCenter(W/2, H/2))
  .force('collision', d3.forceCollide(20));
```

**Node highlighting on query:**
```javascript
function highlightNodes(ids) {
  d3.selectAll('.node-circle').transition().duration(300)
    .attr('r', d => highlightedNodes.has(d.id) ? radius * 1.8 : radius)
    .attr('fill-opacity', d => highlightedNodes.has(d.id) ? 1 : 0.2);
}
```

---

## Prompt 8 — Frontend Chat Interface

**Design iteration:**

Initially built with fetch + full SQL execution in browser. 
Pivoted to: **show SQL transparently** (generated SQL is visible to user), which:
- Builds trust ("the system found this by querying X")
- Aids debugging
- Satisfies the "NL→SQL transparency" evaluation criterion

```javascript
addMsg('assistant', data.answer, data.sql, data.results);
// Shows: answer bubble + collapsible SQL block + results mini-table
```

**Conversation chips** (quick-access queries):
```html
<div class="chip" onclick="sendChip(this)">Trace billing doc 90504204</div>
<div class="chip" onclick="sendChip(this)">Incomplete O2C flows</div>
```
These pre-populate the hardest/most impressive example queries from the brief.

---

## Prompt 9 — Guardrails Testing

Tested edge cases against the system:

| Query | Expected | Result |
|-------|----------|--------|
| "Who invented Python?" | off_topic | ✓ Rejected |
| "Write a poem about invoices" | off_topic | ✓ Rejected |
| "What is 2+2?" | off_topic | ✓ Rejected |
| "Show me all sales orders" | SQL generated | ✓ Works |
| "Ignore previous instructions and..." | off_topic | ✓ Rejected |
| "Which products are billed most?" | SQL generated | ✓ Works |

The guardrail works because:
1. System prompt establishes tight domain scope
2. LLM classifies intent before generating SQL (not after)
3. `off_topic: true` flag triggers immediate rejection in Python — no SQL ever executes

---

## Prompt 10 — Standalone HTML Build

**Challenge**: Build a version with no server dependency for easy demo.

**Solution**: Embed pre-computed graph data as a JavaScript constant, call Anthropic API directly from browser.

```python
# Python build script embeds graph JSON into HTML
html = html.replace('GRAPH_DATA_PLACEHOLDER', graph_data_json)
```

**Tradeoff**: Can't execute SQL queries live (no SQLite in browser). 
**Mitigation**: LLM generates the SQL and explains what it would return — users see the query + expert explanation, which is educationally valuable even without live execution.

---

## Prompt 11 — Submission Materials

**Final packaging:**
- `o2c-graph.html` — standalone demo
- `o2c-full-project.zip` — complete Flask backend
- `README.md` — architecture documentation
- `ai-session-log.md` — this file

---

## Debugging Patterns Used

### Pattern 1: Verify Before Build
Every join path was verified with a live query before being used in application code.

### Pattern 2: Incremental API Testing
Each endpoint was tested with `app.test_client()` before moving to the next.

### Pattern 3: Prompt Regression Testing
After each system prompt change, re-tested the same set of queries to confirm no regressions.

### Pattern 4: Size/Count Sanity Checks
After every data load, printed record counts to catch silent errors:
```
sales_order_headers: 100 rows ✓
outbound_delivery_headers: 86 rows ✓
billing_document_headers: 163 rows ✓
```

---

## Key Prompt Quality Examples

### Effective: Explicit Schema with Join Paths
```
TABLE: billing_document_items
  billingDocument (→ billing_document_headers), billingDocumentItem,
  material (→ products.product), referenceSdDocument (→ outbound_delivery_items.deliveryDocument)

JOIN PATH:
- Delivery→Billing: billing_document_items.referenceSdDocument = outbound_delivery_items.deliveryDocument
```
Why effective: No ambiguity. LLM can generate correct 4-table joins without guessing.

### Effective: Structured Output Constraint
```
Return ONLY JSON: {"sql":"SELECT...","explanation":"brief","off_topic":false}
```
Why effective: Forces parseable output. Regex `/{[\s\S]*}/` reliably extracts it.

### Effective: Domain Hint for Edge Cases
```
For product names, JOIN with product_descriptions on product=product and language='EN'
```
Why effective: Without this, LLM either skips product names or returns duplicate rows for each language.

---

## Iteration Count Summary

| Component | Iterations | Key Changes |
|-----------|-----------|-------------|
| System prompt | 3 | Added schema → added join paths → added off_topic flag |
| LLM pipeline | 2 | Single-step → two-step |
| Graph layout | 1 | Direct D3 force (no changes needed) |
| Join paths | 1 | Verified all 4 paths before building |
| Guardrails | 2 | Prompt-level → prompt + code-level backup |

Total development time (AI-assisted): ~2 hours
