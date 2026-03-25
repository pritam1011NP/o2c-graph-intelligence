import json, glob, sqlite3, os

DB_PATH = '/home/claude/o2c-app/o2c.db'
DATA_DIR = '/home/claude/dataset/sap-o2c-data'

def load_jsonl(folder):
    records = []
    for f in glob.glob(f'{DATA_DIR}/{folder}/*.jsonl'):
        with open(f) as fp:
            for line in fp:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records

def clean(v):
    if isinstance(v, dict):
        return json.dumps(v)
    return v

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

def create_table(name, records):
    if not records:
        return
    keys = list(records[0].keys())
    cols = ', '.join(f'"{k}" TEXT' for k in keys)
    c.execute(f'DROP TABLE IF EXISTS "{name}"')
    c.execute(f'CREATE TABLE "{name}" ({cols})')
    for r in records:
        vals = [str(clean(r.get(k, ''))) if r.get(k) is not None else None for k in keys]
        placeholders = ','.join(['?']*len(keys))
        c.execute(f'INSERT INTO "{name}" VALUES ({placeholders})', vals)
    print(f'  {name}: {len(records)} rows')

tables = {
    'sales_order_headers': 'sales_order_headers',
    'sales_order_items': 'sales_order_items',
    'sales_order_schedule_lines': 'sales_order_schedule_lines',
    'outbound_delivery_headers': 'outbound_delivery_headers',
    'outbound_delivery_items': 'outbound_delivery_items',
    'billing_document_headers': 'billing_document_headers',
    'billing_document_items': 'billing_document_items',
    'billing_document_cancellations': 'billing_document_cancellations',
    'journal_entry_items_accounts_receivable': 'journal_entries',
    'payments_accounts_receivable': 'payments',
    'business_partners': 'business_partners',
    'business_partner_addresses': 'business_partner_addresses',
    'customer_company_assignments': 'customer_company_assignments',
    'customer_sales_area_assignments': 'customer_sales_area_assignments',
    'products': 'products',
    'product_descriptions': 'product_descriptions',
    'plants': 'plants',
}

for folder, table in tables.items():
    recs = load_jsonl(folder)
    create_table(table, recs)

conn.commit()
conn.close()
print('DB built successfully')
