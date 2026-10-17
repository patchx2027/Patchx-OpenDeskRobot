"""The product must share one logical volume port with its real audio leaf."""
import importlib
from pathlib import Path
import struct
import io
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.patchx.audio import PatchXAudioIO, SoftwareVolume
from apps.patchx.config import validate_product
from test_patchx_protocol import mcp
from test_patchx_support import FakeCodec, FakeTransport, wait_for

entry = importlib.import_module('apps.patchx.__main__')


class Route:
    def acquire(self):
        raise AssertionError('configuration and handshake must not open the speaker')
    def release(self):
        return True


class ProductAudioTests(unittest.TestCase):
    def config(self, **settings):
        return {'voice': {'mode': 'live', 'backend_url': 'wss://example.invalid', 'token_env': '',
                          'device_id': 'audio-fixture', 'client_id': 'audio-fixture'},
                'patchx': dict({'client_ip': '192.0.2.1', 'audio_enabled': True,
                               'volume_initial': 8}, **settings)}

    def test_handshake_constructs_real_leaf_without_opening_and_shares_business_volume(self):
        with mock.patch('apps.patchx.audio.RK809SpeakerRoute', return_value=Route()):
            runtime = entry.create_runtime(self.config(), codec_factory=FakeCodec,
                                           transport_factory=FakeTransport, http_address=('127.0.0.1', 0))
        self.addCleanup(runtime.close)
        runtime.start()
        runtime.core.action('connect')
        wait_for(lambda: runtime.core.snapshot()['connected'])
        audio = runtime.core.audio
        self.assertIsInstance(audio, PatchXAudioIO)
        self.assertIs(audio.volume, runtime.business.volume)
        self.assertFalse(audio.snapshot()['capture_active'])
        self.assertFalse(audio.snapshot()['speaker_owned'])
        self.assertEqual(audio.output_sample_rate, 24000)
        runtime.core.post('message', mcp('set_volume', volume=-100), runtime.core.generation)
        wait_for(lambda: runtime.business.snapshot()['volume'].get('raw') == 0)
        pcm = struct.pack('<3h', -32768, 12345, 32767)
        self.assertEqual(audio.volume.apply(pcm), bytes(6))
        self.assertTrue(runtime.business.absolute_setting('volume', 15)['ok'])
        self.assertEqual(audio.volume.apply(pcm), pcm)
        runtime.close()
        self.assertTrue(audio._stop.is_set())
        self.assertFalse(audio.snapshot()['speaker_owned'])

    def test_disabled_default_does_not_create_board_audio_or_volume(self):
        with mock.patch('apps.patchx.audio.RK809SpeakerRoute') as route:
            runtime = entry.create_runtime(validate_product({}), http_address=('127.0.0.1', 0))
            try:
                self.assertIsNone(runtime.business.volume)
                route.assert_not_called()
            finally:
                runtime.close()

    def test_invalid_board_audio_configuration_rejects_before_runtime(self):
        invalid = [self.config(audio_enabled=1), self.config(volume_initial=True),
                   self.config(volume_initial=16), {'patchx': {'audio_enabled': True}},
                   dict(self.config(), voice=dict(self.config()['voice'], aec_enabled=True))]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                entry.create_runtime(config, http_address=('127.0.0.1', 0))

    def test_explicit_volume_port_is_shared_with_the_constructed_audio(self):
        volume = SoftwareVolume(raw=3)
        runtime = entry.create_runtime(self.config(), codec_factory=FakeCodec,
                                       transport_factory=FakeTransport, http_address=('127.0.0.1', 0),
                                       audio_route=Route(), volume_port=volume)
        self.addCleanup(runtime.close)
        runtime.start()
        runtime.core.action('connect')
        wait_for(lambda: runtime.core.snapshot()['connected'])
        self.assertIs(runtime.business.volume, volume)
        self.assertIs(runtime.core.audio.volume, volume)
        self.assertEqual(runtime.core.snapshot()['patchx']['volume']['raw'], 3)

    def test_unreaped_playback_retains_owner_blocks_reconnect_and_fails_shutdown(self):
        route = mock.Mock()
        runtime = entry.create_runtime(self.config(), codec_factory=FakeCodec,
                                       transport_factory=FakeTransport, http_address=('127.0.0.1', 0),
                                       audio_route=route)
        runtime.start()
        runtime.core.action('connect')
        wait_for(lambda: runtime.core.snapshot()['connected'])
        audio = runtime.core.audio
        child = mock.Mock(stdin=io.BytesIO(), stdout=None)
        child.poll.return_value = None
        child.wait.side_effect = subprocess.TimeoutExpired('synthetic-unreaped-aplay', 1)
        audio._playback = child
        audio._speaker_owned = True
        runtime.core.action('disconnect')
        self.assertIs(runtime.core.audio, audio)
        self.assertIs(audio._playback, child)
        self.assertTrue(runtime.core.snapshot()['patchx']['audio_cleanup_uncertain'])
        self.assertTrue(audio.snapshot()['speaker_owned'])
        route.release.assert_not_called()
        with self.assertRaises(ValueError):
            runtime.core.action('connect')
        self.assertIs(runtime.core.audio, audio)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'audio'):
                runtime.close()
            self.assertFalse(runtime.closed)
            self.assertFalse(runtime.worker.is_alive())
            self.assertFalse(runtime.core.thread.is_alive())
        route.release.assert_not_called()
        self.assertFalse(child.stdin.closed)

    def test_false_cleanup_result_is_not_hidden_by_base_release(self):
        runtime = entry.create_runtime(self.config(), codec_factory=FakeCodec,
                                       transport_factory=FakeTransport, http_address=('127.0.0.1', 0),
                                       audio_route=Route())
        runtime.start()
        runtime.core.action('connect')
        wait_for(lambda: runtime.core.snapshot()['connected'])
        audio = runtime.core.audio
        audio.cleanup_uncertain = True
        with self.assertRaisesRegex(RuntimeError, 'audio'):
            runtime.close()
        self.assertIs(runtime.core.audio, audio)
        self.assertTrue(runtime.core.snapshot()['patchx']['audio_cleanup_uncertain'])
        self.assertFalse(runtime.worker.is_alive())


if __name__ == '__main__':
    unittest.main()
