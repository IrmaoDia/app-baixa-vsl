"""Read public VTurb player configuration without executing remote JavaScript."""
import re
from html import unescape
from urllib.parse import urljoin, urlsplit

import chompjs
from hls_capture import select_playlist

PLAYER_SCRIPT = re.compile(
    r'''(?:https?:)?//scripts\.converteai\.net/[^\s"'<>\\]+/(?:players|ab-test)/[^\s"'<>\\]+/player\.js(?:\?[^\s"'<>\\]*)?''', re.I)


def player_scripts(html):
    # Inline script assignments and preload links also work with LiteSpeed delayed scripts.
    normalized = unescape(html).replace('\\/', '/').replace('\\"', '"').replace("\\'", "'")
    normalized = re.sub(r'\\(?:u002[fF]|x2[fF])', '/', normalized)
    return list(dict.fromkeys('https:' + url if url.startswith('//') else url
                             for url in PLAYER_SCRIPT.findall(normalized)))[:8]


def enabled(value):
    return value is True or value == 1 or value in ('!0', 'true')


def video_configs(script):
    # Only parse the data object: no eval, function calls or browser execution.
    match = re.search(r'''\{\s*["']?elementId["']?\s*:''', script)
    if not match:
        raise ValueError('Configuração do player VTurb não reconhecida.')
    root = chompjs.parse_js_object(script[match.start():], loader_kwargs={'strict': False})
    def walk(item):
        if isinstance(item, dict):
            if isinstance(item.get('video'), dict) and item.get('oid'):
                yield item
            else:
                for value in item.values():
                    yield from walk(value)
        elif isinstance(item, list):
            for value in item:
                yield from walk(value)
    return list(walk(root))


def discover_vturb(html, page, fetch_text, observed_media=(), visible_players=None, manifest_captures=()):
    results, warnings, seen = [], [], set()
    for script_url in player_scripts(html):
        try:
            script = fetch_text(script_url, page)
            configs = video_configs(script)
            if not configs:
                raise ValueError('O script não contém uma configuração de vídeo.')
            for config in configs:
                video = config['video']
                visible = None
                if visible_players is not None:
                    visible = next((item for item in visible_players if item['player_id'] == config.get('id')
                                    and (not item.get('media_id') or item['media_id'] == video.get('id'))),None)
                    if not visible:
                        continue
                if enabled(video.get('drm')):
                    warnings.append('Player VTurb protegido; extração da mídia não disponível.')
                    continue
                oid, video_id = str(video.get('oid') or config['oid']), str(video.get('id') or '')
                if not re.fullmatch(r'[a-zA-Z0-9-]+', oid) or not re.fullmatch(r'[a-zA-Z0-9-]+', video_id):
                    raise ValueError('Identificadores de mídia inválidos no player.')
                cdn = config.get('cdn') or 'cdn.converteai.net'
                if not isinstance(cdn, str) or not (cdn == 'converteai.net' or cdn.endswith('.converteai.net')):
                    raise ValueError('CDN do player não reconhecida.')
                path = video.get('manifestPath') or f"{oid}/{video_id}/{video.get('hlsFile') or 'main.m3u8'}"
                manifest = urljoin(f'https://{cdn}/', path)
                if urlsplit(manifest).hostname != cdn:
                    raise ValueError('Manifesto fora da CDN configurada.')
                captured = select_playlist(config, manifest_captures, (visible or {}).get('duration'))
                if captured:
                    manifest = captured['url']
                elif config.get('fkdlvid'):
                    warnings.append('Esse player pode devolver mídia alternativa fora da reprodução. É necessário capturar os segmentos do vídeo visível na janela de análise.')
                    continue
                elif visible_players is not None:
                    warnings.append('Player visível identificado; aguardando os segmentos da sua reprodução. Inicie o vídeo na janela de análise.')
                    continue
                elif enabled(config.get('secure')):
                    # Secure delivery is distinct from DRM. Use only the address actually
                    # requested by the player's normal playback session, including its token.
                    matching = [url for url in observed_media
                        if urlsplit(url).path.endswith('/' + path.lstrip('/'))
                        and (urlsplit(url).hostname == 'm3u8.vturb.net'
                             or (urlsplit(url).hostname or '').endswith('.converteai.net'))]
                    if not matching:
                        warnings.append('VTurb com mídia de sessão: aguardando o manifesto carregado pelo player. Ative a análise com JavaScript ou abra a página durante a varredura.')
                        continue
                    manifest = matching[0]
                if manifest in seen:
                    continue
                text = captured['body'] if captured else fetch_text(manifest, page)
                if not text.lstrip('\ufeff \r\n').startswith('#EXTM3U'):
                    raise ValueError('A CDN não retornou um manifesto HLS válido.')
                seen.add(manifest)
                results.append({'url': manifest, 'title': config.get('name') or 'Vídeo VTurb',
                    'thumbnail': video.get('cover') or video.get('poster'),
                    'ext': 'm3u8', 'extractor_key': 'VTurb',
                    'duration': captured['duration'] if captured else video.get('duration'),
                    'player_script': script_url, 'captured_playlist': captured,
                    'identity_verified': bool(captured), 'media_id': video_id,
                    'visible_player_id': (visible or {}).get('player_id')})
        except Exception as exc:
            warnings.append(f'VTurb: {str(exc)[:240]}')
    return results, warnings
