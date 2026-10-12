"""Render pages in a dedicated local browser session to observe dynamically loaded players."""
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from vturb import player_scripts

ROOT = Path(__file__).resolve().parent
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.runtime/browsers')
browser_lock = threading.Lock()
PROFILE_DIR = ROOT / '.runtime/browser-profile'

VISIBLE_PLAYERS = r'''() => {
 const players=[];
 const walk=root=>{for(const el of root.querySelectorAll('*')){
   if(el.tagName==='VTURB-SMARTPLAYER'){
     const r=el.getBoundingClientRect();
     const shown=el.checkVisibility?el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}):getComputedStyle(el).display!=='none';
     if(shown&&r.width>4&&r.height>4&&r.right>0&&r.bottom>0){
       const c=el.config||el._setup||{};
       const duration=el.duration||el.playback?.video?.duration;
       players.push({player_id:c.id||(el.id||'').replace(/^(vid-|ab-)/,''),
         media_id:c.video?.id||null,duration:Number.isFinite(duration)?duration:null});
     }
   }
   if(el.shadowRoot)walk(el.shadowRoot);
 }};
 walk(document);return players;
}'''


def install_guard(context, domain, validate_url, same_domain):
    validated = set()
    def guard(route):
        request = route.request
        try:
            p = urlsplit(request.url)
            key = (p.scheme, p.netloc)
            if key not in validated:
                validate_url(request.url)
                validated.add(key)
            if request.is_navigation_request() and request.frame.parent_frame is None:
                if not same_domain(request.url, domain):
                    route.abort()
                    return
            route.continue_()
        except Exception:
            route.abort()
    context.route('**/*', guard)


def render_page(url, domain, validate_url, same_domain, interactive=False, cancelled=lambda: False,
                on_progress=lambda info: None):
    if not browser_lock.acquire(blocking=False):
        raise ValueError('O navegador de análise está em uso. Aguarde a varredura atual terminar.')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=not interactive,
                viewport={'width': 1100, 'height': 850}, locale='pt-BR',
                service_workers='block', accept_downloads=False)
            try:
                install_guard(context, domain, validate_url, same_domain)
                page = context.pages[0] if context.pages else context.new_page()
                snapshots = {}
                target_path = urlsplit(url).path.rstrip('/')
                latest = {'url':url,'html':'','media':[],'scripts':[], 'visible_players':[], 'manifest_captures':[]}
                def eligible(tab):
                    return (not tab.is_closed() and same_domain(tab.url, domain)
                            and (interactive or urlsplit(tab.url).path.rstrip('/') == target_path))
                def snapshot(tab):
                    if tab not in snapshots or snapshots[tab]['url'] != tab.url:
                        snapshots[tab] = {'url':tab.url, 'scripts':set(), 'media':set(), 'captures':{}}
                    return snapshots[tab]
                def observe(response):
                    try:
                        tab = response.request.frame.page
                        if response.status >= 400 or not eligible(tab):
                            return
                        state = snapshot(tab)
                        state['scripts'].update(player_scripts(response.url))
                        if urlsplit(response.url).path.lower().endswith(('.m3u8', '.mpd')):
                            state['media'].add(response.url)
                            if response.url.lower().split('?')[0].endswith('.m3u8'):
                                body = response.text()
                                if len(body) <= 2_000_000 and body.startswith('#EXTM3U'):
                                    headers = {k:v for k,v in response.request.all_headers().items()
                                               if not k.startswith(':') and k.lower() not in ('host','content-length','accept-encoding')}
                                    state['captures'][response.url] = {'url':response.url,'body':body,'headers':headers}
                    except Exception:
                        pass
                context.on('response', observe)
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
                if not interactive:
                    # Trigger lazy-loaded scripts without clicking calls to action or submitting forms.
                    page.mouse.move(150, 180)
                    page.mouse.wheel(0, 500)
                deadline = time.monotonic() + (600 if interactive else 25)
                found_at = None
                while time.monotonic() < deadline and not cancelled():
                    tabs = [tab for tab in context.pages if not tab.is_closed()]
                    if not tabs:
                        break
                    try:
                        tabs[-1].wait_for_timeout(400)
                    except Exception:
                        if tabs[-1].is_closed():
                            continue
                        raise
                    captured = False
                    for tab in tabs:
                        if not eligible(tab):
                            continue
                        state = snapshot(tab)
                        parts = []
                        visible_players = []
                        for frame in tab.frames:
                            try:
                                parts.append(frame.content())
                                visible_players.extend(frame.evaluate(VISIBLE_PLAYERS))
                            except Exception:
                                pass
                        html = '\n'.join(parts)
                        state['scripts'].update(player_scripts(html))
                        latest = {'url':tab.url, 'html':html, 'media':sorted(state['media']),
                                  'scripts':sorted(state['scripts']), 'visible_players':visible_players,
                                  'manifest_captures':list(state['captures'].values())}
                        on_progress({'url':tab.url, 'players':len(visible_players),
                                     'manifests':len(state['media'])})
                        if visible_players and (state['scripts'] or state['media']):
                            captured = True
                            # Resolve immediately while the page is open. A script alone
                            # may require more time before its playback manifest is available.
                            resolved = on_progress(dict(latest, capture=True))
                            if resolved or (resolved is None and state['scripts'] and not interactive):
                                found_at = found_at or time.monotonic()
                    if captured and found_at and time.monotonic() - found_at >= 2:
                        break
                    if not captured:
                        found_at = None
                html, scripts, media = latest['html'], latest['scripts'], latest['media']
                # Only include requested player scripts, not unused video variants in API responses.
                html += '\n' + '\n'.join(f'<script src="{script}"></script>' for script in scripts)
                warnings = []
                if not scripts and not media:
                    warnings.append('Nenhum player carregou na sessão de análise. Se o vídeo aparece após um quiz ou login, use “Abrir página e aguardar o vídeo” ou cole o código do player já carregado.')
                return {'url': latest['url'], 'html': html, 'media': sorted(media), 'warnings': warnings,
                        'visible_players':latest['visible_players'], 'manifest_captures':latest['manifest_captures']}
            finally:
                context.close()
    finally:
        browser_lock.release()
