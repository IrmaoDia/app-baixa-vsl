import unittest
from hls_capture import select_playlist,absolute_playlist
from vturb import discover_vturb

CFG={'id':'visible','oid':'account','fkdlvid':'bait','video':{'id':'real'}}
REAL='#EXTM3U\n#EXTINF:4,\nhttps://cdn.converteai.net/account/real/segment.ts\n#EXT-X-ENDLIST\n'
BAIT=REAL.replace('/real/','/bait/')


class PlaybackIdentityTests(unittest.TestCase):
    def test_bait_segments_are_rejected_even_when_request_url_looks_real(self):
        self.assertIsNone(select_playlist(CFG,[{'url':'https://m3u8.vturb.net/t/account/real/video.m3u8','body':BAIT}]))

    def test_captured_body_selected_without_refetching_modified_server_response(self):
        script='{elementId:"vid-visible",config:{id:"visible",oid:"account",fkdlvid:"bait",secure:!0,video:{id:"real",drm:!1}}}'
        calls=[]
        def fetch(url,referer):
            calls.append(url)
            return script if url.endswith('.js') else BAIT
        url='https://scripts.converteai.net/account/players/visible/v4/player.js'
        captures=[{'url':'https://m3u8.vturb.net/t/account/real/video.m3u8','body':REAL,'headers':{}}]
        videos,warnings=discover_vturb(url,'https://any.example',fetch,
            visible_players=[{'player_id':'visible','media_id':'real','duration':4}],manifest_captures=captures)
        self.assertFalse(warnings)
        self.assertTrue(videos[0]['identity_verified'])
        self.assertEqual(videos[0]['captured_playlist']['body'],REAL)
        self.assertEqual(calls,[url])

    def test_hidden_player_config_not_selected(self):
        script='{elementId:"p",config:{id:"hidden",oid:"account",video:{id:"bait"}}}'
        videos,warnings=discover_vturb('https://scripts.converteai.net/account/players/hidden/v4/player.js',
            'https://any.example',lambda url,referer:script,visible_players=[{'player_id':'visible','media_id':'real'}])
        self.assertFalse(videos)

    def test_alternative_media_configuration_requires_browser_capture(self):
        script='{elementId:"p",config:{id:"visible",oid:"account",fkdlvid:"bait",video:{id:"real"}}}'
        calls=[]
        def fetch(url,referer):
            calls.append(url)
            return script
        url='https://scripts.converteai.net/account/players/visible/v4/player.js'
        videos,warnings=discover_vturb(url,'https://any.example',fetch)
        self.assertFalse(videos)
        self.assertTrue(warnings)
        self.assertEqual(calls,[url])

    def test_relative_references_resolved_and_invalid_protocols_blocked(self):
        playlist=absolute_playlist('#EXTM3U\n#EXTINF:4,\nsegment.ts\n#EXT-X-ENDLIST',
            'https://cdn.example/video/list.m3u8',lambda url:url)
        self.assertIn('https://cdn.example/video/segment.ts',playlist)
        with self.assertRaises(ValueError):
            absolute_playlist('#EXTM3U\nfile:///private/path', 'https://cdn.example/list',lambda url:url)
