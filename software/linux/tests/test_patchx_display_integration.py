"""Product events must reach a real mapped framebuffer and report completed writes."""
import importlib
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.patchx.config import validate_product
import test_patchx_display as fb_fixture
from test_patchx_protocol import mcp
from test_patchx_support import FakeAudio, FakeCodec, FakeTransport, wait_for

entry = importlib.import_module('apps.patchx.__main__')


class ProductDisplayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fb_fixture.DisplayTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def runtime(self, service=None):
        config = {'voice': {'mode': 'live', 'backend_url': 'wss://example.invalid',
                           'token_env': '', 'device_id': 'display-fixture', 'client_id': 'display-fixture'},
                  'patchx': {'client_ip': '192.0.2.1', 'display_enabled': True,
                             'display_device': str(self.fixture.device)}}
        runtime = entry.create_runtime(config, audio_factory=FakeAudio, codec_factory=FakeCodec,
                                       transport_factory=FakeTransport, http_address=('127.0.0.1', 0),
                                       display_service=service)
        self.addCleanup(runtime.close)
        return runtime

    def test_actual_product_expression_and_voice_state_restore_pixels(self):
        runtime = self.runtime()
        runtime.start()
        runtime.core.action('connect')
        wait_for(lambda: runtime.core.snapshot()['connected'])
        runtime.core.post('message', mcp('expression_control', expression_type=5), runtime.core.generation)
        wait_for(lambda: runtime.core.snapshot()['patchx']['expression']['rendered'])
        status = runtime.core.snapshot()['patchx']
        self.assertTrue(status['expression']['renderer_available'])
        self.assertEqual(status['display']['rendered_expression_id'], 5)
        self.assertFalse(status['display']['unity_equivalent'])
        self.assertNotEqual(self.fixture.device.read_bytes(), fb_fixture.ORIGINAL)
        runtime.core._set(state='listening', emotion='neutral')
        wait_for(lambda: runtime.core.snapshot()['patchx']['display']['rendered_state'] == 'listening')
        runtime.close()
        self.assertEqual(self.fixture.device.read_bytes(), fb_fixture.ORIGINAL)
        self.assertTrue(runtime.core.snapshot()['patchx']['display']['pixels_restored'])
        self.assertFalse(runtime.core.snapshot()['patchx']['expression']['renderer_available'])
        self.assertFalse(runtime.core.display.snapshot()['thread_alive'])

    def test_queued_expression_is_not_claimed_rendered_and_wrong_session_is_fenced(self):
        self.fixture.map_options['block_once'] = True
        service = self.fixture.service()
        runtime = self.runtime(service)
        runtime.start()
        runtime.core.action('connect')
        wait_for(lambda: runtime.core.snapshot()['connected'])
        mapping = self.fixture.mappings[0]
        self.assertTrue(mapping.entered.wait(1))
        runtime.core.post('message', mcp('expression_control', expression_type=5), runtime.core.generation)
        wait_for(lambda: runtime.core.snapshot()['patchx']['expression']['id'] == 5)
        self.assertFalse(runtime.core.snapshot()['patchx']['expression']['rendered'])
        runtime.core.post('message', dict(mcp('expression_control', expression_type=10), session_id='stale'),
                          runtime.core.generation)
        runtime.core.action('unmute')
        self.assertEqual(runtime.core.snapshot()['patchx']['expression']['id'], 5)
        mapping.release.set()
        runtime.core.post('message', mcp('expression_control', expression_type=5), runtime.core.generation)
        wait_for(lambda: runtime.core.snapshot()['patchx']['expression']['rendered'])
        runtime.close()
        self.assertEqual(self.fixture.device.read_bytes(), fb_fixture.ORIGINAL)

    def test_display_startup_failure_still_closes_http_and_all_owners(self):
        self.fixture.stat_character = False
        service = self.fixture.service()
        runtime = self.runtime(service)
        with self.assertRaises(RuntimeError):
            runtime.start()
        runtime.close()
        self.assertFalse(service.snapshot()['thread_alive'])
        self.assertTrue(service.snapshot()['resources_closed'])
        self.assertEqual(self.fixture.device.read_bytes(), fb_fixture.ORIGINAL)
        self.assertEqual(runtime.server.fileno(), -1)

    def test_display_close_failure_is_visible_after_http_and_core_stop(self):
        service = self.fixture.service()
        runtime = self.runtime(service)
        runtime.start()
        with mock.patch.object(service, 'close', return_value=False):
            with self.assertRaises(RuntimeError):
                runtime.close()
        self.assertFalse(runtime.core.thread.is_alive())
        self.assertFalse(runtime.worker.is_alive())
        self.assertEqual(runtime.server.fileno(), -1)
        self.assertTrue(service.close())
        self.assertEqual(self.fixture.device.read_bytes(), fb_fixture.ORIGINAL)

    def test_physical_display_stays_explicit_and_requires_live_mode(self):
        self.assertFalse(validate_product({})['patchx']['display_enabled'])
        for settings in ({'display_enabled': True, 'display_device': '/dev/fb0'},
                         {'display_enabled': 1}, {'display_device': 'fb0'}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_product({'patchx': settings})

    def test_mqtt_close_exception_still_stops_core_and_restores_actual_framebuffer(self):
        service = self.fixture.service()
        runtime = self.runtime(service)
        runtime.start()
        wait_for(lambda: service.snapshot()['frames_written'] > 0)
        class BrokenMqtt:
            def close(self):
                raise OSError('fixture MQTT cleanup failure')
        runtime.core.mqtt = BrokenMqtt()
        with self.assertRaises(RuntimeError):
            runtime.close()
        self.assertTrue(runtime.core.stopping.is_set())
        self.assertFalse(runtime.core.thread.is_alive())
        self.assertTrue(service.snapshot()['pixels_restored'])
        self.assertFalse(service.snapshot()['thread_alive'])
        self.assertEqual(self.fixture.device.read_bytes(), fb_fixture.ORIGINAL)
        # Release the deliberately failing fixture before the registered cleanup.
        runtime.core.mqtt = None
        runtime.close()


if __name__ == '__main__':
    unittest.main()
