from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3, json, os, re, requests

app = Flask(__name__, static_folder='static')
CORS(app)

DB_PATH = '/home/claude/o2c-app/o2c.db'
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# ── DB helpers ──────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def q(sql, params=()):
    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ── Schema summary for LLM ──────────────────────────────────────────────────

SCHEMA = """
DATABASE SCHEMA (SAP Order-to-Cash):

TABLE: sales_order_headers
  salesOrder (PK), salesOrderType, salesOrganization, soldToParty (→ business_partners.customer),
  creationDate, totalNetAmount, transactionCurrency, overallDeliveryStatus,
  overallOrdReltdBillgStatus, requestedDeliveryDate

TABLE: sales_order_items
  salesOrder (→ sales_order_headers), salesOrderItem, material (→ products.product),
  requestedQuantity, requestedQuantityUnit, netAmount, transactionCurrency,
  productionPlant, storageLocation, salesDocumentRjcnReason, itemBillingBlockReason

TABLE: outbound_delivery_headers
  deliveryDocument (PK), creationDate, overallGoodsMovementStatus,
  overallPickingStatus, shippingPoint

TABLE: outbound_delivery_items
  deliveryDocument (→ outbound_delivery_headers), deliveryDocumentItem,
  referenceSdDocument (→ sales_order_headers.salesOrder),
  referenceSdDocumentItem (→ sales_order_items.salesOrderItem),
  actualDeliveryQuantity, plant (→ plants.plant), storageLocation

TABLE: billing_document_headers
  billingDocument (PK), billingDocumentType, creationDate, billingDocumentDate,
  billingDocumentIsCancelled, totalNetAmount, transactionCurrency,
  companyCode, fiscalYear, accountingDocument (→ journal_entries.accountingDocument),
  soldToParty (→ business_partners.customer)

TABLE: billing_document_items
  billingDocument (→ billing_document_headers), billingDocumentItem,
  material (→ products.product), billingQuantity, netAmount, transactionCurrency,
  referenceSdDocument (→ outbound_delivery_items.deliveryDocument),
  referenceSdDocumentItem

TABLE: billing_document_cancellations
  billingDocument (PK), billingDocumentIsCancelled, cancelledBillingDocument,
  totalNetAmount, transactionCurrency, companyCode, fiscalYear, accountingDocument

TABLE: journal_entries
  accountingDocument (PK), fiscalYear, glAccount, referenceDocument (→ billing_document_headers.billingDocument),
  transactionCurrency, amountInTransactionCurrency, companyCodeCurrency,
  amountInCompanyCodeCurrency, postingDate, documentDate, accountingDocumentType,
  customer (→ business_partners.customer), clearingDate, clearingAccountingDocument

TABLE: payments
  companyCode, fiscalYear, accountingDocument, accountingDocumentItem,
  clearingDate, clearingAccountingDocument, clearingDocFiscalYear,
  amountInTransactionCurrency, transactionCurrency, amountInCompanyCodeCurrency,
  companyCodeCurrency

TABLE: business_partners
  businessPartner (PK), customer (also a key), businessPartnerFullName,
  businessPartnerName, businessPartnerCategory, creationDate

TABLE: business_partner_addresses
  businessPartner (→ business_partners), addressId, cityName, country,
  streetName, postalCode, region

TABLE: customer_company_assignments
  customer (→ business_partners.customer), companyCode, paymentTerms, reconciliationAccount

TABLE: customer_sales_area_assignments
  customer, salesOrganization, distributionChannel, division,
  currency, customerPaymentTerms, deliveryPriority

TABLE: products
  product (PK), productType, baseUnit, weightUnit, grossWeight, netWeight,
  productGroup, division

TABLE: product_descriptions
  product (→ products), language, productDescription

TABLE: plants
  plant (PK), plantName, salesOrganization, addressId

KEY RELATIONSHIPS (join paths):
- Sales Order → Delivery: outbound_delivery_items.referenceSdDocument = sales_order_headers.salesOrder
- Delivery → Billing: billing_document_items.referenceSdDocument = outbound_delivery_items.deliveryDocument
- Billing → Journal Entry: billing_document_headers.accountingDocument = journal_entries.accountingDocument
  AND journal_entries.referenceDocument = billing_document_headers.billingDocument
- Billing → Payment: payments.clearingAccountingDocument links to journal_entries
- Customer: sales_order_headers.soldToParty = business_partners.customer
- Product: sales_order_items.material = products.product = product_descriptions.product

FULL O2C FLOW: Sales Order → Delivery → Billing Document → Journal Entry → Payment
"""

SYSTEM_PROMPT = f"""You are an intelligent data analyst for an SAP Order-to-Cash (O2C) system.
Your ONLY purpose is to answer questions about the O2C dataset using SQL queries.

{SCHEMA}

RULES:
1. ONLY answer questions related to the O2C dataset and business process.
2. For any off-topic request (general knowledge, creative writing, jokes, weather, etc.), respond with:
   "This system is designed to answer questions related to the Order-to-Cash dataset only. Please ask about sales orders, deliveries, billing documents, payments, customers, or products."
3. Generate valid SQLite SQL queries to answer the question.
4. Always return your response in this exact JSON format:
{{
  "sql": "SELECT ... (or null if off-topic/no SQL needed)",
  "explanation": "Brief explanation of what you're querying",
  "off_topic": false
}}
5. Use table and column names exactly as defined in the schema.
6. For product names, JOIN with product_descriptions on product=product and language='EN'.
7. Keep SQL queries efficient - use LIMIT when appropriate.
8. When tracing full O2C flows, use the relationship chain: SO → delivery_items (referenceSdDocument) → billing_items (referenceSdDocument=deliveryDocument) → billing_headers (accountingDocument) → journal_entries.
"""

def call_llm_for_sql(user_query, conversation_history=None):
    """Use Claude API to translate natural language to SQL"""
    messages = []
    if conversation_history:
        messages.extend(conversation_history[-6:])  # last 3 turns
    messages.append({"role": "user", "content": user_query})
    
    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={"Content-Type": "application/json"},
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": SYSTEM_PROMPT,
            "messages": messages
        }
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["content"][0]["text"]
    
    # Extract JSON from response
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        return json.loads(json_match.group())
    return {"sql": None, "explanation": text, "off_topic": False}

def call_llm_for_answer(user_query, sql, sql_results, conversation_history=None):
    """Use Claude to interpret SQL results and give a natural language answer"""
    result_str = json.dumps(sql_results[:50], indent=2)  # cap at 50 rows
    
    messages = []
    if conversation_history:
        messages.extend(conversation_history[-6:])
    
    messages.append({"role": "user", "content": f"""User question: {user_query}

SQL executed: {sql}

Results ({len(sql_results)} rows total, showing up to 50):
{result_str}

Please provide a clear, concise natural language answer based on these results.
Focus on the key insights. Format numbers nicely. Be specific with IDs and values from the data."""})
    
    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={"Content-Type": "application/json"},
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": "You are a helpful data analyst. Answer questions about SAP O2C data concisely and clearly. Use bullet points for lists. Bold important values.",
            "messages": messages
        }
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]

# ── API Routes ───────────────────────────────────────────────────────────────

@app.route('/api/graph/nodes', methods=['GET'])
def get_graph_nodes():
    """Return graph nodes and edges for visualization"""
    nodes = []
    edges = []
    
    # Sales Orders
    orders = q("SELECT salesOrder, soldToParty, totalNetAmount, creationDate, overallDeliveryStatus FROM sales_order_headers LIMIT 50")
    for o in orders:
        nodes.append({"id": f"SO_{o['salesOrder']}", "type": "SalesOrder", "label": o['salesOrder'],
                       "data": o, "group": "order"})
    
    # Deliveries (linked to SO via delivery items)
    deliveries = q("""
        SELECT DISTINCT odh.deliveryDocument, odi.referenceSdDocument as salesOrder, odh.overallGoodsMovementStatus
        FROM outbound_delivery_headers odh
        JOIN outbound_delivery_items odi ON odh.deliveryDocument = odi.deliveryDocument
        LIMIT 60
    """)
    for d in deliveries:
        nid = f"DEL_{d['deliveryDocument']}"
        if not any(n['id'] == nid for n in nodes):
            nodes.append({"id": nid, "type": "Delivery", "label": d['deliveryDocument'],
                           "data": d, "group": "delivery"})
        edges.append({"source": f"SO_{d['salesOrder']}", "target": nid, "label": "delivered_by"})
    
    # Billing Docs (linked to delivery via billing items)
    billings = q("""
        SELECT DISTINCT bdh.billingDocument, bdi.referenceSdDocument as deliveryDocument,
               bdh.totalNetAmount, bdh.billingDocumentIsCancelled, bdh.accountingDocument
        FROM billing_document_headers bdh
        JOIN billing_document_items bdi ON bdh.billingDocument = bdi.billingDocument
        LIMIT 80
    """)
    for b in billings:
        nid = f"BD_{b['billingDocument']}"
        if not any(n['id'] == nid for n in nodes):
            nodes.append({"id": nid, "type": "BillingDoc", "label": b['billingDocument'],
                           "data": b, "group": "billing"})
        edges.append({"source": f"DEL_{b['deliveryDocument']}", "target": nid, "label": "billed_as"})
    
    # Journal Entries
    journals = q("""
        SELECT DISTINCT accountingDocument, referenceDocument, amountInCompanyCodeCurrency, postingDate
        FROM journal_entries LIMIT 50
    """)
    for j in journals:
        nid = f"JE_{j['accountingDocument']}"
        if not any(n['id'] == nid for n in nodes):
            nodes.append({"id": nid, "type": "JournalEntry", "label": j['accountingDocument'],
                           "data": j, "group": "journal"})
        edges.append({"source": f"BD_{j['referenceDocument']}", "target": nid, "label": "journal_entry"})
    
    # Customers
    customers = q("SELECT customer, businessPartnerFullName FROM business_partners LIMIT 20")
    for cu in customers:
        nid = f"CU_{cu['customer']}"
        nodes.append({"id": nid, "type": "Customer", "label": cu['businessPartnerFullName'] or cu['customer'],
                       "data": cu, "group": "customer"})
    # Link customers to orders
    for o in orders:
        if o['soldToParty']:
            edges.append({"source": f"CU_{o['soldToParty']}", "target": f"SO_{o['salesOrder']}", "label": "placed"})
    
    # Remove edges referencing non-existent nodes
    node_ids = {n['id'] for n in nodes}
    edges = [e for e in edges if e['source'] in node_ids and e['target'] in node_ids]
    
    return jsonify({"nodes": nodes, "edges": edges})

@app.route('/api/graph/node/<node_id>', methods=['GET'])
def get_node_detail(node_id):
    """Get detailed data for a specific node"""
    parts = node_id.split('_', 1)
    if len(parts) < 2:
        return jsonify({"error": "Invalid node id"})
    
    ntype, nval = parts[0], parts[1]
    
    if ntype == 'SO':
        data = {
            "header": q("SELECT * FROM sales_order_headers WHERE salesOrder=?", (nval,)),
            "items": q("SELECT soi.*, pd.productDescription FROM sales_order_items soi LEFT JOIN product_descriptions pd ON soi.material=pd.product AND pd.language='EN' WHERE soi.salesOrder=?", (nval,)),
        }
    elif ntype == 'DEL':
        data = {
            "header": q("SELECT * FROM outbound_delivery_headers WHERE deliveryDocument=?", (nval,)),
            "items": q("SELECT * FROM outbound_delivery_items WHERE deliveryDocument=?", (nval,)),
        }
    elif ntype == 'BD':
        data = {
            "header": q("SELECT * FROM billing_document_headers WHERE billingDocument=?", (nval,)),
            "items": q("SELECT bdi.*, pd.productDescription FROM billing_document_items bdi LEFT JOIN product_descriptions pd ON bdi.material=pd.product AND pd.language='EN' WHERE bdi.billingDocument=?", (nval,)),
        }
    elif ntype == 'JE':
        data = {
            "entries": q("SELECT * FROM journal_entries WHERE accountingDocument=?", (nval,)),
        }
    elif ntype == 'CU':
        data = {
            "partner": q("SELECT * FROM business_partners WHERE customer=?", (nval,)),
            "address": q("SELECT * FROM business_partner_addresses ba JOIN business_partners bp ON ba.businessPartner=bp.businessPartner WHERE bp.customer=?", (nval,)),
            "orders": q("SELECT salesOrder, creationDate, totalNetAmount FROM sales_order_headers WHERE soldToParty=?", (nval,)),
        }
    else:
        data = {}
    
    return jsonify(data)

@app.route('/api/chat', methods=['POST'])
def chat():
    body = request.json
    user_query = body.get('message', '')
    history = body.get('history', [])
    
    try:
        # Step 1: Get SQL from LLM
        parsed = call_llm_for_sql(user_query, history)
        
        if parsed.get('off_topic'):
            return jsonify({
                "answer": "This system is designed to answer questions related to the Order-to-Cash dataset only. Please ask about sales orders, deliveries, billing documents, payments, customers, or products.",
                "sql": None,
                "results": [],
                "highlighted_nodes": []
            })
        
        sql = parsed.get('sql')
        results = []
        highlighted_nodes = []
        
        if sql:
            try:
                results = q(sql)
                
                # Extract node IDs to highlight
                for row in results[:20]:
                    for key, val in row.items():
                        if val and isinstance(val, str):
                            if key in ('salesOrder',):
                                highlighted_nodes.append(f"SO_{val}")
                            elif key in ('deliveryDocument',):
                                highlighted_nodes.append(f"DEL_{val}")
                            elif key in ('billingDocument',):
                                highlighted_nodes.append(f"BD_{val}")
                            elif key in ('accountingDocument',) and len(str(val)) > 8:
                                highlighted_nodes.append(f"JE_{val}")
            except Exception as e:
                return jsonify({
                    "answer": f"SQL execution error: {str(e)}\n\nSQL attempted:\n```sql\n{sql}\n```",
                    "sql": sql,
                    "results": [],
                    "highlighted_nodes": []
                })
        
        # Step 2: Get natural language answer
        answer = call_llm_for_answer(user_query, sql, results, history)
        
        return jsonify({
            "answer": answer,
            "sql": sql,
            "results": results[:100],
            "highlighted_nodes": list(set(highlighted_nodes))
        })
    
    except Exception as e:
        return jsonify({"answer": f"Error: {str(e)}", "sql": None, "results": [], "highlighted_nodes": []})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats = {
        "sales_orders": q("SELECT COUNT(*) as cnt FROM sales_order_headers")[0]['cnt'],
        "deliveries": q("SELECT COUNT(*) as cnt FROM outbound_delivery_headers")[0]['cnt'],
        "billing_docs": q("SELECT COUNT(*) as cnt FROM billing_document_headers")[0]['cnt'],
        "journal_entries": q("SELECT COUNT(*) as cnt FROM journal_entries")[0]['cnt'],
        "payments": q("SELECT COUNT(*) as cnt FROM payments")[0]['cnt'],
        "customers": q("SELECT COUNT(*) as cnt FROM business_partners")[0]['cnt'],
        "products": q("SELECT COUNT(*) as cnt FROM products")[0]['cnt'],
    }
    return jsonify(stats)

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
