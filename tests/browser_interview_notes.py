"""Synthetic browser regression check. Run with the project venv; requires Playwright and Chrome."""
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_interview_notes import InterviewNotesTests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = InterviewNotesTests()
fixture.setUp()
try:
    fixture.database.interviews[901]['scheduled_at'] = datetime(2026, 12, 12, 11)
    fixture.database.interviews[901]['notes'] = 'Short note\nSecond line'
    fixture.database.interviews[903] = fixture.database.interviews[901] | {'interview_id': 903}
    html = fixture.client.get('/interviews').get_data(as_text=True)
    pages = {url: fixture.client.get(url).get_data(as_text=True) for url in
             ('/tracker', fixture.url, fixture.edit, fixture.delete)}
finally:
    fixture.tearDown()
    fixture.doCleanups()

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    for width in (320, 390, 768, 1440):
        page = browser.new_page(viewport={'width': width, 'height': 900})
        def assets(route):
            path = route.request.url.split('/static/', 1)[1]
            file = ROOT / 'static' / path
            route.fulfill(body=file.read_bytes(), content_type='text/css' if path.endswith('.css') else 'application/javascript' if path.endswith('.js') else 'image/jpeg')
        page.route('http://synthetic.test/static/**', assets)
        page.route('http://synthetic.test/', lambda route: route.fulfill(body=html, content_type='text/html'))
        page.goto('http://synthetic.test/', wait_until='networkidle')
        page.wait_for_function("getComputedStyle(document.querySelector('body')).boxSizing === 'border-box'")
        assert page.evaluate("getComputedStyle(document.body).rowGap === '28px'")
        row = page.locator('tr[data-interview-id="901"]')
        other = page.locator('tr[data-interview-id="903"]')
        panel = row.locator('.interview-notes-editor')
        def geometry():
            return page.evaluate('''() => { const left = document.querySelector('#interviews-table').getBoundingClientRect().x; return ({table: document.querySelector('#interviews-table').getBoundingClientRect().width,
                cells: [...document.querySelector('tr[data-interview-id="901"]').children].map(e => [e.getBoundingClientRect().x-left,e.getBoundingClientRect().width]),
                headers: [...document.querySelectorAll('thead th')].map(e => [e.getBoundingClientRect().width,e.getBoundingClientRect().height]),
                actions: [...document.querySelectorAll('tr[data-interview-id="901"] [data-col="actions"] a')].map(e => e.getBoundingClientRect().x-left)}) }''')
        row.locator("summary").scroll_into_view_if_needed()
        actions = row.locator('[data-col="actions"] a')
        first, second = [a.bounding_box() for a in actions.all()]
        assert first['y'] == second['y'], (width, 'actions must share one row')
        cell = row.locator('[data-col="actions"]').bounding_box()
        assert second['x'] + second['width'] <= cell['x'] + cell['width'], (width, 'actions fit cell')
        before = geometry()
        other_height = other.bounding_box()['height']
        old_height = row.bounding_box()['height']
        row.locator('summary').click()
        assert geometry() == before, (width, 'expanded geometry')
        assert other.bounding_box()['height'] == other_height
        assert row.bounding_box()['height'] > old_height
        assert 180 <= panel.bounding_box()['height'] <= 230
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        page.evaluate('window.scrollTo(0, 200)')
        assert panel.is_visible()
        row.locator('[data-notes-edit]').click()
        assert geometry() == before
        textarea = row.locator('textarea')
        assert 110 <= textarea.bounding_box()['height'] <= 140
        textarea.fill('discard')
        row.locator('[data-notes-cancel]').click()
        assert row.locator('[data-notes-text]').inner_text() == 'Short note\nSecond line'
        row.locator('[data-notes-edit]').click()
        textarea.fill('Saved\n' + 'Long line\n' * 80)
        calls = []
        def save(route):
            calls.append(route.request.post_data)
            route.fulfill(json={'ok': True, 'interview_id': 901, 'notes': 'Saved\n' + 'Long line\n' * 80})
        page.route('**/interviews/901/notes', save)
        row.locator('[data-notes-save]').click()
        page.wait_for_function("document.querySelector('[data-notes-text]').textContent.startsWith('Saved')")
        assert len(calls) == 1
        assert geometry() == before
        assert row.locator('[data-notes-text]').evaluate('(e) => e.scrollHeight > e.clientHeight')
        row.locator('[data-notes-edit]').click()
        textarea.fill('failed draft')
        page.unroute('**/interviews/901/notes')
        page.route('**/interviews/901/notes', lambda route: route.fulfill(status=503, json={'ok': False}))
        row.locator('[data-notes-save]').click()
        page.wait_for_function("document.querySelector('[role=status]').dataset.state === 'error'")
        assert textarea.input_value() == 'failed draft'
        row.locator('summary').click()
        row.locator('summary').click()
        assert row.locator('[data-notes-view]').is_visible()
        assert geometry() == before
        # A pending request cannot be duplicated; closing does not let its reply
        # reopen the panel or overwrite a newly selected row.
        row.locator('[data-notes-edit]').click()
        textarea.fill('pending save')
        page.unroute('**/interviews/901/notes')
        pending = []
        page.route('**/interviews/901/notes', lambda route: pending.append(route))
        row.locator('[data-notes-save]').click()
        page.wait_for_timeout(50)
        row.locator('form').evaluate('(e) => { e.requestSubmit(); e.requestSubmit(); }')
        page.wait_for_timeout(50)
        assert len(pending) == 1
        assert row.locator('[data-notes-save]').is_disabled()
        row.locator('summary').click()
        other.locator('summary').click()
        pending[0].fulfill(json={'ok': True, 'interview_id': 901, 'notes': 'pending save'})
        page.wait_for_function("!document.querySelector('.interview-notes-editor').hasAttribute('aria-busy')")
        assert not panel.is_visible()
        assert other.locator('.interview-notes-editor').is_visible()
        row.locator('summary').focus()
        page.keyboard.press('Enter')
        assert panel.is_visible()
        page.keyboard.press('Tab')
        assert row.locator('[data-notes-edit]').evaluate('(e) => e === document.activeElement')
        assert row.locator('[data-notes-text]').inner_text() == 'pending save'
        page.keyboard.press('Escape')
        assert not panel.is_visible()
        for action in row.locator('[data-col="actions"] a').all():
            action.scroll_into_view_if_needed()
            assert action.is_visible()
        print(f'{width}px: stable columns/actions, inline growth, compact card, internal scrolling, save/cancel/failure/collapse passed')
        # Render the other integrated flows with the same synthetic fixture.
        for url, content in pages.items():
            page.route('http://synthetic.test' + url, lambda route, request, content=content:
                       route.fulfill(body=content, content_type='text/html'))
            page.goto('http://synthetic.test' + url, wait_until='networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, url)
            for control in page.locator('a, button, input:not([type=hidden]), select, textarea').all():
                if control.is_visible():
                    control.scroll_into_view_if_needed()
                    assert control.bounding_box()['width'] > 0
            if url == '/tracker':
                assert page.locator('.interview-card').count() == 2
                assert page.locator('#interviews-table').count() == 0
                page.locator('[data-details-toggle]').click()
                assert page.locator('.details-panel-notes').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        print(f'{width}px: Tracker upcoming/details/practice and create/edit/delete pages fit')
        page.close()
    browser.close()
