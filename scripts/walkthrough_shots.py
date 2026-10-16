"""Annotated walkthrough screenshots of the OnCall Brain web UI.

Drives the running UI (http://127.0.0.1:8000) with Playwright, overlays
numbered callouts with arrows on real elements, and saves PNGs. One point per
screenshot; callouts are numbered in reading order.

    python scripts/walkthrough_shots.py docs/walkthrough
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://127.0.0.1:8000"
Q14 = ("A pipeline runner is stuck in a circular dependency deadlock. Should I ping "
       "Wesley Tate to log into the backend and terminate the zombie worker process?")

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
  document.body.append(layer);
  for (const it of items) {
    let r;
    if (it.xy) { r = {left: it.xy[0]-22, top: it.xy[1]-22, width: 44, height: 44}; }
    else {
      const el = document.querySelector(it.sel);
      if (!el) continue;
      const b = el.getBoundingClientRect(); r = {left: b.left, top: b.top, width: b.width, height: b.height};
    }
    const box = document.createElementNS(NS, 'rect');
    box.setAttribute('x', r.left-4); box.setAttribute('y', r.top-4);
    box.setAttribute('width', r.width+8); box.setAttribute('height', r.height+8);
    box.setAttribute('rx', 8); box.setAttribute('fill', 'none');
    box.setAttribute('stroke', '#ffb020'); box.setAttribute('stroke-width', 3);
    svg.append(box);
    const label = document.createElement('div');
    label.style.cssText = `position:absolute;left:${it.lx}px;top:${it.ly}px;width:${it.w||280}px;background:#ffb020;color:#1b1300;font:600 15px/1.5 system-ui,"Microsoft YaHei",sans-serif;padding:9px 12px 9px 42px;border-radius:9px;box-shadow:0 6px 22px #000a`;
    label.innerHTML = `<span style="position:absolute;left:10px;top:9px;width:24px;height:24px;border-radius:50%;background:#1b1300;color:#ffb020;display:grid;place-items:center;font-size:14px">${it.n}</span>${it.text}`;
    layer.append(label);
    const lb = label.getBoundingClientRect();
    const tx = Math.min(Math.max(lb.left + lb.width/2, r.left - 4), r.left + r.width + 4);
    const ty = Math.min(Math.max(lb.top + lb.height/2, r.top - 4), r.top + r.height + 4);
    const sx = Math.min(Math.max(tx, lb.left), lb.right);
    const sy = Math.min(Math.max(ty, lb.top), lb.bottom);
    if (Math.hypot(tx - sx, ty - sy) < 8) continue;
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', sx); line.setAttribute('y1', sy);
    line.setAttribute('x2', tx); line.setAttribute('y2', ty);
    line.setAttribute('stroke', '#ffb020'); line.setAttribute('stroke-width', 3);
    line.setAttribute('marker-end', 'url(#wt-a)');
    svg.append(line);
  }
}
"""


def rect(pg, sel):
    return pg.evaluate(f"""() => {{ const e = document.querySelector({sel!r}); if (!e) return null;
      const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; }}""")


def edge_xy(pg, which=0, predicate=None):
    pick = f"es.filter(x => x.data('predicate') === {predicate!r})[0] ||" if predicate else ""
    return pg.evaluate(f"""() => {{
      const es = cy.edges('.cited'); const e = {pick} es[{which}] || es[0]; if (!e) return null;
      const m = e.renderedMidpoint(), b = document.getElementById('graph').getBoundingClientRect();
      return [b.left + m.x, b.top + m.y]; }}""")


def shot(pg, name, items, scroll_y=0):
    pg.evaluate(f"() => {{ window.scrollTo(0, {scroll_y}); document.querySelectorAll('section').forEach(x => x.scrollTop = 0); }}")
    pg.evaluate(OVERLAY_JS, items)
    pg.wait_for_timeout(250)
    pg.screenshot(path=str(OUT / name))
    pg.evaluate("() => document.getElementById('wt-overlay')?.remove()")
    print("saved", name)


def ask(pg, question=None, example=None):
    if example:
        pg.evaluate("() => { const b = document.querySelector('.examples-box'); if (b) b.open = true; }")
        pg.locator(f'[data-example="{example}"]').click()
    else:
        pg.fill("#question", question)
        pg.locator("#ask-button").click()
    pg.locator("#activity").filter(has_text="完成").wait_for(timeout=180000)
    pg.wait_for_timeout(1500)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(URL, wait_until="networkidle")
    pg.evaluate("() => localStorage.setItem('oncall-lang','zh')")
    pg.reload(wait_until="networkidle")

    # 1. Ask
    shot(pg, "01-ask.png", [
        {"n": 1, "sel": ".examples", "text": "点一道示例题（或在上面的框里输入英文问题），系统就开始查", "lx": 660, "ly": 300, "w": 320},
    ])

    # 2. The conclusion
    ask(pg, example="Q1")
    shot(pg, "02-conclusion.png", [
        {"n": 1, "sel": ".conclusion", "text": "这就是答案：知识库在哪、谁维护、技术问题找谁", "lx": 650, "ly": 800, "w": 320},
        {"n": 2, "sel": "#activity", "text": "状态「可直接照做」：资料里有明确答案，没有冲突或过期", "lx": 650, "ly": 100, "w": 320},
    ])

    # 3. Why: click a citation, read the source line
    cite = pg.locator(".conclusion .cite-n[data-matched='true']").first
    if not cite.count():
        cite = pg.locator("#answer .cite-n[data-matched='true']").first
    cite.click()
    pg.wait_for_timeout(600)
    pg.evaluate("() => { const d = document.getElementById('evidence-dialog'); d.close(); d.show(); Object.assign(d.style, {position:'fixed', top:'280px', left:'640px', margin:0, width:'560px', zIndex:5000}); }")
    pg.wait_for_timeout(300)
    shot(pg, "03-source.png", [
        {"n": 1, "sel": ".conclusion .cite-n", "text": "凭什么这么说？点结论里的小编号", "lx": 250, "ly": 440, "w": 280},
        {"n": 2, "sel": "#evidence-text", "text": "弹出原文那一行：来自哪份文件第几行，标着是正式文档还是聊天", "lx": 640, "ly": 160, "w": 330},
    ])
    pg.locator("#close-dialog").click()
    pg.wait_for_timeout(1800)  # let the focus flash end

    # 4. The graph: switch to explore tab to see Cytoscape graph with cited edges
    pg.locator('[data-view="explore"]').click()
    pg.wait_for_timeout(500)
    pg.evaluate("() => { const c = cy.$('.cited'); if (c.length) { cy.fit(c, 70); } }")
    pg.wait_for_timeout(400)
    g = rect(pg, "#graph")
    xy = edge_xy(pg, predicate="MAINTAINED_BY")
    items4 = [{"n": 2, "sel": "#graph-summary", "text": "变暗的是查过但没用上的内容；亮起几条线，结论就引用了几条关系", "lx": g[0] + 20, "ly": g[3] - 70, "w": 340}]
    if xy:
        items4.insert(0, {"n": 1, "xy": xy, "text": "亮绿色的线 = 结论的依据。线上写着关系名，例如「这份知识库由谁维护」", "lx": g[0] + 20, "ly": g[1] + 20, "w": 330})
    shot(pg, "04-graph-proof.png", items4)

    # 5. How it was found: open trace box
    pg.evaluate("() => { document.getElementById('trace-box').open = true; }")
    pg.wait_for_timeout(300)
    t = rect(pg, "#trace")
    shot(pg, "05-how.png", [
        {"n": 1, "sel": "#trace .trace-card:nth-of-type(1)", "text": "第一步：按问题里的关键词，先在原始资料里找相关的几行", "lx": t[0] - 340, "ly": 170, "w": 300},
        {"n": 2, "sel": "#trace .trace-card:nth-of-type(2)", "text": "第二步：Jev 给图里的候选实体打分，挑出值得读的几个（括号里是分数）", "lx": t[0] - 340, "ly": 420, "w": 300},
        {"n": 3, "sel": "#trace .trace-card:nth-of-type(3)", "text": "第三步：读这些实体的全部关系和出处，最后才让大模型写结论", "lx": t[0] - 340, "ly": 720, "w": 300},
    ])
    pg.evaluate("() => { document.getElementById('trace-box').open = false; }")

    # 6. Trap question
    ask(pg, question=Q14)
    pg.locator('[data-view="explore"]').click()
    pg.wait_for_timeout(500)
    pg.evaluate("() => { const c = cy.$('.cited'); if (c.length) { cy.fit(c, 70); } }")
    pg.wait_for_timeout(400)
    g = rect(pg, "#graph")
    xy = edge_xy(pg, predicate="HOLDS_ROLE")
    items6 = [
        {"n": 1, "sel": "#question", "text": "这个问题本身就错了：它默认 Wesley 能去修服务器", "lx": g[0] + 20, "ly": 100, "w": 320},
        {"n": 2, "sel": ".conclusion", "text": "系统没有顺着问题答，而是指出前提错误：别找 Wesley，找 Colin Marsh", "lx": g[0] + 20, "ly": 650, "w": 320},
        {"n": 3, "sel": "#activity", "text": "所以状态是「说法冲突 / 问题前提不成立」", "lx": g[0] + 20, "ly": 220, "w": 320},
    ]
    if xy:
        items6.append({"n": 4, "xy": xy, "text": "依据在图上：Colin 是 Tier 2 Pipeline Specialist（HOLDS_ROLE 角色），属于二线技术负责人；Wesley 只有维护文档关系", "lx": g[2] - 340, "ly": 380, "w": 330})
    shot(pg, "06-trap.png", items6)

    # 7. Who actually fixed it card (Expertise card)
    pg.locator('[data-view="path"]').click()
    pg.wait_for_timeout(300)
    shot(pg, "07-who-fixed.png", [
        {"n": 1, "sel": ".expertise-card", "text": "负责人 vs 实际修过的人：系统沿「工单→模块」与「工单→处理人」穿透分析，找出真正处理过工单的人与工单号", "lx": 650, "ly": 760, "w": 330},
        {"n": 2, "sel": "#status-explanation", "text": "状态解释抽屉：详细展开系统判定「冲突」的依据与来源级别（正式规则 > 惯例 > 聊天）", "lx": 650, "ly": 480, "w": 330},
    ], scroll_y=220)

    # 8. Health page
    pg.goto(f"{URL}/health", wait_until="networkidle")
    pg.wait_for_timeout(500)
    shot(pg, "08-health.png", [
        {"n": 1, "sel": ".totals-grid", "text": "全库体检大盘：统计全库 494 个实体、824 条断言关系与 98 张关联工单", "lx": 25, "ly": 95, "w": 230},
        {"n": 2, "sel": "#sec-single-points", "text": "单点风险排查：自动识别一人负责多个模块的关键人员，避免请假无人兜底", "lx": 25, "ly": 320, "w": 230},
        {"n": 3, "sel": "#sec-owner-handlers", "text": "负责人 vs 实际处理人脱节：扫描负责人从未处理过工单的模块，识别沟通断层", "lx": 25, "ly": 740, "w": 230},
    ])

    b.close()
