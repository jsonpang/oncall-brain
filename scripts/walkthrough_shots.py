"""Annotated walkthrough screenshots of the OnCall Brain web UI.

Drives the running UI (http://127.0.0.1:8000) with Playwright, overlays
numbered callouts with arrows pointing at real elements, and saves PNGs.

    python walkthrough_shots.py <out_dir>
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://127.0.0.1:8000"
Q14 = ("A pipeline runner is stuck in a circular dependency deadlock. Should I ping "
       "Wesley Tate to log into the backend and terminate the zombie worker process?")

# Overlay: for each item, a dashed box around the target, a numbered badge with
# a label placed at (lx, ly) relative to the viewport, and an arrow to the box.
OVERLAY_JS = r"""
(items) => {
  document.getElementById('wt-overlay')?.remove();
  const NS = 'http://www.w3.org/2000/svg';
  const layer = document.createElement('div');
  layer.id = 'wt-overlay';
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:99999';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', innerWidth); svg.setAttribute('height', innerHeight);
  svg.style.cssText = 'position:absolute;inset:0';
  svg.innerHTML = '<defs><marker id="wt-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#ffb020"/></marker></defs>';
  layer.append(svg);
  for (const it of items) {
    let r;
    if (it.xy) { r = {left: it.xy[0]-18, top: it.xy[1]-18, width: 36, height: 36}; }
    else {
      const el = document.querySelector(it.sel);
      if (!el) continue;
      const b = el.getBoundingClientRect(); r = {left: b.left, top: b.top, width: b.width, height: b.height};
    }
    const box = document.createElementNS(NS, 'rect');
    box.setAttribute('x', r.left-4); box.setAttribute('y', r.top-4);
    box.setAttribute('width', r.width+8); box.setAttribute('height', r.height+8);
    box.setAttribute('rx', 7); box.setAttribute('fill', 'none');
    box.setAttribute('stroke', '#ffb020'); box.setAttribute('stroke-width', 2.5);
    box.setAttribute('stroke-dasharray', '7 4');
    svg.append(box);
    const label = document.createElement('div');
    label.style.cssText = `position:absolute;left:${it.lx}px;top:${it.ly}px;max-width:${it.w||260}px;background:#ffb020;color:#1b1300;font:600 14px/1.45 system-ui,"Microsoft YaHei",sans-serif;padding:8px 11px 8px 40px;border-radius:9px;box-shadow:0 6px 20px #0008`;
    label.innerHTML = `<span style="position:absolute;left:9px;top:7px;width:23px;height:23px;border-radius:50%;background:#1b1300;color:#ffb020;display:grid;place-items:center;font-size:13px">${it.n}</span>${it.text}`;
    layer.append(label);
    document.body.append(layer);
    const lb = label.getBoundingClientRect();
    // arrow from the label edge nearest the target to the box edge
    const tx = Math.min(Math.max(lb.left + lb.width/2, r.left), r.left + r.width);
    const ty = Math.min(Math.max(lb.top + lb.height/2, r.top), r.top + r.height);
    const sx = Math.min(Math.max(tx, lb.left), lb.right);
    const sy = Math.min(Math.max(ty, lb.top), lb.bottom);
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', sx); line.setAttribute('y1', sy);
    line.setAttribute('x2', tx + (tx < sx ? 6 : tx > sx ? -6 : 0));
    line.setAttribute('y2', ty + (ty < sy ? 6 : ty > sy ? -6 : 0));
    line.setAttribute('stroke', '#ffb020'); line.setAttribute('stroke-width', 3);
    line.setAttribute('marker-end', 'url(#wt-a)');
    svg.append(line);
  }
  document.body.append(layer);
}
"""


def cited_edge_xy(pg):
    """Viewport position of the first cited edge's midpoint on the cytoscape canvas."""
    return pg.evaluate("""() => {
      const e = cy.edges('.cited')[0] || cy.edges()[0]; if (!e) return null;
      const m = e.renderedMidpoint(), b = document.getElementById('graph').getBoundingClientRect();
      return [b.left + m.x, b.top + m.y];
    }""")


def shot(pg, name, items):
    pg.evaluate(OVERLAY_JS, items)
    pg.wait_for_timeout(250)
    pg.screenshot(path=str(OUT / name))
    pg.evaluate("() => document.getElementById('wt-overlay')?.remove()")
    print("saved", name)


def ask(pg, question=None, example=None):
    if example:
        pg.locator(f'[data-example="{example}"]').click()
    else:
        pg.fill("#question", question)
        pg.locator("#ask-button").click()
    pg.locator("#activity").filter(has_text="完成").wait_for(timeout=180000)
    pg.wait_for_timeout(1200)
    # Zoom the graph onto the proof path so the lit edges are readable.
    pg.evaluate("() => { const c = cy.$('.cited'); if (c.length) { cy.fit(c, 60); const z = Math.min(1.5, Math.max(0.9, cy.zoom())); cy.zoom(z); cy.center(c); } }")
    pg.wait_for_timeout(400)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(URL, wait_until="networkidle")
    pg.evaluate("() => localStorage.setItem('oncall-lang','zh')")
    pg.reload(wait_until="networkidle")

    # 1. Home: where to ask
    shot(pg, "01-home.png", [
        {"n": 1, "sel": "#question", "text": "在这里输入问题（语料是英文，建议用英文问）", "lx": 640, "ly": 190, "w": 300},
        {"n": 2, "sel": ".examples", "text": "或者直接点示例题，一键提问", "lx": 640, "ly": 300, "w": 260},
        {"n": 3, "sel": ".mode", "text": "默认快速模式；勾选切到深度模式（模型逐步决定查询，约 1 分钟）", "lx": 120, "ly": 450, "w": 330},
        {"n": 4, "sel": ".lang", "text": "中文 / 英文界面切换", "lx": 1250, "ly": 110, "w": 200},
    ])

    # 2. Answer + proof path + trace
    ask(pg, example="Q1")
    xy = cited_edge_xy(pg)
    items = [
        {"n": 1, "sel": "#answer-status", "text": "答案状态：applicable 可直接用；另有 superseded 已替代、conflict 冲突、not_found 资料里没有", "lx": 640, "ly": 470, "w": 290},
        {"n": 2, "sel": "#answer .citation", "text": "每句话后面的方块是出处：编号 → 文件:行号。绿色=正式文档，黄色=聊天", "lx": 640, "ly": 620, "w": 290},
        {"n": 3, "sel": "#trace .trace-card:nth-child(2)", "text": "Jev 从候选实体里挑出要读的几个，括号里是打分", "lx": 1000, "ly": 780, "w": 220},
        {"n": 4, "sel": "#activity", "text": "耗时：查图+决策几秒，主要时间花在最后一次组装回答", "lx": 640, "ly": 330, "w": 290},
    ]
    shot(pg, "02-answer.png", items)

    # 2b. The proof path on the graph
    xy = cited_edge_xy(pg)
    gb = pg.evaluate("() => { const b = document.getElementById('graph').getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; }")
    items = [{"n": 1, "sel": "#graph-summary", "text": "图下方写明：亮起几条边就是答案引用了几条关系", "lx": gb[0] + 10, "ly": gb[3] - 70, "w": 270}]
    if xy:
        items.insert(0, {"n": 2, "xy": xy, "text": "亮绿色粗线 = 答案引用的关系（证明路径），两端节点亮起并显示名字；其余变暗，悬停可看", "lx": gb[0] + 10, "ly": gb[1] + 10, "w": 300})
    shot(pg, "02b-proof-path.png", items)

    # 3. Click a citation: graph focuses its edge, dialog shows the source line
    cite = pg.locator('#answer button.citation[data-matched="true"]').first
    cite.click()
    pg.wait_for_timeout(500)
    # A modal dialog sits in the top layer and would dim the callouts; reopen it non-modal.
    pg.evaluate("() => { window.scrollTo(0, 0); const d = document.getElementById('evidence-dialog'); d.close(); d.show(); Object.assign(d.style, {position: 'fixed', top: '320px', left: '420px', margin: 0, zIndex: 5000}); }")
    pg.wait_for_timeout(200)
    pg.evaluate("() => { document.activeElement?.blur(); window.scrollTo(0, 0); document.querySelectorAll('section').forEach(x => x.scrollTop = 0); }")
    pg.wait_for_timeout(200)
    shot(pg, "03-citation.png", [
        {"n": 1, "sel": "#evidence-text", "text": "点开任一引用：弹出这条结论来自的原文那一行，图上对应的边同时闪一下", "lx": 420, "ly": 160, "w": 330},
        {"n": 2, "sel": "#evidence-meta", "text": "权威级别：formal_rule 正式文档 > practice 惯例 > observation 单条聊天", "lx": 900, "ly": 190, "w": 300},
    ])
    pg.locator("#close-dialog").click()

    # 4. Trap question: maintainer is not the escalation target
    ask(pg, question=Q14)
    xy = cited_edge_xy(pg)
    items = [
        {"n": 1, "sel": "#answer-status", "text": "陷阱题：问题前提（找 Wesley 修服务器）和资料冲突，状态是 conflict", "lx": 660, "ly": 220, "w": 300},
        {"n": 2, "sel": "#answer .answer-heading", "text": "回答：别找 Wesley（他只维护文档），找 Pipeline 技术负责人 Colin Marsh，并按流程转单", "lx": 660, "ly": 600, "w": 300},
    ]
    if xy:
        items.append({"n": 3, "xy": xy, "text": "图上是两种不同的关系：MAINTAINED_BY（维护文档）和 TECH_LEAD_OF（技术负责），悬停节点可看关系名", "lx": 660, "ly": 400, "w": 300})
    shot(pg, "04-trap-conflict.png", items)
    print(json.dumps({"q14_status": pg.locator("#answer-status").inner_text()}, ensure_ascii=False))
    b.close()
