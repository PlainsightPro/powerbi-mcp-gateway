"""Small, dependency-free onboarding page. Never embeds private catalog or skill content."""
from html import escape
import json


def welcome_page(name: str, base_url: str) -> str:
    endpoint = base_url.rstrip('/') + '/mcp'
    config = json.dumps({'servers': {'powerbi': {'type': 'http', 'url': endpoint}}}, indent=2)
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(name)} · Connect</title><style>
:root{{color-scheme:light;--ink:#182e32;--muted:#50696c;--accent:#116b63}}*{{box-sizing:border-box}}
body{{margin:0;background:#f5f7f3;color:var(--ink);font:17px/1.6 system-ui,sans-serif}}
main{{max-width:1000px;margin:auto;padding:56px 28px 64px}}.eyebrow{{letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-size:12px;font-weight:750}}
h1{{font-size:clamp(32px,5vw,56px);letter-spacing:-.04em;line-height:1.1;margin:18px 0}}.lead{{max-width:670px;color:var(--muted);font-size:20px}}
.endpoint{{background:var(--ink);color:white;padding:24px;border-radius:16px;margin:32px 0;overflow-wrap:anywhere}}
.endpoint small{{display:block;color:#b7d7d0;margin-bottom:8px}}.cards{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
article{{background:white;border:1px solid #dae3dc;border-radius:16px;padding:26px}}h2{{font-size:22px;margin:0 0 14px}}
pre{{font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere;padding:16px;background:#f1f5f2;border-radius:8px}}
code{{font-family:ui-monospace,monospace}}li{{margin:8px 0}}a{{color:var(--accent)}}footer{{margin-top:30px;color:var(--muted);font-size:14px}}
@media(max-width:680px){{.cards{{grid-template-columns:1fr}}main{{padding:32px 20px}}}}
</style><main><div class="eyebrow">Power BI · Your data, your permissions</div><h1>{escape(name)}</h1>
<p class="lead">Ask questions about your Power BI data from your preferred AI assistant. Connect once using your Microsoft work account.</p>
<div class="endpoint"><small>Remote MCP server URL — copy this into your assistant</small><code>{escape(endpoint)}</code></div>
<div class="cards"><article><h2>1. Connect your assistant</h2><p>In Claude, ChatGPT or another MCP client, add a remote connector using the URL above. Select OAuth authentication and follow the Microsoft sign-in.</p>
<p>Claude Code:</p><pre>claude mcp add --transport http powerbi {escape(endpoint)}</pre><p>VS Code · <code>.vscode/mcp.json</code>:</p><pre>{escape(config)}</pre>
<p>Use the work account with access to your Power BI models.</p></article>
<article><h2>2. Ask your first question</h2><p>Start with one of these:</p><ul><li>“Which models can I query?”</li><li>“Help me analyse this Power BI report.” Paste the report link.</li><li>“Show the monthly trend for my chosen measure.”</li></ul>
<p>Name the period and scope. Follow-up questions can retain them. Every analysis returns its query, assumptions and execution status.</p>
<h2>Need help connecting?</h2><p>Ask your assistant to <strong>diagnose the Power BI connection</strong>. It checks discovery, model access and a constant query, without returning business figures.</p>
<p><a href="/healthz">Check whether the gateway is running</a></p></article></div>
<footer>Queries use your Power BI permissions. Ask your administrator for Build access to the models you need. The connection diagnostic checks more than this page's basic availability.</footer></main></html>'''
