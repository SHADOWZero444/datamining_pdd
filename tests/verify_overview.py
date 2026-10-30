import sys
import sys, threading, json
from pathlib import Path
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
from support import artifact_dir
output = artifact_dir()
class Handler(SimpleHTTPRequestHandler):
    def log_message(self,*args): pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(root/'web')))
threading.Thread(target=server.serve_forever,daemon=True).start()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='msedge')
    page=browser.new_page(viewport={'width':1280,'height':850})
    errors=[]
    page.route('**/icons/*',lambda r:r.fulfill(path=str(root/'icons'/r.request.url.rsplit('/',1)[-1])))
    page.on('pageerror',lambda err: errors.append(str(err)))
    page.route('**/api/**',lambda r:r.fulfill(json={'keywords':[],'library':{'groups':[],'entries':[]},'job':{'running':False},'auth':{'configured':False}}))
    page.goto(f'http://127.0.0.1:{server.server_port}')
    page.locator('[data-section="tools"]').click()
    page.wait_for_selector('.feature-card')
    assert page.locator('.feature-card').count()==2
    assert len(page.locator('.feature-grid').evaluate('(el)=>getComputedStyle(el).gridTemplateColumns').split())>=4
    assert page.locator('.feature-card').first.bounding_box()['width']<250
    # 临时增加到五张，检查前四张同排、第五张换行，不改正式配置。
    page.evaluate("const grid=document.querySelector('.feature-grid'); for(let i=0;i<3;i++)grid.append(grid.lastElementChild.cloneNode(true))")
    page.wait_for_timeout(350)
    boxes=page.locator('.feature-card').evaluate_all('(els)=>els.map(el=>({x:el.offsetLeft,y:el.offsetTop}))')
    assert len(set(b['y'] for b in boxes[:4]))==1 and boxes[4]['y']>boxes[0]['y'],boxes
    page.screenshot(path=str(output/'overview-four-columns.png'))
    page.evaluate('renderStage()')
    assert page.locator('[data-feature="coming-soon"]').is_disabled()
    for theme in ('day','ink','warm'):
        page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
        page.wait_for_timeout(350)
        page.screenshot(path=str(output/f'overview-{theme}.png'))
    page.evaluate("document.documentElement.dataset.theme='day'")
    page.locator('[data-feature="keyword-search"]').focus()
    page.keyboard.press('Enter')
    page.wait_for_selector('#search-form')
    page.locator('[data-section="tools"]').click()
    page.wait_for_timeout(350)
    box=page.locator('.feature-art').first.bounding_box()
    page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2)
    page.wait_for_selector('#search-form')
    page.locator('[data-section="tools"]').click()
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(output/'overview-narrow.png'))
    assert page.locator('#stage').evaluate('(el)=>el.scrollWidth <= el.clientWidth')
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('.feature-card').first.evaluate('(el)=>getComputedStyle(el).animationName')=='none'
    assert not errors,errors
    # 仅用项目现有图标模拟图片加载，验证尺寸；不制作或交付卡片插画。
    page.route('**/assets/features/*.png',lambda r:r.fulfill(path=str(root/'icons/nav-tools.png')))
    page.evaluate('renderStage()')
    page.wait_for_selector('.feature-art img')
    assert page.locator('.feature-art img').count()==2
    ratio=page.locator('.feature-art').first.evaluate('(el)=>el.clientWidth/el.clientHeight')
    assert abs(ratio-16/9)<.03,ratio
    print(json.dumps({'cards':2,'themes':3,'keyboard':'passed','whole_card_click':'passed','narrow_overflow':False,'reduced_motion':'passed','errors':errors}))
    browser.close()
server.shutdown()
