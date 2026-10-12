"""Manual live test using the quiz answers already saved by the user in the analysis profile."""
import time
import requests

BASE='http://127.0.0.1:8765'
PAGE='https://quiz.astranumerica.com/vidas-passadas/video'


def post(path,body):
    response=requests.post(BASE+path,json=body,timeout=20)
    response.raise_for_status()
    return response.json()


def wait(path):
    for _ in range(180):
        response=requests.get(BASE+path,timeout=20)
        response.raise_for_status()
        state=response.json()
        if state['status'] in ('done','error','cancelled'):
            assert state['status']=='done',state
            return state
        time.sleep(1)
    raise AssertionError('Timeout')


if __name__=='__main__':
    scan=post('/api/scans',{'url':PAGE})
    state=wait('/api/scans/'+scan['id'])
    video=next(v for v in state['videos'] if v['extractor']=='VTurb')
    assert len([v for v in state['videos'] if v.get('media_id')==video['media_id']])==1
    assert 'm3u8.vturb.net' in video['url']
    assert video['identity_verified'] and video['media_id']
    print('Player real do quiz e manifesto da sessao: OK',flush=True)
    for mode in ('video','audio'):
        job=post('/api/downloads',{'scan_id':scan['id'],'video_id':video['id'],'mode':mode})
        finished=wait('/api/downloads/'+job['id'])
        response=requests.get(BASE+'/api/downloads/'+job['id']+'/file',stream=True,timeout=30)
        response.raise_for_status()
        size=sum(len(chunk) for chunk in response.iter_content(65536))
        assert size>1000
        if mode=='audio': assert finished['filename'].endswith('.mp3')
        print(mode,'OK:',size,'bytes',flush=True)
