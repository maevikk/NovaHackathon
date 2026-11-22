"""Optional end-to-end browser test. Requires playwright + Chromium.
Run: python3 tests/browser_smoke.py
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def serve():
    sock=socket.socket();sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1];sock.close()
    proc=subprocess.Popen([sys.executable,'-m','backend.server','--port',str(port)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    for _ in range(60):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/health',timeout=.3)
            return proc,f'http://127.0.0.1:{port}/'
        except Exception:
            time.sleep(.1)
    proc.terminate();raise AssertionError('server did not start')


def run():
    from playwright.sync_api import sync_playwright
    proc,url=serve()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
            # Chromium here forbids navigation to HTTP by policy. Load the
            # project's exact HTML/CSS/JS inline, but forward its API calls to
            # the real local Python server through an instrumented bridge.
            import http.cookiejar,urllib.error
            html=(ROOT/'frontend/index.html').read_text('utf-8')
            css=(ROOT/'frontend/styles.css').read_text('utf-8')
            js=(ROOT/'frontend/app.js').read_text('utf-8')
            html=html.replace('<link rel="stylesheet" href="/styles.css">','<style>'+css+'</style>')
            html=html.replace('<script src="/app.js" defer></script>', '<script>window.fetch=async (endpoint,options={})=>{const result=await window.__realBackend(endpoint,options);return {ok:result.status>=200&&result.status<300,status:result.status,json:async()=>JSON.parse(result.body)};};</script><script>'+js+'</script>' )
            def install_page(page):
                jar=http.cookiejar.CookieJar()
                opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                def request_backend(source,endpoint,options):
                    payload=options.get('body')
                    headers={'Content-Type':'application/json'} if payload is not None else {}
                    req=urllib.request.Request(url+endpoint.lstrip('/'),data=payload.encode('utf-8') if payload is not None else None,headers=headers)
                    try:response=opener.open(req,timeout=10)
                    except urllib.error.HTTPError as exc:response=exc
                    status=response.status
                    api_replies.append((options.get('method','GET'),endpoint.rsplit('/',1)[-1],status))
                    return {'status':status,'body':response.read().decode('utf-8')}
                page.expose_binding('__realBackend',request_backend)
                page.set_content(html,wait_until='load')
            page=browser.new_page(viewport={'width':1440,'height':900},accept_downloads=True)
            page.set_default_timeout(10000)
            errors=[];failed=[];api_replies=[]
            page.on('pageerror',lambda e: errors.append(str(e)))
            page.on('console',lambda e: failed.append(e.text) if e.type=='error' else None)
            install_page(page)
            page.locator('#password').fill('demo2026');page.locator('#login-form button[type=submit]').click()
            page.locator('.sidebar').wait_for(timeout=20000)
            assert 'PROCESS LAB' in page.locator('.sidebar').inner_text()
            page.locator('[data-view="map"]').click()
            page.locator('#flow-space svg').wait_for()
            page.screenshot(path=str(ROOT/'docs/previews/kpsc_verified.png'),full_page=True)
            assert page.locator('#flow-space [data-node]').count()==12
            assert page.locator('.map-tools').count()==1
            # Drag only a visual position; no simulation request is needed.
            page.locator('#flow-space').evaluate('(e)=>e.scrollLeft=0')
            g=page.locator('#flow-space [data-node="o1"]')
            rect=g.bounding_box()
            assert rect, 'drag target not visible'
            src=(rect['x']+rect['width']/2,rect['y']+rect['height']/2)
            before=page.evaluate('JSON.stringify(state.model.edges)')
            page.mouse.move(*src);page.mouse.down();page.mouse.move(src[0]+70,src[1]+35,steps=8);page.mouse.up()
            layout=page.evaluate('state.model.kpsc_layout?.o1')
            assert layout and layout['x']>0, f'Drag did not save: {layout}'
            assert before==page.evaluate('JSON.stringify(state.model.edges)'),'drag changed graph topology'
            with page.expect_download(timeout=5000) as dl:
                page.locator('[data-action="export"]').click()
            downloaded=json.loads(Path(dl.value.path()).read_text('utf-8'))
            assert downloaded['kpsc_layout']['o1']==layout,'export lost drag positions'
            page.locator('[data-action="reset-layout"]').click()
            assert page.evaluate('state.model.kpsc_layout===undefined')
            # AS-IS is a read-only baseline; editing lives in the TO-BE scenario.
            page.locator('[data-action="goto-target"]').first.click()
            page.locator('#flow-space [data-node="o8"]').click()
            inp=page.locator('[data-node-field="cycle_s"][data-node-id="o8"]')
            inp.fill('125');inp.press('Tab')
            page.wait_for_function("state.model.nodes.find(n=>n.id==='o8').cycle_s===125 && state.result.potential_bottleneck.cycle_s===125",timeout=10000)
            assert ('POST','simulate',200) in api_replies
            assert ('POST','validate',200) in api_replies
            # The original AS-IS card remains unchanged after modifying TO-BE.
            page.locator('#stage-select').select_option('current')
            assert page.evaluate("state.baseline.nodes.find(n=>n.id==='o8').cycle_s")==171
            assert '171 с' in page.locator('#flow-space [data-node="o8"]').text_content()
            page.locator('#stage-select').select_option('target')
            assert '125 с' in page.locator('#flow-space [data-node="o8"]').text_content()
            # Reject invalid model through server; UI must not keep it.
            page.locator('[data-node-field="workers"][data-node-id="o8"]').fill('0')
            page.locator('[data-node-field="workers"][data-node-id="o8"]').press('Tab')
            assert page.evaluate("state.model.nodes.find(n=>n.id==='o8').workers") == 1
            page.locator('[data-view="scenarios"]').click()
            page.locator('[data-action="compare"]').click()
            page.wait_for_function('state.comparison!==null',timeout=10000)
            assert ('POST','compare',200) in api_replies
            # Capture base-factory metrics before swapping datasets.
            page.locator('[data-view="overview"]').click()
            page.screenshot(path=str(ROOT/'docs/previews/dashboard_factory_verified.png'),full_page=True)
            # Other model: no factory-specific labels should leak into generic dashboard.
            page.locator('[data-view="sources"]').click();page.locator('[data-action="generic"]').click()
            page.wait_for_function("state.model.status==='synthetic_demo'",timeout=10000)
            page.locator('[data-view="floor"]').click()
            assert 'пока не загружен' in page.locator('main').inner_text().lower()
            # ensure no 500/server crashes, and desktop visually valid
            page.locator('[data-view="overview"]').click()
            page.screenshot(path=str(ROOT/'docs/previews/dashboard_verified.png'),full_page=True)
            mobile=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
            mobile.set_default_timeout(10000)
            mobile.on('pageerror',lambda e: errors.append('mobile:'+str(e)))
            install_page(mobile)
            mobile.locator('#password').fill('demo2026'); mobile.locator('#login-form button[type=submit]').click()
            mobile.locator('.sidebar').wait_for(timeout=20000)
            mobile.locator('[data-view="map"]').click()
            assert mobile.locator('#flow-space svg').count()==1
            mobile.screenshot(path=str(ROOT/'docs/previews/map_mobile_verified.png'),full_page=True)
            assert not errors,errors
            assert not failed,failed
            assert not any(status>=500 for _,_,status in api_replies),api_replies
            print(json.dumps({'browser':'Chromium','result':'PASS','desktop_drag':True,'backend_roundtrip':True,'compare':True,'generic':True,'mobile':True,'api_calls':api_replies,'errors':errors},ensure_ascii=False,indent=2))
            mobile.close();browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill()

if __name__=='__main__':run()
