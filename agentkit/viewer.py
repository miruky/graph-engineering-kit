"""Read-only, offline workbench. No project file-serving endpoint or write API."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib

from .core import atomic_write, confined, require

CSS = '''*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#182337;font:15px/1.6 system-ui,sans-serif}header{padding:30px 5%;background:#14243c;color:#fff}h1{font-size:27px;margin:0 0 8px}header p{margin:0;color:#cbd5e5}main{max-width:1450px;margin:24px auto;padding:0 24px}.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:18px}input,button{font:inherit;padding:10px 14px;border:1px solid #b5c2d2;border-radius:6px;background:white}button{cursor:pointer}input{min-width:260px}button:focus-visible,input:focus-visible{outline:3px solid #dc8300;outline-offset:3px}.layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:20px}#graph{position:relative;height:580px;background:#fff;border:1px solid #d8e0eb;border-radius:10px}aside,section{background:white;padding:20px;border:1px solid #d8e0eb;border-radius:10px}h2{font-size:18px;margin:0 0 12px}#detail{white-space:pre-wrap;overflow-wrap:anywhere}.legend{font-size:13px;color:#495d78;margin:12px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid #e0e7ef;overflow-wrap:anywhere}th{background:#f3f6fa}section{margin-top:20px;overflow:auto}.tag{background:#e7edf7;border-radius:5px;padding:4px 9px}#count{color:#435571}footer{padding:20px 0;color:#53657b;font-size:13px}@media(max-width:800px){.layout{grid-template-columns:1fr}#graph{height:420px}main{padding:0 14px}input{min-width:0;width:100%}}'''
JS = '''const graph=JSON.parse(document.getElementById('data').textContent);const tbody=document.querySelector('tbody');const detail=document.getElementById('detail');const selector=document.getElementById('filter');let cy;document.getElementById('count').textContent=`${graph.nodes.length} nodes · ${graph.edges.length} relations`;function select(id){const n=graph.nodes.find(x=>x.id===id);detail.textContent=JSON.stringify(n,null,2);if(cy){cy.elements().removeClass('selected');cy.getElementById(id).addClass('selected')}}function rows(){tbody.replaceChildren();const q=selector.value.toLowerCase();for(const n of graph.nodes){if(!JSON.stringify(n).toLowerCase().includes(q))continue;const tr=document.createElement('tr');for(const k of ['id','kind','status','title']){const td=document.createElement('td');if(k==='id'){const b=document.createElement('button');b.textContent=n.id;b.onclick=()=>select(n.id);td.append(b)}else td.textContent=n[k]??'';tr.append(td)}tbody.append(tr)}if(cy){cy.elements().removeClass('dim');if(q)cy.nodes().forEach(n=>{if(!JSON.stringify(n.data()).toLowerCase().includes(q))n.addClass('dim')})}}rows();if(typeof cytoscape==='function'){cy=cytoscape({container:document.getElementById('graph'),elements:[...graph.nodes.map(n=>({data:{...n,label:n.id+'\\n'+(n.status??n.kind)}})),...graph.edges.map(e=>({data:{...e,source:e.from,target:e.to}}))],layout:{name:'breadthfirst',directed:true,roots:graph.nodes.filter(n=>!graph.edges.some(e=>e.kind!=='verifies'&&e.to===n.id)).map(n=>n.id),padding:35,spacingFactor:1.15},style:[{selector:'node',style:{label:'data(label)','text-wrap':'wrap','text-max-width':170,'font-size':11,'text-valign':'center','text-halign':'center','background-color':'#e4ecf8','border-color':'#607a9d','border-width':1,width:170,height:60,shape:'roundrectangle',color:'#172941'}},{selector:'node[status="succeeded"],node[status="current"]',style:{'background-color':'#d4efe1','border-color':'#2d875a'}},{selector:'node[status="failed"],node[status="stale"]',style:{'background-color':'#ffe0de','border-color':'#bc4743','border-width':3}},{selector:'node[status="waiting"],node[status="unreviewed"]',style:{'background-color':'#fff0ce','border-color':'#b58624'}},{selector:'edge',style:{width:1.7,'curve-style':'bezier','line-color':'#8799b2','target-arrow-color':'#8799b2','target-arrow-shape':'triangle'}},{selector:'edge[kind="owns"]',style:{'target-arrow-shape':'none','line-style':'dotted'}},{selector:'edge[kind="verifies"]',style:{'curve-style':'unbundled-bezier','control-point-distances':-400,'control-point-weights':.5,'line-style':'dashed','line-color':'#8c63ad','target-arrow-color':'#8c63ad'}},{selector:'.dim',style:{opacity:.15}},{selector:'.selected',style:{'border-width':4,'border-color':'#1766bc'}}]});cy.on('tap','node',e=>select(e.target.id()))}else document.getElementById('graph').textContent='Interactive renderer unavailable. The complete node and relation tables remain available.';selector.oninput=rows;document.getElementById('fit').onclick=()=>cy?.fit(undefined,35);document.getElementById('reset').onclick=()=>{selector.value='';rows();cy?.fit(undefined,35)};const edges=document.getElementById('relations');for(const e of graph.edges){const li=document.createElement('li');li.textContent=e.from+' → '+e.to+' ('+e.kind+')';edges.append(li)}'''


def export(root, graph, title="Agent Engineering Workbench"):
    require(isinstance(graph, dict) and isinstance(graph.get("nodes"), list) and isinstance(graph.get("edges"), list),
            "Invalid graph data")
    import html
    target = ".agentkit/output/workbench"
    data = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>'''+html.escape(title)+'''</title><link rel="stylesheet" href="style.css"></head><body><header><h1>'''+html.escape(title)+'''</h1><p>Specifications, work and evidence · Read-only snapshot</p></header><main><div class="bar"><input id="filter" aria-label="Filter nodes" placeholder="Search node, path or status"><button id="fit">Fit graph</button><button id="reset">Reset</button><span id="count"></span><span class="tag">Offline · No write API</span></div><div class="layout"><div><div id="graph" role="img" aria-label="Relationship graph. The same content is available in the tables below."></div><p class="legend">Green: current/succeeded · Amber: review/approval pending · Red: stale/failed. Solid arrows show dependencies; dashed arrows show verification; dotted lines show ownership.</p></div><aside><h2>Selected node</h2><div id="detail">Select a node or its table button.</div></aside></div><section><h2>All nodes</h2><table><thead><tr><th>Node</th><th>Kind</th><th>Status</th><th>Title / path</th></tr></thead><tbody></tbody></table></section><section><h2>Relations</h2><ul id="relations"></ul></section><footer>Generated from declared metadata and execution records. A green status does not establish semantic correctness of a design.</footer></main><script id="data" type="application/json">'''+data+'''</script><script src="cytoscape.min.js"></script><script src="app.js"></script></body></html>'''
    vendor = Path(__file__).parent / "assets/cytoscape.min.js"
    require(vendor.is_file(), "Vendored graph renderer is missing")
    for name, content in (("index.html", page.encode()), ("style.css", CSS.encode()),
                          ("app.js", JS.encode()), ("graph.json", (json.dumps(graph, indent=2) + "\n").encode()),
                          ("cytoscape.min.js", vendor.read_bytes())):
        atomic_write(root, target + "/" + name, content)
    return {"ok": True, "directory": target, "html": target + "/index.html",
            "nodes": len(graph["nodes"]), "edges": len(graph["edges"])}


def serve(root, directory, port=0):
    files = {"/": ("index.html", "text/html; charset=utf-8"),
             "/style.css": ("style.css", "text/css; charset=utf-8"),
             "/app.js": ("app.js", "text/javascript; charset=utf-8"),
             "/cytoscape.min.js": ("cytoscape.min.js", "text/javascript; charset=utf-8"),
             "/graph.json": ("graph.json", "application/json")}
    payloads = {route: (confined(root, directory + "/" + name, exists=True).read_bytes(), kind)
                for route, (name, kind) in files.items()}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in expected or self.headers.get("Origin", "") not in (
                    "", *("http://" + name for name in expected)):
                self.send_error(403)
                return
            if self.path not in payloads:
                self.send_error(404)
                return
            body, content_type = payloads[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            self.send_error(405)
        def log_message(self, *args):
            pass
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
