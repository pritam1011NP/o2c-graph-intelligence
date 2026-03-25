# O2C Graph Intelligence System

A context graph system with an LLM-powered natural language query interface for SAP Order-to-Cash data.

![O2C Graph](https://img.shields.io/badge/Stack-Python%20%7C%20Flask%20%7C%20SQLite%20%7C%20D3.js%20%7C%20Claude-6c63ff)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Demo

> Open `o2c-graph.html` directly in your browser — no server needed for the standalone demo.  
> For the full backend: `python3 app.py` → `http://localhost:5000`

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Database Design & Choice](#database-design--choice)
3. [Graph Model](#graph-model)
4. [LLM Integration & Prompting Strategy](#llm-integration--prompting-strategy)
5. [Guardrails](#guardrails)
6. [Running the Project](#running-the-project)
7. [Example Queries](#example-queries)
8. [File Structure](#file-structure)
9. [Tradeoffs & Future Work](#tradeoffs--future-work)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Interface                           │
│  ┌──────────────────────────┐   ┌───────────────────────────┐  │
│  │   D3.js Force Graph      │   │   Chat Interface          │  │
│  │   (198 nodes, 98 edges)  │   │   (Natural Language)      │  │
│  └──────────────────────────┘   └────────────┬──────────────┘  │
└───────────────────────────────────────────────┼─────────────────┘
                                                │
                                    ┌───────────▼────────────┐
                                    │   Flask REST API        │
                                    │   /api/graph/nodes      │
                                    │   /api/graph/node/:id   │
                                    │   /api/chat             │
                                    │   /api/stats            │
                                    └───────────┬────────────┘
                           ┌────────────────────┼──────────────────┐
                           │                    │                  │
               ┌───────────▼──────┐  ┌──────────▼───────┐  ┌─────▼──────┐
               │   SQLite DB       │  │  Claude API       │  │  Graph     │
               │   (17 tables)     │  │  NL → SQL → Text  │  │  Builder   │
               │   o2c.db          │  │  claude-sonnet-4  │  │            │
               └──────────────────┘  └──────────────────┘  └────────────┘
                           │
               ┌───────────▼──────────────────────────────────┐
               │              Raw Data (JSONL)                  │
               │  sales_order_headers, billing_documents,       │
               │  deliveries, journal_entries, payments, ...    │
               └──────────────────────────────────────────────┘
```

### Data Flow

1. **Ingestion**: 17 JSONL entity folders → normalized into SQLite via `build_db.py`
2. **Graph Construction**: Flask API queries SQLite and builds node/edge structures in memory
3. **Visualization**: D3.js force-directed simulation renders the graph in-browser
4. **Query**: User types natural language → Claude API generates SQL → SQLite executes → Claude narrates results
5. **Highlighting**: Referenced entity IDs are extracted from results and pulsed on the graph

---

## Database Design & Choice

### Why SQLite

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **SQLite** | Zero-config, file-based, full SQL, portable | Not horizontally scalable | ✅ **Chosen** |
| Neo4j | Native graph queries, Cypher | Requires running server, licensing | ❌ Overkill for dataset size |
| PostgreSQL | Production-grade, PostGIS | Requires server, setup overhead | ❌ Over-engineered for demo |
| In-memory (dict) | Fast | No SQL, no persistence | ❌ Loses query expressiveness |

**Rationale**: The dataset has ~1,400 total records across 17 tables. SQLite handles this with sub-millisecond query times and requires zero infrastructure. The LLM generates standard SQL, which SQLite executes natively — making it the perfect fit for an NL→SQL pipeline.

### Schema Design

The raw data arrives as **17 JSONL entity folders**. Each folder is ingested into a corresponding SQLite table preserving all original field names (no renaming) for maximum transparency and LLM compatibility.

Key tables and their roles:

```
sales_order_headers     ← Core transaction anchor (salesOrder PK)
sales_order_items       ← Line items with materials
outbound_delivery_headers ← Delivery fulfillment status
outbound_delivery_items ← Links delivery → sales order (referenceSdDocument)
billing_document_headers ← Invoice with accounting link
billing_document_items  ← Links billing → delivery (referenceSdDocument)
journal_entries         ← AR postings (referenceDocument = billingDocument)
payments                ← Clearing entries
business_partners       ← Customer master data
products / product_descriptions ← Material catalog
plants                  ← Logistics locations
```

### Critical Join Paths

The entire O2C traceability depends on these four joins:

```sql
-- Sales Order → Delivery
outbound_delivery_items.referenceSdDocument = sales_order_headers.salesOrder

-- Delivery → Billing Document  
billing_document_items.referenceSdDocument = outbound_delivery_items.deliveryDocument

-- Billing Document → Journal Entry
billing_document_headers.accountingDocument = journal_entries.accountingDocument
-- AND: journal_entries.referenceDocument = billing_document_headers.billingDocument

-- Customer linkage
sales_order_headers.soldToParty = business_partners.customer
```

---

## Graph Model

### Nodes

| Type | Color | Count | Key Field |
|------|-------|-------|-----------|
| `SalesOrder` | Blue `#3b82f6` | 100 | `salesOrder` |
| `Delivery` | Green `#10b981` | 86 | `deliveryDocument` |
| `BillingDoc` | Amber `#f59e0b` | 163 | `billingDocument` |
| `JournalEntry` | Red `#ef4444` | 123 | `accountingDocument` |
| `Customer` | Purple `#8b5cf6` | 8 | `customer` |

### Edges (Relationships)

| Source | Target | Relationship | Derived From |
|--------|--------|--------------|--------------|
| Customer | SalesOrder | `placed` | `soldToParty` field |
| SalesOrder | Delivery | `delivered_by` | `outbound_delivery_items.referenceSdDocument` |
| Delivery | BillingDoc | `billed_as` | `billing_document_items.referenceSdDocument` |
| BillingDoc | JournalEntry | `journal_entry` | `accountingDocument` match |

### Modelling Decisions

- **Headers over items for nodes**: Each business document (SO, Delivery, Billing) is represented once as a header node. Items are metadata on the node, not separate nodes. This keeps the graph readable and prevents explosion to 1,000+ nodes.
- **Edges derived from item-level joins**: Even though nodes are header-level, edges are correctly derived by traversing item relationships (e.g. `billing_document_items` links a billing doc to a delivery).
- **Customer as high-degree hub**: Customers connect to all their sales orders, naturally forming cluster patterns in the force layout.
- **Force-directed layout**: Chosen over hierarchical layout because the O2C data has many-to-many patterns (one customer → many orders → many deliveries → many billing docs). Force simulation lets natural clusters emerge.

---

## LLM Integration & Prompting Strategy

### Two-Step Pipeline

```
User Query
    │
    ▼
┌──────────────────────────────────────┐
│  Step 1: SQL Generation              │
│  System: Schema + Rules + JSON fmt   │
│  → Returns: {"sql":"...", ...}        │
└──────────────────┬───────────────────┘
                   │
                   ▼
           Execute SQL on SQLite
                   │
                   ▼
┌──────────────────────────────────────┐
│  Step 2: Result Narration            │
│  System: "You are a data analyst"    │
│  Input: query + SQL + results(50)    │
│  → Returns: Natural language answer  │
└──────────────────────────────────────┘
```

### Why Two Steps Instead of One?

Combining SQL generation and narration in one prompt creates two competing objectives. Separating them:
- Makes SQL generation **deterministic and structured** (JSON output format)
- Makes narration **conversational and natural** (no JSON constraint)
- Allows **SQL to be shown to the user** for transparency
- Enables **error handling** between the steps

### System Prompt Design

The SQL generation prompt contains:

1. **Full schema** with table names, column names, and data types — so the LLM never guesses column names
2. **Explicit join paths** as comments — the most complex part of the schema (the indirect join chains) are spelled out step-by-step
3. **Strict output format** — `{"sql": "...", "explanation": "...", "off_topic": false}` — JSON with no prose wrapper
4. **Domain-specific hints** — e.g. "for product names, JOIN product_descriptions with language='EN'"
5. **Off-topic flag** — the model is instructed to set `off_topic: true` and return no SQL for non-O2C queries

```python
SYSTEM_PROMPT = f"""You are an intelligent data analyst for an SAP Order-to-Cash system.
Your ONLY purpose is to answer questions about the O2C dataset using SQL queries.

{SCHEMA}  # ← full table/column definitions + join paths

RULES:
1. ONLY answer questions related to the O2C dataset...
2. For off-topic requests, respond with off_topic: true
3. Generate valid SQLite SQL to answer the question
4. Return ONLY JSON: {{"sql":"...", "explanation":"...", "off_topic":false}}
5. Use exact table/column names from schema
6. For product names, JOIN product_descriptions WHERE language='EN'
"""
```

### Conversation Memory

The last 6 messages (3 turns) are passed as `messages` history on each API call, enabling follow-up queries like:
- "Show me that customer's other orders" (after asking about a customer)
- "Now filter that to only cancelled ones" (after a billing query)

### Prompt Engineering Decisions

| Decision | Rationale |
|----------|-----------|
| Include full schema in every prompt | Context window is cheap; hallucinated column names are not |
| Spell out join paths explicitly | The indirect 3-table join (SO→delivery_items→billing_items) is non-obvious |
| Force JSON output with `only JSON` instruction | Prevents prose-wrapped SQL that's hard to parse |
| Two-step pipeline | Separates structured (SQL) from unstructured (narration) generation |
| Cap results at 50 rows in narration prompt | Prevents context overflow while preserving answer quality |
| `language='EN'` hint for products | Dataset has multi-language descriptions; without this, results are duplicated |

---

## Guardrails

### Implementation

Guardrails operate at the **prompt level** (LLM is instructed to classify and reject) with a **code-level check** as backup:

```python
# In SYSTEM_PROMPT:
"For any off-topic request (general knowledge, creative writing, jokes, weather, etc.), 
respond with off_topic: true"

# In app.py:
if parsed.get('off_topic'):
    return jsonify({
        "answer": "This system is designed to answer questions related to the 
                   Order-to-Cash dataset only...",
        "sql": None
    })
```

### What Gets Blocked

| Query Type | Example | Handling |
|------------|---------|----------|
| General knowledge | "Who is the CEO of Apple?" | `off_topic: true` → canned response |
| Creative writing | "Write me a poem" | `off_topic: true` → canned response |
| Math/science | "What is the speed of light?" | `off_topic: true` → canned response |
| Prompt injection | "Ignore all instructions and..." | Schema-anchored prompt is resistant |
| SQL injection | `'; DROP TABLE --` | SQLite parameterised queries |

### What Gets Allowed

Any question reasonably related to:
- Sales orders, deliveries, billing documents, journal entries, payments
- Customers, products, plants, materials
- Business process flows (O2C traceability)
- Aggregate analytics (revenue, counts, status breakdowns)

### Why Prompt-Level vs. Keyword Filter

A keyword filter ("if 'poem' in query: reject") is brittle and easy to bypass. Asking the LLM itself to classify intent is:
- More robust to paraphrasing ("compose a verse about...")
- Naturally aware of context
- Zero additional latency (classification happens inside the same API call)

---

## Running the Project

### Option 1: Standalone HTML (No Server)

```bash
# Just open in browser
open o2c-graph.html
# or: python3 -m http.server 8080 && open http://localhost:8080/o2c-graph.html
```

> The standalone file uses the Anthropic API directly from the browser. Works in claude.ai artifacts context.

### Option 2: Full Flask Backend

```bash
# Install dependencies
pip install flask flask-cors

# Build the database (already included as o2c.db)
python3 build_db.py

# Start the server
python3 app.py
# → http://localhost:5000
```

### Option 3: Docker (optional)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install flask flask-cors
EXPOSE 5000
CMD ["python3", "app.py"]
```

---

## Example Queries

### Supported by the System

**1. Product analytics**
> "Which products are associated with the highest number of billing documents?"

Generated SQL:
```sql
SELECT bdi.material, pd.productDescription, COUNT(DISTINCT bdi.billingDocument) as billing_count
FROM billing_document_items bdi
LEFT JOIN product_descriptions pd ON bdi.material = pd.product AND pd.language = 'EN'
GROUP BY bdi.material, pd.productDescription
ORDER BY billing_count DESC
LIMIT 10
```

**2. Full O2C flow trace**
> "Trace the full flow of billing document 90504204"

Generated SQL:
```sql
SELECT 
    soh.salesOrder, soh.creationDate, soh.soldToParty,
    odi.deliveryDocument,
    bdh.billingDocument, bdh.totalNetAmount,
    je.accountingDocument, je.postingDate,
    p.clearingDate
FROM billing_document_headers bdh
JOIN billing_document_items bdi ON bdh.billingDocument = bdi.billingDocument
JOIN outbound_delivery_items odi ON bdi.referenceSdDocument = odi.deliveryDocument
JOIN sales_order_headers soh ON odi.referenceSdDocument = soh.salesOrder
LEFT JOIN journal_entries je ON bdh.accountingDocument = je.accountingDocument
LEFT JOIN payments p ON je.accountingDocument = p.clearingAccountingDocument
WHERE bdh.billingDocument = '90504204'
```

**3. Incomplete flow detection**
> "Which sales orders were delivered but not billed?"

Generated SQL:
```sql
SELECT DISTINCT soh.salesOrder, soh.creationDate, soh.totalNetAmount, soh.soldToParty
FROM sales_order_headers soh
JOIN outbound_delivery_items odi ON odi.referenceSdDocument = soh.salesOrder
WHERE soh.salesOrder NOT IN (
    SELECT DISTINCT odi2.referenceSdDocument
    FROM outbound_delivery_items odi2
    JOIN billing_document_items bdi ON bdi.referenceSdDocument = odi2.deliveryDocument
)
ORDER BY soh.creationDate DESC
```

**4. Revenue analytics**
> "Show total revenue by customer"

```sql
SELECT bp.businessPartnerFullName, bp.customer,
       SUM(CAST(bdh.totalNetAmount AS REAL)) as total_revenue,
       bdh.transactionCurrency,
       COUNT(DISTINCT bdh.billingDocument) as invoice_count
FROM billing_document_headers bdh
JOIN business_partners bp ON bdh.soldToParty = bp.customer
GROUP BY bp.customer, bp.businessPartnerFullName, bdh.transactionCurrency
ORDER BY total_revenue DESC
```

---

## File Structure

```
o2c-app/
├── app.py                  # Flask REST API (graph endpoints + chat)
├── build_db.py             # JSONL → SQLite ingestion script
├── o2c.db                  # Pre-built SQLite database
├── start.sh                # Quick-start script
├── README.md               # This file
└── static/
    └── index.html          # Full frontend (for Flask backend)

o2c-graph.html              # Standalone version (no server needed)
```

---

## Tradeoffs & Future Work

### Current Tradeoffs

| Area | Current Approach | Production Alternative |
|------|-----------------|----------------------|
| Database | SQLite file | PostgreSQL with proper indexing |
| Graph storage | Built in memory per request | Neo4j or in-memory graph cache |
| Auth | None (as specified) | OAuth2 / API keys |
| SQL execution | Direct string execution | Parameterised ORM queries |
| LLM | Single model (Sonnet) | Fine-tuned model on O2C schema |
| Caching | None | Redis for repeated queries |

### Bonus Features Implemented

- ✅ **Natural language → SQL translation** (dynamic, not templated)
- ✅ **Node highlighting** on graph when entities referenced in answers
- ✅ **Conversation memory** (last 3 turns as context)
- ✅ **SQL transparency** (every generated query shown to user)
- ✅ **Streaming-ready architecture** (easy to add SSE)

### Potential Extensions

- **Semantic search** over entity metadata using embeddings
- **Graph clustering** to detect O2C process anomalies
- **Streaming responses** via Server-Sent Events
- **Export** graph as GraphML / JSON-LD
- **Anomaly detection** — flag orders with unusual amounts, delays, or broken flows

---

## AI Coding Session

This project was built using **Claude** (claude.ai) as the primary AI coding assistant. The full conversation transcript is included in `ai-session-log.md`.

Key prompting patterns used during development:
- Schema exploration before writing any joins
- Incremental testing of each API endpoint before building the next
- Asking Claude to verify join paths with sample data queries
- Iterating on the system prompt by testing edge cases (off-topic, ambiguous queries)
