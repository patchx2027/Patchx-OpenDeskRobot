"""Executable Linux PatchX product; starts offline and microphone-muted."""
import argparse
import signal
import threading

from apps.companion.audio import AudioIO, OpusCodec
from apps.companion.server import Server
from .business import PatchXBusiness
from .config import load_product, validate_product
from .devices import LinuxBacklight
from .product import PatchXCompanion
from .transport import PatchXCloudTransport


class Runtime:
    def __init__(self, config, core, business, http_address=None):
        self.config, self.core, self.business = config, core, business
        voice = config["voice"]
        address = http_address if http_address is not None else (voice["bind"], voice["port"])
        if http_address is not None:
            if (not isinstance(address, tuple) or len(address) != 2 or
                    address[0] not in ("localhost", "127.0.0.1") or
                    type(address[1]) is not int or not 0 <= address[1] <= 65535):
                raise ValueError("embedded HTTP address must be loopback with a valid port")
        self.server = Server(address, core)
        self.worker = None
        self.closed = False

    def start(self):
        if self.closed or self.worker is not None:
            raise ValueError("product runtime cannot be started twice")
        try:
            self.core.start()
            self.worker = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05),
                                           name="patchx-http", daemon=True)
            self.worker.start()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        try:
            if self.worker is not None and self.worker.is_alive():
                self.server.shutdown()
        finally:
            try:
                self.server.server_close()
            finally:
                try:
                    self.core.close()
                finally:
                    if self.worker is not None:
                        self.worker.join(timeout=2)
                        if self.worker.is_alive():
                            raise RuntimeError("PatchX HTTP shutdown timed out")
        self.closed = True


def create_runtime(config, audio_factory=None, codec_factory=None, transport_factory=None,
                   http_address=None, camera_backend=None, display_service=None,
                   audio_route=None, volume_port=None):
    """Assemble once; embedded callers may request an ephemeral loopback HTTP port."""
    config = validate_product(config)
    voice, settings = config["voice"], config["patchx"]
    backlight = LinuxBacklight(settings["backlight_path"]) if settings["backlight_enabled"] else None
    if settings["audio_enabled"]:
        from .audio import PatchXAudioIO, RK809SpeakerRoute, SoftwareVolume
        if volume_port is None:
            volume_port = SoftwareVolume(settings["volume_initial"])
        if audio_route is None:
            audio_route = RK809SpeakerRoute()
        if audio_factory is None:
            def audio_factory(config, on_error=None):
                return PatchXAudioIO(config, on_error, speaker_route=audio_route, volume=volume_port)
    business = PatchXBusiness(backlight=backlight, volume=volume_port)
    if display_service is None and settings["display_enabled"]:
        from .display import DisplayService
        display_service = DisplayService(settings["display_device"])
    if camera_backend is None and settings["camera_enabled"]:
        from .camera import CommandCameraBackend
        camera_backend = CommandCameraBackend(settings["camera_program"], settings["camera_work_dir"],
                                              termination_grace_s=6.0)
    if transport_factory is None:
        def transport_factory(config, on_message, on_error):
            return PatchXCloudTransport(config, on_message, on_error, settings["client_ip"])
    # All three must be explicit: Companion.connect replaces the group if audio is None.
    core = PatchXCompanion(voice, business, audio_factory=audio_factory or AudioIO,
                          codec_factory=codec_factory or OpusCodec, transport_factory=transport_factory,
                          camera_backend=camera_backend, camera_timeout_s=settings["camera_timeout_s"],
                          display_service=display_service)
    if settings["mqtt_enabled"]:
        core.configure_mqtt(broker=settings["mqtt_broker"], port=settings["mqtt_port"], tls=settings["mqtt_tls"],
            device_id=voice["device_id"], env_names={"username": settings["mqtt_username_env"],
            "password": settings["mqtt_password_env"], "client_id": settings["mqtt_client_id_env"]})
    return Runtime(config, core, business, http_address=http_address)


def main(argv=None):
    parser = argparse.ArgumentParser(description="PatchX Linux business and voice service")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    runtime = None
    try:
        runtime = create_runtime(load_product(args.config))
        stopped = threading.Event()
        def stop(*_):
            stopped.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        runtime.start()
        print("PatchX: http://%s:%s/ mode=%s" % (*runtime.server.server_address, runtime.config["voice"]["mode"]), flush=True)
        stopped.wait()
        return 0
    except (ValueError, OSError, ImportError, RuntimeError):
        print("PatchX startup or shutdown failed; check configuration, port and dependencies", flush=True)
        return 1
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
