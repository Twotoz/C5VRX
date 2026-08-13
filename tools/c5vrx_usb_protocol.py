#!/usr/bin/env python3
"""Versioned, CRC-protected C5VRX USB preview stream framing."""

from __future__ import annotations

import argparse
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"\x00C5VRX\xA5\x5A"
VERSION = 1
HEADER = struct.Struct("<8sBBHIIQI")
HEADER_BYTES = HEADER.size
PAYLOAD_CRC = struct.Struct("<I")
FRAME_DESCRIPTOR = struct.Struct("<HHHBB")
MAX_PAYLOAD_BYTES = 1024 * 1024

PACKET_STREAM_INFO = 1
PACKET_GRAY8_FRAME = 2
PACKET_STREAM_END = 3
PIXEL_FORMAT_GRAY8 = 1


@dataclass(frozen=True)
class Packet:
    packet_type: int
    sequence: int
    timestamp_us: int
    payload: bytes


def encode_packet(packet_type: int, sequence: int, timestamp_us: int,
                  payload: bytes) -> bytes:
    """Reference encoder used by host tests and protocol tooling."""
    prefix = HEADER.pack(
        MAGIC, VERSION, packet_type, HEADER_BYTES, sequence,
        len(payload), timestamp_us, 0,
    )
    header_crc = zlib.crc32(prefix[:28]) & 0xFFFFFFFF
    header = prefix[:28] + struct.pack("<I", header_crc)
    return header + payload + PAYLOAD_CRC.pack(zlib.crc32(payload) & 0xFFFFFFFF)


class StreamDecoder:
    """Incrementally separates ASCII console lines and binary packets.

    ``feed`` returns ordered ``("line", str)``, ``("packet", Packet)`` and
    ``("error", reason)`` events. A bad header or payload advances only far
    enough to search for the next magic marker, so a damaged or truncated
    packet cannot permanently desynchronise the Receiver Console.
    """

    def __init__(self) -> None:
        self._wire = bytearray()
        self._text = bytearray()

    @staticmethod
    def _magic_prefix_suffix(data: bytearray) -> int:
        for count in range(min(len(data), len(MAGIC) - 1), 0, -1):
            if data[-count:] == MAGIC[:count]:
                return count
        return 0

    def _consume_text(self, data: bytes, events: list[tuple[str, object]]) -> None:
        self._text.extend(data)
        while True:
            newline = self._text.find(b"\n")
            if newline < 0:
                break
            raw = bytes(self._text[:newline]).rstrip(b"\r")
            del self._text[:newline + 1]
            events.append(("line", raw.decode("utf-8", errors="replace")))
        if len(self._text) > 65536:
            self._text.clear()
            events.append(("error", "ASCII_BUFFER_LIMIT"))

    def feed(self, data: bytes) -> list[tuple[str, object]]:
        events: list[tuple[str, object]] = []
        self._wire.extend(data)
        while self._wire:
            marker = self._wire.find(MAGIC)
            if marker < 0:
                keep = self._magic_prefix_suffix(self._wire)
                text_bytes = len(self._wire) - keep
                if text_bytes:
                    self._consume_text(bytes(self._wire[:text_bytes]), events)
                    del self._wire[:text_bytes]
                break
            if marker:
                self._consume_text(bytes(self._wire[:marker]), events)
                del self._wire[:marker]
            if len(self._wire) < HEADER_BYTES:
                break

            header = bytes(self._wire[:HEADER_BYTES])
            (magic, version, packet_type, header_bytes, sequence,
             payload_bytes, timestamp_us, expected_header_crc) = HEADER.unpack(header)
            actual_header_crc = zlib.crc32(header[:28]) & 0xFFFFFFFF
            if (magic != MAGIC or version != VERSION or
                    header_bytes != HEADER_BYTES or
                    payload_bytes > MAX_PAYLOAD_BYTES or
                    actual_header_crc != expected_header_crc):
                del self._wire[0]
                events.append(("error", "INVALID_HEADER"))
                continue

            wire_bytes = HEADER_BYTES + payload_bytes + PAYLOAD_CRC.size
            if len(self._wire) < wire_bytes:
                break
            payload = bytes(self._wire[HEADER_BYTES:HEADER_BYTES + payload_bytes])
            expected_payload_crc, = PAYLOAD_CRC.unpack_from(
                self._wire, HEADER_BYTES + payload_bytes)
            if zlib.crc32(payload) & 0xFFFFFFFF != expected_payload_crc:
                nested_marker = self._wire.find(MAGIC, 1, wire_bytes)
                if nested_marker >= 0:
                    del self._wire[:nested_marker]
                else:
                    del self._wire[:wire_bytes]
                events.append(("error", "PAYLOAD_CRC"))
                continue
            del self._wire[:wire_bytes]
            events.append(("packet", Packet(
                packet_type=packet_type,
                sequence=sequence,
                timestamp_us=timestamp_us,
                payload=payload,
            )))
        return events


def self_test() -> None:
    descriptor = FRAME_DESCRIPTOR.pack(160, 120, 160, PIXEL_FORMAT_GRAY8, 1)
    frame = bytes((n * 17) & 0xFF for n in range(160 * 120))
    info = encode_packet(PACKET_STREAM_INFO, 3, 1000, descriptor)
    video = encode_packet(PACKET_GRAY8_FRAME, 4, 2000, descriptor + frame)
    damaged = bytearray(video)
    damaged[-1] ^= 0x80
    end = encode_packet(PACKET_STREAM_END, 5, 3000, b"")
    wire = b"boot\r\n" + info + bytes(damaged) + b"noise\n" + video + end

    decoder = StreamDecoder()
    events: list[tuple[str, object]] = []
    for offset in range(0, len(wire), 37):
        events.extend(decoder.feed(wire[offset:offset + 37]))
    lines = [value for kind, value in events if kind == "line"]
    packets = [value for kind, value in events if kind == "packet"]
    errors = [value for kind, value in events if kind == "error"]
    assert "boot" in lines
    assert "noise" in lines
    assert "PAYLOAD_CRC" in errors
    assert [packet.packet_type for packet in packets] == [
        PACKET_STREAM_INFO, PACKET_GRAY8_FRAME, PACKET_STREAM_END,
    ]
    assert packets[1].payload[FRAME_DESCRIPTOR.size:] == frame
    print("c5vrx_usb_protocol: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
