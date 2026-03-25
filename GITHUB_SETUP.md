# GitHub Repository Setup

## Repository Structure

```
o2c-graph-intelligence/
├── README.md                    ← Architecture docs, prompting strategy, guardrails
├── ai-session-log.md            ← Full AI coding session transcript
├── app.py                       ← Flask REST API backend
├── build_db.py                  ← JSONL → SQLite ingestion
├── o2c.db                       ← Pre-built database (or run build_db.py)
├── start.sh                     ← Quick start script
├── requirements.txt             ← Python dependencies
├── static/
│   └── index.html               ← Full frontend (for Flask backend)
└── o2c-graph.html               ← Standalone demo (no server needed)
```

## Quick Setup Commands

```bash
# 1. Create new GitHub repo at https://github.com/new
# Name: o2c-graph-intelligence
# Visibility: Public

# 2. Initialize and push
git init
git add .
git commit -m "Initial commit: O2C Graph Intelligence System

- D3.js force-directed graph visualization
- Flask REST API with SQLite backend  
- Claude-powered NL-to-SQL query interface
- Off-topic guardrails
- Conversation memory"

git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/o2c-graph-intelligence.git
git push -u origin main
```

## requirements.txt

```
flask>=2.3.0
flask-cors>=4.0.0
```

## .gitignore

```
__pycache__/
*.pyc
.env
*.log
venv/
.DS_Store
```

## Demo Deployment Options

### Option A: GitHub Pages (static demo only)
1. Go to repo Settings → Pages
2. Source: Deploy from branch `main`, folder `/` (root)
3. Access: `https://YOUR_USERNAME.github.io/o2c-graph-intelligence/o2c-graph.html`

### Option B: Render.com (full Flask backend)
1. Connect GitHub repo to render.com
2. Build command: `pip install flask flask-cors`
3. Start command: `python3 app.py`
4. Add env var: `PORT=10000`

Update `app.py` last line:
```python
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
```

### Option C: Railway.app
```bash
railway init
railway up
```

### Option D: Vercel (serverless, requires restructure)
Not recommended — Flask doesn't map cleanly to serverless functions.
