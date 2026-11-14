"""Local maintenance entry for the GD32F503 motor board."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import struct
import time
import zlib

from .motor import MotorController, _Serial

MAX_IMAGE = 0x20000
CHUNK_SIZE = 256
SETUP_TIMEOUT = 15.
BLOCK_TIMEOUT = 2.
RESET_TIMEOUT = 20.
QUERY_TIMEOUT = .4


def _validate_image(image):
    if not 8 <= len(image) <= MAX_IMAGE:
        raise ValueError("firmware size must be 8..131072 bytes")
    msp, reset = struct.unpack_from("<II", image)
    if not (0x20000000 < msp <= 0x20010000 and msp % 8 == 0 and reset & 1 and
            0x08004000 <= (reset & ~1) < 0x08004000 + len(image)):
        raise ValueError("firmware vectors are invalid for GD32F503 APP at 0x08004000")


def _request(wire, command, data, timeout, prefix=b""):
    wire.write(command, data)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = wire.receive(deadline)
        if frame is None:
            break
        reply, body = frame
        if reply == 0xE0:
            try:
                MotorController._decode_fault(body)
            except ValueError:
                continue
            wire.write(0xE1, body[:2])
        elif command in (0xDE, 0xDF) and frame == (0xEE, b""):
            raise ValueError(f"board rejected OTA command 0x{command:02X}")
        elif reply == command and body.startswith(prefix):
            return body
    raise TimeoutError(f"board did not acknowledge 0x{command:02X}")


def _version(wire):
    body = _request(wire, 0xCC, b"", QUERY_TIMEOUT)
    if not 1 <= len(body) <= 64 or not all(32 <= byte < 127 for byte in body):
        raise ValueError("invalid application version")
    return body.decode("ascii")


def _sequence(wire, token):
    body = _request(wire, 0x6B, token, QUERY_TIMEOUT, prefix=token)
    if len(body) != 6 or body[2] > 7 or body[3] != 1:
        raise ValueError("unsupported motor protocol")
    return struct.unpack_from(">H", body, 4)[0]


def _wait_restart(wire, token):
    deadline = time.monotonic() + RESET_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if _sequence(wire, token) == 0:
                return _version(wire)
        except TimeoutError:
            pass
        time.sleep(.2)
    raise TimeoutError("application did not return with a reset sequence")


def _update_faults(wire, token):
    body = _request(wire, 0x64, token, QUERY_TIMEOUT, prefix=token)
    if len(body) < 4 or body[2] or body[3] > 8 or len(body) != 4 + 10 * body[3]:
        raise ValueError("invalid MCU fault snapshot")
    faults = [MotorController._decode_fault(body[i:i + 10]) for i in range(4, len(body), 10)]
    return [event["code"] for event in faults if event["source_id"] == 0xFE and event["active"]]


def upgrade(port, image):
    """Upload through the same exclusive serial transport as normal motor control."""
    _validate_image(image)
    count = (len(image) + CHUNK_SIZE - 1) // CHUNK_SIZE
    crc = zlib.crc32(image)
    result = {"bytes": len(image), "crc32": f"{crc:08X}",
              "sha256": hashlib.sha256(image).hexdigest(), "packets": count,
              "acked_packets": 0, "transfer_acknowledged": False, "app_restarted": False,
              "image_confirmation": "requires_readback"}
    with closing(_Serial(port)) as wire:
        result["version_before"] = _version(wire)
        seq = _sequence(wire, b"\x00\x01") % 65535 + 1
        prefix = struct.pack(">H", seq)
        if _request(wire, 0x63, prefix, QUERY_TIMEOUT, prefix=prefix) != prefix + b"\0":
            raise ValueError("motor board did not accept stop")
        # A nonzero sequence recorded before transfer proves subsequent reset to zero.
        if _sequence(wire, b"\x00\x02") != seq:
            raise ValueError("motor stop sequence was not recorded")
        if _request(wire, 0xDE, struct.pack(">II", len(image), crc), SETUP_TIMEOUT) != b"":
            raise ValueError("invalid OTA start ACK")
        for index, offset in enumerate(range(0, len(image), CHUNK_SIZE), 1):
            block = image[offset:offset + CHUNK_SIZE]
            started = time.monotonic()
            try:
                ack = _request(wire, 0xDF, struct.pack(">HH", index, len(block)) + block,
                               SETUP_TIMEOUT if index == count else BLOCK_TIMEOUT)
            except TimeoutError:
                if index != count:
                    raise
                # An empty ACK carries no packet number. Do not retransmit and risk
                # mistaking a late duplicate for the next packet's acknowledgement.
                result["final_ack_missing"] = True
                break
            if ack:
                raise ValueError("invalid OTA data ACK")
            result["acked_packets"] = index
            if index == count:
                result["final_ack_seconds"] = round(time.monotonic() - started, 3)
        result["transfer_acknowledged"] = result["acked_packets"] == count
        try:
            result["version_after"] = _wait_restart(wire, b"\x00\x03")
            result["app_restarted"] = True
            result["update_faults"] = _update_faults(wire, b"\x00\x04")
        except (OSError, ValueError) as error:
            result["verification_error"] = str(error)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Upload a local GD32F503 APP image over the motor UART")
    parser.add_argument("--port", required=True, help="motor UART, for example /dev/ttyS0")
    parser.add_argument("image", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.image.open("rb") as source:
            image = source.read(MAX_IMAGE + 1)
        result = upgrade(args.port, image)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(result))
    return int(not (result["transfer_acknowledged"] and result["app_restarted"] and
                    result.get("update_faults") == []))


if __name__ == "__main__":
    raise SystemExit(main())
