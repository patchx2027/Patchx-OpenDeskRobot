"""Run shared fixtures against checkouts of the Android and Linux clients."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
from unittest.mock import patch


def expect(actual, expected):
    if actual != expected:
        raise AssertionError(f"Expected {expected!r}, got {actual!r}")


def check_android(root, uart, voice):
    source = root / "software/android/app/src/main/java/com/patchx_main/patchx"
    sources = {
        # Platform dependencies of the two production classes. No UART is opened.
        "android/util/Log.java": """
            package android.util;
            public class Log {
                public static int i(String tag, String value) { return 0; }
                public static int e(String tag, String value) { return 0; }
                public static int e(String tag, String value, Throwable error) { return 0; }
            }
        """,
        "android/media/AudioFormat.java": """
            package android.media;
            public class AudioFormat {
                public static final int CHANNEL_IN_MONO = 16, CHANNEL_OUT_MONO = 4,
                    ENCODING_PCM_16BIT = 2;
            }
        """,
        "com/wcy/testserial/SerialPortUtil.java": """
            package com.wcy.testserial;
            public class SerialPortUtil {
                public void write(byte[] value) { throw new AssertionError("Unexpected UART write"); }
            }
        """,
        "com/patchx_main/BuildConfig.java": """
            package com.patchx_main;
            public class BuildConfig { public static final String PATCHX_CLIENT_ID = "fixture"; }
        """,
        "Probe.java": """
            import com.patchx_main.patchx.util.ComProtocol;
            import com.patchx_main.patchx.voice.AudioConfig;
            import java.util.Locale;

            public class Probe {
                public static void main(String[] args) {
                    System.out.println(AudioConfig.SAMPLE_RATE + " " + AudioConfig.FRAME_DURATION +
                        " " + AudioConfig.FRAME_SIZE);
                    for (int i = 0; i < args.length; i += 2) {
                        String input = args[i + 1];
                        byte[] payload = new byte[input.length() / 2];
                        for (int index = 0; index < payload.length; index++) {
                            int offset = 2 * index;
                            payload[index] = (byte) Integer.parseInt(input.substring(offset, offset + 2), 16);
                        }
                        int command = Integer.parseInt(args[i]);
                        byte[] frame = payload.length == 0 ? ComProtocol.buildSimpleFrame(command) :
                            ComProtocol.buildFrame(command, payload, payload.length);
                        System.out.println(ComProtocol.bytesToHex(frame).replace(" ", "").toLowerCase(Locale.ROOT));
                    }
                }
            }
        """,
    }
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        inputs = []
        for name, content in sources.items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            inputs.append(str(path))
        subprocess.run(["javac", "-d", directory, *inputs,
                        str(source / "util/ComProtocol.java"), str(source / "voice/AudioConfig.java")],
                       check=True)
        args = [value for row in uart["frames"] for value in (str(row["command"]), row["payload_hex"])]
        lines = subprocess.check_output(["java", "-cp", directory, "Probe", *args], text=True).splitlines()
    params = voice["client_hello"]["audio_params"]
    rate, duration = params["sample_rate"], params["frame_duration"]
    expect(lines, [f"{rate} {duration} {rate * duration // 1000}",
                   *(case["frame_hex"] for case in uart["frames"])])


def check_linux(root, uart, voice):
    software = root / "software/linux"
    sys.path[:0] = [str(software), str(software / "tests")]
    from apps.companion.audio import OpusCodec
    from apps.companion.core import AUDIO_PARAMS, validate_hello
    from apps.patchx.motor import FrameReader, encode_frame
    from apps.patchx.transport import PatchXCloudTransport
    from test_companion_transport import LoopbackServer

    for case in uart["frames"]:
        command = case["command"]
        payload = bytes.fromhex(case["payload_hex"])
        frame = bytes.fromhex(case["frame_hex"])
        expect(encode_frame(command, payload), frame)
        reader = FrameReader()
        expect(list(reader.feed(frame[:4])), [])
        expect(list(reader.feed(frame[4:])), [(command, payload)])
        damaged = bytearray(frame)
        damaged[-3] ^= 1
        expect(list(FrameReader().feed(damaged)), [])

    hello = voice["client_hello"]
    expect(AUDIO_PARAMS, hello["audio_params"])
    downlink_rate, session = validate_hello(voice["server_hello"])
    expect(session, voice["listen_start"]["session_id"])
    packet = bytes.fromhex(voice["opus_packet_hex"])
    codec = OpusCodec(decoder_sample_rate=downlink_rate)
    try:
        expect(len(codec.decode(packet)), downlink_rate * 60 // 1000 * 2)
    finally:
        codec.close()

    received = queue.Queue()

    def peer(connection, server):
        def send_json(value):
            server.send(connection, 1, json.dumps(value).encode())

        def expect_json(value):
            opcode, data = server.frame(connection)
            expect(opcode, 1)
            expect(json.loads(data), value)

        request = dict(line.split(": ", 1) for line in server.request.splitlines() if ": " in line)
        for name, value in voice["headers"].items():
            expect(request.get(name), value)
        expect_json(hello)
        send_json(voice["server_hello"])
        expect_json(voice["listen_start"])
        expect(server.frame(connection), (2, packet))
        send_json(voice["tts_start"])
        server.send(connection, 2, packet)
        send_json(voice["tts_stop"])

    server = LoopbackServer(peer)
    client = PatchXCloudTransport(
        {"backend_url": server.url, "allow_insecure_ws": True, "network_timeout_s": 2,
         "device_id": hello["device_id"], "client_id": voice["headers"]["Client-Id"],
         "websocket_heartbeat": False},
        received.put, received.put, hello["client_ip"], trace_factory=lambda: hello["trace_id"])
    try:
        with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            client.connect()
            client.send({key: value for key, value in hello.items() if key not in ("client_ip", "trace_id")})
            expect(received.get(timeout=4), voice["server_hello"])
            client.send(voice["listen_start"])
            client.send(packet)
            expect([received.get(timeout=4) for _ in range(3)],
                   [voice["tts_start"], packet, voice["tts_stop"]])
    finally:
        client.close()
        if not server.done.wait(4):
            raise RuntimeError("Loopback fixture did not finish")
        if server.failure:
            raise server.failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("android", type=Path)
    parser.add_argument("linux", type=Path)
    args = parser.parse_args()
    fixture = json.loads(Path(__file__).with_name("fixtures.json").read_text(encoding="utf-8"))
    expect(fixture["contract_version"], 1)
    check_android(args.android.resolve(), fixture["uart"], fixture["voice"])
    check_linux(args.linux.resolve(), fixture["uart"], fixture["voice"])
    print("PASS: Android UART framing and audio parameters; Linux UART framing and WebSocket/Opus")


if __name__ == "__main__":
    main()
