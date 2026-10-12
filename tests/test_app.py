import unittest
from unittest.mock import patch
from app import parse_page, public_url, same_domain, scan_worker, scans, ScanInput


class ScannerTests(unittest.TestCase):
    def test_multiple_sources_embed_and_script(self):
        html = '''<title>Aula</title><video poster="/cover.jpg"><source src="/a.mp4"><source src="https://cdn.example/b.webm"></video><iframe src="https://player.example/123"></iframe><script>var url="https:\\/\\/cdn.example/live.m3u8?token=1"</script><a href="/next#part">Próximo</a>'''
        title, videos, links = parse_page(html, 'https://example.com/course')
        self.assertEqual(title, 'Aula')
        self.assertEqual(len(videos), 4)
        self.assertEqual(videos[0]['thumbnail'], 'https://example.com/cover.jpg')
        self.assertIn('https://example.com/next#part', links)

    def test_domain_boundary(self):
        self.assertTrue(same_domain('https://media.example.com/x', 'example.com'))
        self.assertFalse(same_domain('https://evil-example.com/x', 'example.com'))
        self.assertFalse(same_domain('https://example.com.evil.org/x', 'example.com'))

    def test_private_target_rejected(self):
        with patch('app.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                public_url('https://example.com/video')
        for url in ('file:///tmp/a', 'https://user:pass@example.com', 'http://example.com:8000'):
            with self.assertRaises(ValueError):
                public_url(url)

    def test_crawl_limit_and_duplicate_media(self):
        scans['test'] = {'status': 'running', 'videos': [], 'warnings': [], 'cancelled': False, 'limited': False}
        html = '<video src="https://cdn.example/a.mp4"></video><a href="/b">B</a><a href="https://other.example/a">Outside</a>'
        with patch('app.fetch_page', side_effect=lambda url, domain:(url, html)), patch('app.yt_dlp.YoutubeDL') as ydl, patch('app.time.sleep'):
            ydl.return_value.__enter__.return_value.extract_info.return_value = None
            scan_worker('test', ScanInput(url='https://example.com/', domain='example.com', scope='domain', max_pages=2))
        self.assertEqual(scans['test']['visited'], 2)
        self.assertEqual(len(scans['test']['videos']), 1)
        self.assertEqual(scans['test']['status'], 'done')
        del scans['test']

    def test_dynamic_player_preserves_requested_source_page(self):
        scans['dynamic'] = {'status':'running','videos':[],'warnings':[],'cancelled':False,'limited':False}
        page = 'https://new-domain.example/quiz/p2'
        player = 'https://scripts.converteai.net/account/players/media/v4/player.js'
        rendered = {'url': page, 'html': '<script src="' + player + '"></script>', 'media': [], 'warnings': []}
        info = {'url':'https://cdn.converteai.net/account/media/main.m3u8','title':'Vídeo dinâmico','extractor_key':'VTurb'}
        with patch('app.fetch_page', return_value=(page, '<html></html>')), patch('app.render_page', return_value=rendered) as browser, patch('app.discover_vturb', side_effect=[([],[]),([info],[])]), patch('app.yt_dlp.YoutubeDL') as ydl, patch('app.time.sleep'):
            ydl.return_value.__enter__.return_value.extract_info.return_value = None
            scan_worker('dynamic', ScanInput(url=page,domain='new-domain.example'))
        browser.assert_called_once()
        self.assertEqual(scans['dynamic']['videos'][0]['page'], page)
        self.assertEqual(scans['dynamic']['videos'][0]['extractor'], 'VTurb')
        del scans['dynamic']

    def test_pasted_player_code_included_for_arbitrary_domain(self):
        scans['code'] = {'status':'running','videos':[],'warnings':[],'cancelled':False,'limited':False}
        page = 'https://another.example/page'
        code = '<script src="https://scripts.converteai.net/account/players/media/v4/player.js"></script>'
        info = {'url':'https://cdn.converteai.net/account/media/main.m3u8','extractor_key':'VTurb'}
        with patch('app.fetch_page', return_value=(page, '<html></html>')), patch('app.render_page') as browser, patch('app.discover_vturb', return_value=([info],[])) as discover, patch('app.yt_dlp.YoutubeDL') as ydl, patch('app.time.sleep'):
            ydl.return_value.__enter__.return_value.extract_info.return_value = None
            scan_worker('code', ScanInput(url=page,domain='another.example',player_code=code,render_js=False))
        self.assertIn(code, discover.call_args.args[0])
        browser.assert_not_called()
        self.assertEqual(len(scans['code']['videos']), 1)
        del scans['code']


if __name__ == '__main__':
    unittest.main()
