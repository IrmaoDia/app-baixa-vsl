"""Select and preserve the HLS playlist received by a visible playback element."""
import re
from urllib.parse import urljoin, urlsplit


def select_playlist(config, captures, runtime_duration=None):
    oid = str(config['video'].get('oid') or config['oid'])
    media_id = str(config['video']['id'])
    expected = f'/{oid}/{media_id}/'
    bait_id = str(config.get('fkdlvid') or '')
    resolutions = {}
    for capture in captures:
        lines = capture.get('body', '').splitlines()
        for index, line in enumerate(lines[:-1]):
            if line.startswith('#EXT-X-STREAM-INF:'):
                match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
                if match:
                    resolutions[urljoin(capture['url'], lines[index+1].strip())] = tuple(map(int, match.groups()))
    valid = []
    for capture in captures:
        body = capture.get('body', '')
        if not body.startswith('#EXTM3U') or '#EXTINF:' not in body or '#EXT-X-ENDLIST' not in body:
            continue
        references = [urljoin(capture['url'], line.strip()) for line in body.splitlines()
                      if line.strip() and not line.startswith('#')]
        # The media id inside the segment URLs must match the visible player's config.
        if not references or any(expected not in urlsplit(url).path for url in references):
            continue
        if bait_id and any(f'/{bait_id}/' in urlsplit(url).path for url in references):
            continue
        duration = sum(float(value) for value in re.findall(r'#EXTINF:([0-9.]+)', body))
        if runtime_duration and abs(duration - runtime_duration) > max(2, runtime_duration * .01):
            continue
        width, height = resolutions.get(capture['url'], (0, 0))
        valid.append(dict(capture, duration=duration, width=width, height=height,
                          media_id=media_id, player_id=config['id']))
    return max(valid, key=lambda item:item['width'] * item['height']) if valid else None


def absolute_playlist(body, source_url, validate_url):
    if not body.startswith('#EXTM3U') or '#EXT-X-STREAM-INF:' in body:
        raise ValueError('É necessário um manifesto de segmentos capturado na reprodução.')
    validated = {}
    def absolute(raw):
        url = urljoin(source_url, raw)
        host = urlsplit(url).netloc
        if urlsplit(url).scheme not in ('http','https'):
            raise ValueError('Referência inválida no manifesto de reprodução.')
        if host not in validated:
            validate_url(url)
            validated[host] = True
        return url
    output = []
    for line in body.splitlines():
        if line and not line.startswith('#'):
            line = absolute(line.strip())
        elif 'URI="' in line:
            line = re.sub(r'URI="([^"]+)"',lambda m:'URI="'+absolute(m.group(1))+'"',line)
        output.append(line)
    return '\n'.join(output) + '\n'
