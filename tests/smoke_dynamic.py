"""Browser integration test with controlled dynamic content; no quiz answers or external requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import time
from playwright.sync_api import BrowserType

from app import same_domain
from browser_scan import render_page
from vturb import player_scripts

PLAYER = 'https://scripts.converteai.net/account/players/media/v4/player.js'
HTML = '''<html><head></head><body><h1>Fixture</h1><div style="display:none"><vturb-smartplayer id="vid-hidden" style="display:block;width:200px;height:200px"></vturb-smartplayer></div><script>
setTimeout(async () => {
  const config = await (await fetch('/video-config')).json();
  const player = document.createElement('vturb-smartplayer'); player.id = 'vid-media';
  player.style.cssText = 'display:block;width:200px;height:200px';
  document.body.appendChild(player);
  const script = document.createElement('script'); script.src = config.script;
  document.head.appendChild(script);
}, 600);
</script></body></html>'''


def fixture_routes(context, domain, validate_url, boundary):
    def serve(route):
        if route.request.url.endswith('/video-config'):
            route.fulfill(content_type='application/json', body='{"script":"' + PLAYER + '"}')
        elif route.request.url == PLAYER:
            route.fulfill(content_type='application/javascript', body='window.fixturePlayerLoaded = true;')
        else:
            route.fulfill(content_type='text/html', body=HTML)
    context.route('**/*', serve)


def redirect_fixture(context, domain, validate_url, boundary):
    def serve(route):
        if route.request.url.endswith('/page'):
            route.fulfill(content_type='text/html', body='<script>location.replace("/quiz")</script>')
        elif route.request.url == PLAYER:
            route.fulfill(content_type='application/javascript', body='')
        else:
            route.fulfill(content_type='text/html', body='<vturb-smartplayer id="vid-media" style="display:block;width:200px;height:200px"></vturb-smartplayer><script src="' + PLAYER + '"></script>')
    context.route('**/*', serve)


if __name__ == '__main__':
    with TemporaryDirectory() as directory, patch('browser_scan.PROFILE_DIR', Path(directory)), patch('browser_scan.install_guard', fixture_routes):
        for domain in ('first.example', 'second.example'):
            result = render_page(f'https://{domain}/page', domain, lambda url:url, same_domain)
            assert player_scripts(result['html']) == [PLAYER], result
            assert not result['warnings'], result
            assert [item['player_id'] for item in result['visible_players']] == ['media'], result
            print('Player dinamico via API:', domain, 'OK', flush=True)
    with TemporaryDirectory() as directory, patch('browser_scan.PROFILE_DIR', Path(directory)), patch('browser_scan.install_guard', redirect_fixture):
        started = time.monotonic()
        result = render_page('https://third.example/page', 'third.example', lambda url:url, same_domain,
                             cancelled=lambda:time.monotonic()-started>4)
        assert not player_scripts(result['html']) and not result['media'], result
        assert result['warnings'], result
        print('Player de outra etapa do quiz nao confundido com pagina solicitada: OK',flush=True)
    original_launch = BrowserType.launch_persistent_context
    def headless_launch(self, *args, **kwargs):
        kwargs['headless'] = True
        return original_launch(self, *args, **kwargs)
    captured = []
    def progress(info):
        if info.get('capture'):
            captured.append(info)
            return True
    with TemporaryDirectory() as directory, patch('browser_scan.PROFILE_DIR', Path(directory)), patch('browser_scan.install_guard', redirect_fixture), patch.object(BrowserType, 'launch_persistent_context', headless_launch):
        result = render_page('https://fourth.example/page', 'fourth.example', lambda url:url, same_domain,
                             interactive=True, on_progress=progress)
        assert player_scripts(result['html']) == [PLAYER], result
        assert result['url'] == 'https://fourth.example/quiz', result
        assert captured and captured[0]['url'] == result['url']
        print('Modo interativo acompanha mudanca de URL e entrega captura imediatamente: OK',flush=True)
