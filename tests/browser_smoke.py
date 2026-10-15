"""Optional browser QA using a recorded answer; never invokes an LLM.

Usage: python tests/browser_smoke.py --recording answer.json --output qa-directory
Requires Playwright and its Chromium browser, plus a running local web service.
"""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    temporary = args.output.resolve() / "browser-tmp"
    temporary.mkdir(exist_ok=True)
    os.environ["TMP"] = os.environ["TEMP"] = str(temporary)
    from playwright.sync_api import sync_playwright

    recording = json.loads(args.recording.read_text(encoding="utf-8"))
    errors, checks = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1512, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/ask", lambda route: route.fulfill(json=recording))
        page.goto("http://localhost:8000", wait_until="networkidle")
        page.wait_for_function("window.cytoscape && document.getElementById('graph').children.length > 0")
        page.locator('[data-example="Q1"]').click()
        page.locator('#activity').filter(has_text="完成").wait_for()
        assert page.locator('#question').input_value() == recording['question']
        assert page.locator('#trace .trace-card').count() == len(recording['trace'])
        assert page.evaluate("cy.nodes().length") > 0
        checks.append("Q1 recorded answer, real trace, and graph rendered")
        page.locator('#answer button[data-matched="true"]').first.click()
        assert page.locator('#evidence-dialog').is_visible()
        assert len(page.locator('#evidence-text').inner_text()) > 20
        assert "摘录" not in page.locator('#evidence-text').inner_text()[:8]
        page.locator('#close-dialog').click()
        checks.append("Matched citation opens original graph excerpt")
        missing = page.locator('#answer button[data-matched="false"]')
        if missing.count():
            missing.first.click()
            assert "没有此引用的摘录" in page.locator('#evidence-text').inner_text()
            page.locator('#close-dialog').click()
            checks.append("Unmatched citation explicitly reports missing excerpt")
        with page.expect_response(lambda response: '/api/neighbors/' in response.url and response.status == 200):
            page.evaluate("cy.nodes()[0].emit('tap')")
        page.wait_for_function("document.getElementById('node-info').textContent.includes('assertions')")
        checks.append("Graph node expansion uses live read-only neighbors API")
        page.locator('#ontology').click()
        page.locator('#evidence-dialog').wait_for(state="visible")
        assert "HAS_RESOURCE" in page.locator('#evidence-text').inner_text()
        page.locator('#close-dialog').click()
        page.screenshot(path=str(args.output / 'web-desktop.png'), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        # Cytoscape resizes its canvases asynchronously after the viewport event.
        page.wait_for_function("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(args.output / 'web-mobile.png'), full_page=True)
        checks.append("Ontology dialog and mobile layout without horizontal overflow")
        assert not errors, errors
        browser.close()
    result = {"checks": checks, "page_errors": errors, "ask_source": "recorded single live LLM response"}
    (args.output / 'browser-summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
