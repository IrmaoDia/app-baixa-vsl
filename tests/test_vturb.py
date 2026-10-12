import unittest
from vturb import discover_vturb, player_scripts, video_configs

SCRIPT_URL = 'https://scripts.converteai.net/account/players/player-id/v4/player.js'
CONFIG = '''{id:"player-id",name:"Aula de exemplo.mp4",oid:"account",secure:!1,
video:{id:"media-id",cover:"https://images.converteai.net/cover.jpg",drm:!1}}'''


class VTurbTests(unittest.TestCase):
    def test_inline_and_preload_script_deduplicated(self):
        html = f'<vturb-smartplayer id="vid-player-id"></vturb-smartplayer><script type="litespeed/javascript">s.src="{SCRIPT_URL}"</script><link href="{SCRIPT_URL}" rel="preload">'
        self.assertEqual(player_scripts(html), [SCRIPT_URL])

    def test_protocol_relative_and_escaped_json(self):
        for text in (SCRIPT_URL.replace('https:', ''), SCRIPT_URL.replace('/', '\\/'), SCRIPT_URL.replace('/', '\\u002f')):
            self.assertEqual(player_scripts(text), [SCRIPT_URL])

    def test_provider_detection_independent_of_page_domain(self):
        script = '{elementId:"p",config:' + CONFIG + '}'
        for page in ('https://first.example/video', 'https://second.example/quiz/p2', 'https://arbitrary.example/lesson'):
            videos, warnings = discover_vturb(SCRIPT_URL, page,
                lambda url, referer: script if url.endswith('player.js') else '#EXTM3U\n')
            self.assertFalse(warnings)
            self.assertEqual(videos[0]['url'], 'https://cdn.converteai.net/account/media-id/main.m3u8')

    def test_normal_player_uses_media_id_not_player_id(self):
        script = 'var e={elementId:"vid-player-id",config:' + CONFIG + '};e.run()'
        calls = []
        def fetch(url, referer):
            calls.append(url)
            return script if url.endswith('player.js') else '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo.m3u8'
        videos, warnings = discover_vturb(f'<script src="{SCRIPT_URL}"></script>', 'https://example.com', fetch)
        self.assertFalse(warnings)
        self.assertEqual(videos[0]['url'], 'https://cdn.converteai.net/account/media-id/main.m3u8')
        self.assertEqual(videos[0]['title'], 'Aula de exemplo.mp4')
        self.assertEqual(len(calls), 2)

    def test_ab_test_includes_nested_variants(self):
        script = 'var e={elementId:"ab-test",config:{children:[{config:' + CONFIG + '},{config:' + CONFIG.replace('media-id','second-media') + '}]}}'
        self.assertEqual(len(video_configs(script)), 2)

    def test_player_with_multiline_markup(self):
        config = CONFIG.replace('name:"Aula de exemplo.mp4"', 'markup:"<div>\nTexto</div>",name:"Aula de exemplo.mp4"')
        self.assertEqual(video_configs('{elementId:"p",config:' + config + '}')[0]['video']['id'], 'media-id')

    def test_protected_player_not_requested(self):
        script = 'var e={elementId:"p",config:' + CONFIG.replace('drm:!1','drm:!0') + '}'
        calls = []
        def fetch(url, referer):
            calls.append(url)
            return script
        videos, warnings = discover_vturb(f'<script src="{SCRIPT_URL}"></script>', 'https://example.com', fetch)
        self.assertFalse(videos)
        self.assertTrue(warnings)
        self.assertEqual(len(calls), 1)

    def test_invalid_manifest_not_listed(self):
        script = 'var e={elementId:"p",config:' + CONFIG + '}'
        videos, warnings = discover_vturb(f'<script src="{SCRIPT_URL}"></script>', 'https://example.com',
            lambda url, referer: script if url.endswith('player.js') else '<html>Not found</html>')
        self.assertFalse(videos)
        self.assertTrue(warnings)

    def test_secure_delivery_is_not_drm_and_uses_observed_url(self):
        script = 'var e={elementId:"p",config:' + CONFIG.replace('secure:!1','secure:!0') + '}'
        captured = 'https://m3u8.vturb.net/t-session/account/media-id/main.m3u8'
        calls = []
        def fetch(url, referer):
            calls.append(url)
            return script if url.endswith('player.js') else '#EXTM3U\n'
        videos, warnings = discover_vturb(SCRIPT_URL, 'https://quiz.example/video', fetch,
                                         observed_media=[captured])
        self.assertFalse(warnings)
        self.assertEqual(videos[0]['url'], captured)
        self.assertEqual(calls[-1], captured)

    def test_secure_delivery_waits_for_actual_session_manifest(self):
        script = 'var e={elementId:"p",config:' + CONFIG.replace('secure:!1','secure:!0') + '}'
        calls = []
        def fetch(url, referer):
            calls.append(url)
            return script
        videos, warnings = discover_vturb(SCRIPT_URL, 'https://quiz.example/video', fetch,
            observed_media=['https://m3u8.vturb.net/t-other/account/wrong-media/main.m3u8'])
        self.assertFalse(videos)
        self.assertIn('sessão', warnings[0])
        self.assertEqual(calls, [SCRIPT_URL])
