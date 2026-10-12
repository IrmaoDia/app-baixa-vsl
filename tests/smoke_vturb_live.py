"""Teste manual com internet e servidor local: página VTurb, vídeo e áudio reais."""
import time
import requests
from app import fetch_player_text
from vturb import discover_vturb

BASE = 'http://127.0.0.1:8765'
PAGE = 'https://protocolopessoal.online/iniciar'


def wait(path):
    for _ in range(300):
        response = requests.get(BASE + path, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data['status'] in ('done', 'error', 'cancelled'):
            assert data['status'] == 'done', data
            return data
        time.sleep(1)
    raise AssertionError('Tempo limite excedido')


def post(path, body):
    response = requests.post(BASE + path, json=body, timeout=15)
    response.raise_for_status()
    return response.json()


if __name__ == '__main__':
    example = '<script>s.src="https://scripts.converteai.net/b4d92c20-2920-493b-982b-7f8dd3923ccb/players/6ab9cb8a44e77e9c402a6433/v4/player.js"</script>'
    videos, warnings = discover_vturb(example, PAGE, fetch_player_text)
    assert videos and not warnings, warnings
    print('Formato /players/.../v4/player.js: OK', flush=True)
    scan = post('/api/scans', {'url': PAGE})
    result = wait('/api/scans/' + scan['id'])
    found = next(v for v in result['videos'] if v['extractor'] == 'VTurb')
    print('Pagina real e player AB: OK', flush=True)
    for mode in ('video', 'audio'):
        job = post('/api/downloads', {'scan_id':scan['id'], 'video_id':found['id'], 'mode':mode})
        finished = wait('/api/downloads/' + job['id'])
        response = requests.get(BASE + '/api/downloads/' + job['id'] + '/file', stream=True, timeout=30)
        response.raise_for_status()
        size = sum(len(chunk) for chunk in response.iter_content(65536))
        assert size > 1000
        if mode == 'audio':
            assert finished['filename'].endswith('.mp3')
        print(mode, 'OK:', size, 'bytes', flush=True)
