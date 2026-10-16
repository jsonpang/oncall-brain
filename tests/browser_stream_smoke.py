"""Browser regression checks for SSE parsing, fallback, timer and status copy.
Run against a web server: python tests/browser_stream_smoke.py --url http://127.0.0.1:18011
No graph or model calls are made by these checks.
"""
import argparse
import json
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1512, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='networkidle')
        # Every byte is its own chunk, including split UTF-8 characters and CRLF.
        result = page.evaluate('''async () => {
          const original = window.fetch, seen = [], receive = receiveStep;
          receiveStep = s => seen.push(s);
          const frames = ': heartbeat\\r\\n\\r\\nevent: step\\r\\ndata: {"tool":"读图"}\\r\\n\\r\\nevent: done\\r\\ndata: {"answer":"完成"}\\r\\n\\r\\n';
          const bytes = new TextEncoder().encode(frames);
          window.fetch = async () => new Response(new ReadableStream({start(c) {for (const b of bytes) c.enqueue(new Uint8Array([b])); c.close();}}), {headers:{'Content-Type':'text/event-stream'}});
          try {return {answer: await askWithProgress({question:'offline'}), seen};}
          finally {window.fetch = original; receiveStep = receive;}
        }''')
        assert result == {'answer': {'answer': '完成'}, 'seen': [{'tool': '读图'}]}
        # Hold the final result while the actual form and interval render progress.
        page.evaluate('''() => {
          window.originalFetch = window.fetch;
          window.mockAnswer = {answer:'Status: conflict\\n## Answer\\nTwo sources disagree.', status:'conflict', trace:[], evidence:[], citations:[]};
          window.fetch = async () => new Response(new ReadableStream({start(c) {
            window.progressController = c;
            c.enqueue(new TextEncoder().encode('event: step\\ndata: {"tool":"source_lines","summary":{"source_evidence":2}}\\n\\nevent: step\\ndata: {"tool":"compose"}\\n\\n'));
          }}), {headers:{'Content-Type':'text/event-stream'}});
        }''')
        page.locator('#question').fill('offline timer check')
        page.locator('#ask-button').click()
        page.wait_for_function("document.querySelector('#ask-progress')?.textContent.includes('正在组装回答 · 0 秒')")
        page.wait_for_function("document.querySelector('#ask-progress')?.textContent.includes('正在组装回答 · 1 秒')")
        assert page.locator('#ask-progress li').count() == 1
        page.locator('[data-lang="en"]').click()
        assert 'Composing the answer' in page.locator('#ask-progress').inner_text()
        page.evaluate('''() => {progressController.enqueue(new TextEncoder().encode('event: done\\ndata: '+JSON.stringify(mockAnswer)+'\\n\\n'));progressController.close();window.fetch=originalFetch;}''')
        page.wait_for_function('!busy')
        assert not page.locator('#ask-progress').count()
        assert page.locator('#status-explanation').get_attribute('open') is not None
        assert page.locator('#status-explanation summary').inner_text() == 'What does this mean? How was it decided?'
        for state in ('applicable', 'superseded', 'conflict', 'insufficient_context', 'not_found'):
            page.evaluate('(state) => {lang="zh";renderAnswer({...mockAnswer,status:state});}', state)
            assert page.locator('#status-explanation > div').text_content() == page.evaluate('(state) => STATUS_TEXT.zh[state]', state)
            assert (page.locator('#status-explanation').get_attribute('open') is not None) == (state == 'conflict')
        # Unsupported route falls back once to the established endpoint.
        calls = []
        page.route('**/api/ask/stream', lambda route: (calls.append('stream'), route.fulfill(status=404)))
        page.route('**/api/ask', lambda route: (calls.append('regular'), route.fulfill(json={'answer': 'fallback'})))
        assert page.evaluate("async () => await askWithProgress({question:'offline fallback'})") == {'answer': 'fallback'}
        assert calls == ['stream', 'regular']
        page.unroute('**/api/ask/stream')
        for frame in ('event: error\ndata: {"error":"unavailable"}\n\n', 'event: step\ndata: {"tool":"compose"}\n\n'):
            page.route('**/api/ask/stream', lambda route, request, frame=frame: route.fulfill(body=frame, content_type='text/event-stream'))
            failure = page.evaluate("async () => {try {await askWithProgress({question:'offline'});return null;} catch(e) {return e.message;}}")
            assert failure and ('unavailable' in failure or '中断' in failure)
            page.unroute('**/api/ask/stream')
        assert calls == ['stream', 'regular'], 'Errors must not reissue the question'
        assert not errors, errors
        browser.close()
    print(json.dumps({'checks': ['byte-fragmented UTF-8/CRLF SSE', 'live one-second timer', 'language switch during wait', 'collapse on done', 'five status explanations and conflict open', '404 fallback', 'error and premature EOF without retry'], 'page_errors': errors}))


if __name__ == '__main__':
    main()
