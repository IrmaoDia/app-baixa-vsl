"""Teste opcional com internet; requer o serviço local em execução."""
import time
import requests

BASE = 'http://127.0.0.1:8765'


def post(path, body):
    response = requests.post(BASE + path, json=body, timeout=15)
    response.raise_for_status()
    return response.json()


def wait(path):
    for _ in range(120):
        response = requests.get(BASE + path, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data['status'] in ('done', 'error', 'cancelled'):
            assert data['status'] == 'done', data
            return data
        time.sleep(1)
    raise AssertionError('Tempo limite excedido')


if __name__ == '__main__':
    assert requests.get(BASE, timeout=10).status_code == 200
    assert requests.post(BASE + '/api/scans', json={'url':'http://127.0.0.1/'}).status_code == 400
    assert requests.get(BASE, headers={'Host':'external.example'}).status_code == 403
    scan = post('/api/scans', {'url':'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'})
    result = wait('/api/scans/' + scan['id'])
    assert result['videos'], result
    print('HTTP, validacao e deteccao: OK', flush=True)
    for mode in ('video', 'audio'):
        job = post('/api/downloads', {'scan_id':scan['id'], 'video_id':result['videos'][0]['id'], 'mode':mode})
        result_job = wait('/api/downloads/' + job['id'])
        file = requests.get(BASE + '/api/downloads/' + job['id'] + '/file', timeout=30)
        assert file.status_code == 200 and len(file.content) > 1000
        if mode == 'audio':
            assert result_job['filename'].endswith('.mp3')
        print(mode, 'OK:', len(file.content), 'bytes', flush=True)
