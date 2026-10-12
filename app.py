from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
import copy
import ipaddress
import re
import socket
import subprocess
import threading
import time
import uuid

import imageio_ffmpeg
import requests
import yt_dlp
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from vturb import discover_vturb, player_scripts
from browser_scan import render_page
from hls_capture import absolute_playlist

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / 'downloads'
DOWNLOADS.mkdir(exist_ok=True)
app = FastAPI(title='VideoScope')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
pool = ThreadPoolExecutor(max_workers=3)
lock = threading.RLock()
scans = {}
jobs = {}
MEDIA = re.compile(r'\.(?:mp4|webm|mov|m4v|m3u8|mpd)(?:[?#]|$)', re.I)
UA = 'Mozilla/5.0 VideoScope/1.0'


@app.middleware('http')
async def local_origin(request: Request, call_next):
    if request.headers.get('host', '').split(':')[0] not in {'127.0.0.1', 'localhost'}:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Acesso permitido apenas pelo endereço local.'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin not in {'http://localhost:8765', 'http://127.0.0.1:8765'}:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Origem não permitida.'}, status_code=403)
    return await call_next(request)


def public_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Informe uma URL pública HTTP ou HTTPS válida.')
    if parts.port not in (None, 80, 443):
        raise ValueError('A URL deve usar a porta HTTP ou HTTPS padrão.')
    addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Endereços locais ou de redes privadas não são permitidos.')
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))


def same_domain(url, domain):
    host = (urlsplit(url).hostname or '').lower()
    return host == domain or host.endswith('.' + domain)


def fetch_page(url, domain):
    for _ in range(6):
        public_url(url)
        if not same_domain(url, domain):
            raise ValueError('Redirecionamento para fora do domínio ignorado.')
        with requests.get(url, headers={'User-Agent': UA}, timeout=(8, 15),
                          allow_redirects=False, stream=True) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers['Location'])
                continue
            response.raise_for_status()
            if 'html' not in response.headers.get('Content-Type', '').lower():
                return url, ''
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 4_000_000:
                    raise ValueError('Página excede o limite de 4 MB.')
                chunks.append(chunk)
            return url, b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace')
    raise ValueError('Redirecionamentos demais.')


def parse_page(html, page):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else urlsplit(page).hostname
    base_tag = soup.find('base', href=True)
    base = urljoin(page, base_tag['href']) if base_tag else page
    candidates = {}
    def add(raw, kind='direct', poster=None):
        if not raw:
            return
        url = urljoin(base, raw.strip())
        if urlsplit(url).scheme in ('http', 'https'):
            candidates.setdefault(url, {'url': url, 'kind': kind, 'thumbnail': urljoin(base, poster) if poster else None})
    for video in soup.find_all('video'):
        add(video.get('src') or video.get('data-src'), poster=video.get('poster'))
        for source in video.find_all('source'):
            add(source.get('src') or source.get('data-src'), poster=video.get('poster'))
    for tag in soup.find_all(['iframe', 'embed']):
        add(tag.get('src') or tag.get('data-src'), 'embed')
    for tag in soup.find_all('meta'):
        if tag.get('property', '').startswith('og:video') and 'type' not in tag.get('property', ''):
            add(tag.get('content'))
    for anchor in soup.find_all('a', href=True):
        if MEDIA.search(anchor['href']):
            add(anchor['href'])
    unescaped = html.replace('\\/', '/')
    for match in re.finditer(r'''["']([^"'<>\s]+\.(?:mp4|webm|m3u8|mpd)(?:\?[^"'<>\s]*)?)["']''', unescaped, re.I):
        add(match.group(1))
    links = [urljoin(base, a['href']) for a in soup.find_all('a', href=True)]
    return title, list(candidates.values()), links


def fetch_player_text(url, referer):
    for _ in range(6):
        public_url(url)
        with requests.get(url, headers={'User-Agent': UA, 'Referer': referer},
                          timeout=(8, 15), allow_redirects=False, stream=True) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers['Location'])
                continue
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 4_000_000:
                    raise ValueError('Configuração de player excede 4 MB.')
                chunks.append(chunk)
            return b''.join(chunks).decode('utf-8', errors='replace')
    raise ValueError('Redirecionamentos demais na configuração do player.')


class QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def extractor_options():
    return {'quiet': True, 'no_warnings': True, 'logger': QuietLogger(), 'socket_timeout': 12,
            'retries': 1, 'extractor_retries': 1, 'noplaylist': True, 'playlistend': 30,
            'cachedir': False, 'skip_download': True}


def flatten(info):
    if not info:
        return
    if info.get('entries') is not None:
        for entry in info['entries']:
            yield from flatten(entry)
    elif info.get('formats') or info.get('url'):
        yield info


def scan_worker(scan_id, config):
    state = scans[scan_id]
    queue, visited, seen = deque([(config.url, 0)]), set(), set()
    def record(url, page, title, kind, info=None, thumbnail=None):
        key = url.split('#')[0]
        with lock:
            media_id = (info or {}).get('media_id')
            existing = next((v for v in state['videos'] if media_id and v.get('media_id') == media_id and v['page'] == page),None)
            if existing:
                new_capture = (info or {}).get('captured_playlist') or {}
                old_capture = existing.get('_captured_playlist') or {}
                if new_capture.get('width',0)*new_capture.get('height',0) >= old_capture.get('width',0)*old_capture.get('height',0):
                    existing.update(url=url,duration=(info or {}).get('duration'),
                        identity_verified=(info or {}).get('identity_verified',False),
                        _captured_playlist=(info or {}).get('captured_playlist'))
                return
            if key in seen:
                return
            seen.add(key)
            state['videos'].append({'id': uuid.uuid4().hex, 'url': url, 'page': page,
                'title': (info or {}).get('title') or title, 'kind': kind,
                'thumbnail': (info or {}).get('thumbnail') or thumbnail,
                'duration': (info or {}).get('duration'), 'extension': (info or {}).get('ext'),
                'extractor': (info or {}).get('extractor_key') or 'HTML',
                'identity_verified': (info or {}).get('identity_verified',False),
                'media_id': (info or {}).get('media_id'),
                '_captured_playlist': (info or {}).get('captured_playlist')})
    try:
        while queue and len(visited) < config.max_pages:
            with lock:
                if state['cancelled']:
                    break
            page, depth = queue.popleft()
            if page in visited:
                continue
            visited.add(page)
            with lock:
                state.update(current=page, visited=len(visited), queued=len(queue))
            try:
                final_url, html = fetch_page(page, config.domain)
                if depth == 0 and config.player_code:
                    html += '\n' + config.player_code
                title, candidates, links = parse_page(html, final_url)
                provider_page = bool(player_scripts(html))
                if MEDIA.search(page):
                    record(page, page, Path(urlsplit(page).path).name, 'direct')
                for candidate in candidates:
                    if candidate['kind'] == 'direct' and not (provider_page and config.render_js):
                        record(candidate['url'], final_url, title, 'direct', thumbnail=candidate['thumbnail'])
                vturb_videos, vturb_warnings = discover_vturb(html, final_url, fetch_player_text)
                rendered_provider = False
                if (config.interactive and depth == 0) or (config.render_js and (provider_page or (not vturb_videos and not any(c['kind'] == 'direct' for c in candidates))) and not MEDIA.search(page)):
                    vturb_videos = []
                    rendered_provider = provider_page
                    with lock:
                        state['phase'] = 'interactive' if config.interactive and depth == 0 else 'rendering'
                    try:
                        resolved_captures = {}
                        def browser_progress(progress):
                            if not progress.get('capture'):
                                with lock:
                                    state['browser'] = progress
                                    state['current'] = progress['url']
                                return
                            signature = (progress['url'], tuple(progress['scripts']), tuple(progress['media']),
                                tuple((p['player_id'],p.get('media_id')) for p in progress.get('visible_players',[])),
                                tuple(c['url'] for c in progress.get('manifest_captures',[]) if '#EXTINF:' in c['body']))
                            if signature in resolved_captures:
                                return resolved_captures[signature]
                            capture_html = progress['html'] + '\n' + '\n'.join(progress['scripts'])
                            live_videos, _ = discover_vturb(capture_html, progress['url'], fetch_player_text,
                                observed_media=progress['media'], visible_players=progress.get('visible_players'),
                                manifest_captures=progress.get('manifest_captures',[]))
                            for live_video in live_videos:
                                record(live_video['url'], progress['url'], title, 'player', info=live_video)
                            resolved_captures[signature] = bool(live_videos)
                            return resolved_captures[signature]
                        rendered = render_page(final_url, config.domain, public_url, same_domain,
                            interactive=config.interactive and depth == 0,
                            cancelled=lambda: state['cancelled'], on_progress=browser_progress)
                        if rendered['html'] and config.interactive and depth == 0:
                            final_url = rendered['url']
                        rendered_provider = rendered_provider or bool(player_scripts(rendered['html']))
                        html += '\n' + rendered['html']
                        _, rendered_candidates, rendered_links = parse_page(rendered['html'], rendered['url'])
                        candidates.extend(rendered_candidates)
                        links.extend(rendered_links)
                        for candidate in rendered_candidates:
                            if candidate['kind'] == 'direct' and not rendered_provider:
                                record(candidate['url'], final_url, title, 'direct', thumbnail=candidate['thumbnail'])
                        vturb_videos, rendered_warnings = discover_vturb(html, final_url, fetch_player_text,
                            observed_media=rendered['media'], visible_players=rendered.get('visible_players'),
                            manifest_captures=rendered.get('manifest_captures',[]))
                        vturb_warnings = rendered_warnings
                        # Prefer the provider's master manifest over individual quality requests.
                        if not vturb_videos and not player_scripts(rendered['html']):
                            for media_url in rendered['media']:
                                record(media_url, final_url, title, 'direct')
                        with lock:
                            state['warnings'].extend({'page': page, 'message': w} for w in rendered['warnings'])
                    except Exception as exc:
                        with lock:
                            state['warnings'].append({'page': page, 'message': 'Página dinâmica: ' + str(exc)[:350]})
                    finally:
                        with lock:
                            state['phase'] = 'analysis'
                for video in vturb_videos:
                    record(video['url'], final_url, title, 'player', info=video)
                with lock:
                    state['warnings'].extend({'page': final_url, 'message': message} for message in vturb_warnings)
                # The generic extractor resolves embedded providers and script-configured players.
                try:
                    info = None
                    if not rendered_provider:
                        with yt_dlp.YoutubeDL(extractor_options()) as ydl:
                            info = ydl.extract_info(final_url, download=False)
                    for video in flatten(info):
                        target = video.get('webpage_url') or video.get('original_url') or final_url
                        if target == final_url and video.get('url') and MEDIA.search(video['url']):
                            target = video['url']
                        if any(c['url'] == video.get('url') for c in candidates):
                            continue
                        record(target, final_url, title, 'player', info=video)
                except Exception as exc:
                    if not vturb_videos:
                        with lock:
                            state['warnings'].append({'page': page, 'message': 'Extrator: ' + str(exc)[:240]})
                    for candidate in [c for c in candidates if c['kind'] == 'embed'][:10]:
                        try:
                            public_url(candidate['url'])
                            with yt_dlp.YoutubeDL(extractor_options()) as ydl:
                                embedded = ydl.extract_info(candidate['url'], download=False)
                            for video in flatten(embedded):
                                record(video.get('webpage_url') or candidate['url'], final_url, title, 'player', info=video)
                        except Exception:
                            pass
                if config.scope == 'domain' and depth >= config.max_depth:
                    if any(same_domain(link, config.domain) and link.split('#')[0] not in visited for link in links):
                        with lock:
                            state['limited'] = True
                if config.scope == 'domain' and depth < config.max_depth:
                    for link in links:
                        p = urlsplit(link)
                        normalized = urlunsplit((p.scheme, p.netloc, p.path or '/', p.query, ''))
                        if p.scheme in ('http', 'https') and same_domain(normalized, config.domain) and normalized not in visited:
                            if not re.search(r'\.(?:pdf|jpg|png|css|js|zip|svg|woff2?)(?:\?|$)', normalized, re.I):
                                if len(queue) < config.max_pages * 5:
                                    queue.append((normalized, depth + 1))
            except Exception as exc:
                with lock:
                    state['warnings'].append({'page': page, 'message': str(exc)[:300]})
            time.sleep(0.25)
        with lock:
            state['status'] = 'cancelled' if state['cancelled'] else 'done'
            state['limited'] = (state['limited'] or bool(queue)) and not state['cancelled']
            state['current'] = None
    except Exception as exc:
        with lock:
            state.update(status='error', error=str(exc))


class ScanInput(BaseModel):
    url: str
    domain: str = ''
    scope: str = 'page'
    max_pages: int = Field(default=30, ge=1, le=200)
    max_depth: int = Field(default=2, ge=0, le=5)
    render_js: bool = True
    interactive: bool = False
    player_code: str = Field(default='', max_length=300000)


class DownloadInput(BaseModel):
    scan_id: str
    video_id: str
    mode: str = 'video'


@app.get('/')
def index():
    return FileResponse(ROOT / 'static/index.html')


@app.post('/api/scans')
def start_scan(config: ScanInput):
    try:
        config.url = public_url(config.url.strip())
        raw_domain = config.domain.strip() or urlsplit(config.url).hostname
        config.domain = (urlsplit(raw_domain if '://' in raw_domain else 'https://' + raw_domain).hostname or '').lower()
        if not same_domain(config.url, config.domain):
            raise ValueError('A URL inicial deve pertencer ao domínio informado.')
        if config.scope not in ('page', 'domain'):
            raise ValueError('Modo de varredura inválido.')
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    with lock:
        if sum(s['status'] == 'running' for s in scans.values()) >= 2:
            raise HTTPException(429, 'Aguarde uma varredura terminar.')
        scan_id = uuid.uuid4().hex
        scans[scan_id] = {'id': scan_id, 'status': 'running', 'visited': 0, 'queued': 0,
                          'videos': [], 'warnings': [], 'cancelled': False, 'limited': False}
    pool.submit(scan_worker, scan_id, config)
    return {'id': scan_id}


@app.get('/api/scans/{scan_id}')
def scan_status(scan_id: str):
    with lock:
        if scan_id not in scans:
            raise HTTPException(404, 'Varredura não encontrada.')
        result = copy.deepcopy(scans[scan_id])
        result['videos'] = [{k:v for k,v in video.items() if not k.startswith('_')} for video in result['videos']]
        return result


@app.post('/api/scans/{scan_id}/cancel')
def cancel_scan(scan_id: str):
    with lock:
        if scan_id not in scans:
            raise HTTPException(404, 'Varredura não encontrada.')
        scans[scan_id]['cancelled'] = True
    return {'ok': True}


def download_worker(job_id, video, mode):
    directory = DOWNLOADS / job_id
    directory.mkdir()
    def progress(data):
        with lock:
            jobs[job_id]['status'] = 'downloading' if data['status'] == 'downloading' else 'processing'
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            jobs[job_id]['progress'] = round(data.get('downloaded_bytes', 0) / total * 100, 1) if total else None
    try:
        public_url(video['url'])
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        captured = video.get('_captured_playlist')
        if captured:
            output = download_captured(job_id,video,directory,mode,ffmpeg)
            with lock:
                jobs[job_id].update(status='done',progress=100,filename=output.name,path=str(output))
            return
        options = extractor_options()
        options.update(skip_download=False, outtmpl=str(directory / '%(title).100s [%(id)s].%(ext)s'),
                       ffmpeg_location=ffmpeg, progress_hooks=[progress],
                       format='bestaudio/best' if mode == 'audio' else 'bestvideo+bestaudio/best',
                       merge_output_format='mp4', http_headers={'Referer': video['page'], 'User-Agent': UA},
                       windowsfilenames=True, noplaylist=True, playlistend=1)
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([video['url']])
        files = [f for f in directory.iterdir() if f.is_file() and f.suffix not in ('.part', '.ytdl')]
        if not files:
            raise ValueError('O provedor não retornou um arquivo para download.')
        output = max(files, key=lambda f: f.stat().st_size)
        if mode == 'audio':
            with lock:
                jobs[job_id]['status'] = 'processing'
            converted = output.with_suffix('.mp3')
            if converted == output:
                converted = output.with_name(output.stem + ' audio.mp3')
            result = subprocess.run([ffmpeg, '-y', '-i', str(output), '-vn', '-codec:a', 'libmp3lame',
                                     '-b:a', '192k', str(converted)], capture_output=True,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('Não foi possível converter o áudio: ' + result.stderr.decode(errors='replace')[-400:])
            output = converted
        with lock:
            jobs[job_id].update(status='done', progress=100, filename=output.name, path=str(output))
    except Exception as exc:
        with lock:
            jobs[job_id].update(status='error', error=str(exc)[:700])


def download_captured(job_id,video,directory,mode,ffmpeg):
    captured = video['_captured_playlist']
    playlist = directory / 'playback.m3u8'
    playlist.write_text(absolute_playlist(captured['body'],captured['url'],public_url),encoding='utf-8')
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',video['title']).strip(' .')[:100] or 'Video'
    if title.lower().endswith(('.mp4','.webm','.mov','.m4v')):
        title = Path(title).stem
    output = directory / (title + ('.mp3' if mode=='audio' else '.mp4'))
    command = [ffmpeg,'-y','-loglevel','error','-nostats','-progress','pipe:1',
               '-protocol_whitelist','file,http,https,tcp,tls,crypto','-allowed_extensions','ALL',
               '-rw_timeout','20000000']
    command += ['-i',str(playlist)]
    command += ['-vn','-codec:a','libmp3lame','-b:a','192k'] if mode=='audio' else ['-c','copy','-movflags','+faststart']
    command += [str(output)]
    with (directory/'ffmpeg.log').open('w',encoding='utf-8') as error_log:
        process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=error_log,text=True,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with lock:
            jobs[job_id]['status']='downloading'
        for line in process.stdout:
            if line.startswith('out_time_us='):
                try:
                    seconds=int(line.split('=',1)[1])/1_000_000
                    with lock:
                        jobs[job_id]['progress']=round(min(99,seconds/captured['duration']*100),1)
                except (ValueError,ZeroDivisionError):
                    pass
        result=process.wait()
    if result or not output.exists():
        raise ValueError('Falha ao salvar a reprodução capturada: '+(directory/'ffmpeg.log').read_text(encoding='utf-8')[-500:])
    return output


@app.post('/api/downloads')
def start_download(config: DownloadInput):
    if config.mode not in ('video', 'audio'):
        raise HTTPException(400, 'Escolha vídeo ou áudio.')
    with lock:
        scan = scans.get(config.scan_id)
        video = next((v for v in (scan or {}).get('videos', []) if v['id'] == config.video_id), None)
        if not video:
            raise HTTPException(404, 'Vídeo não encontrado nesta varredura.')
        if sum(j['status'] not in ('done', 'error') for j in jobs.values()) >= 3:
            raise HTTPException(429, 'Aguarde os downloads em andamento.')
        job_id = uuid.uuid4().hex
        jobs[job_id] = {'id': job_id, 'status': 'queued', 'progress': 0, 'title': video['title'], 'mode': config.mode}
    pool.submit(download_worker, job_id, copy.deepcopy(video), config.mode)
    return {'id': job_id}


@app.get('/api/downloads/{job_id}')
def download_status(job_id: str):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, 'Download não encontrado.')
        return {k: v for k, v in jobs[job_id].items() if k != 'path'}


@app.get('/api/downloads/{job_id}/file')
def download_file(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job or job['status'] != 'done':
            raise HTTPException(404, 'Arquivo ainda não disponível.')
        return FileResponse(job['path'], filename=job['filename'])
