#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Versioned, CRC-protected C5VRX USB stream framing."""

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
IQ_DESCRIPTOR = struct.Struct("<II")  # word_count, flags
IQ_CHUNK_DESCRIPTOR = struct.Struct("<IIIII")
MAX_PAYLOAD_BYTES = 1024 * 1024

PACKET_STREAM_INFO = 1
PACKET_GRAY8_FRAME = 2
PACKET_STREAM_END = 3
PACKET_IQ_U32_BLOCK = 4
PACKET_IQ_U32_CHUNK = 5
PIXEL_FORMAT_GRAY8 = 1
IQ_FLAG_NONE = 0


@dataclass(frozen=True)
class IQChunk:
    capture_id: int
    total_words: int
    offset_words: int
    flags: int
    words: tuple[int, ...]


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


def decode_iq_block(packet: Packet) -> list[int]:
    """Decode one PACKET_IQ_U32_BLOCK payload into packed C5 IQ words."""
    if packet.packet_type != PACKET_IQ_U32_BLOCK:
        raise ValueError("not an IQ block packet")
    if len(packet.payload) < IQ_DESCRIPTOR.size:
        raise ValueError("short IQ descriptor")
    word_count, flags = IQ_DESCRIPTOR.unpack_from(packet.payload)
    if flags != IQ_FLAG_NONE:
        raise ValueError(f"unsupported IQ flags: {flags}")
    raw = packet.payload[IQ_DESCRIPTOR.size:]
    if len(raw) != word_count * 4:
        raise ValueError(
            f"IQ payload size mismatch: words={word_count} bytes={len(raw)}"
        )
    if not word_count:
        return []
    return list(struct.unpack(f"<{word_count}I", raw))


def decode_iq_chunk(packet: Packet) -> IQChunk:
    """Decode one independently CRC-protected fragment of an IQ capture."""
    if packet.packet_type != PACKET_IQ_U32_CHUNK:
        raise ValueError("not an IQ chunk packet")
    if len(packet.payload) < IQ_CHUNK_DESCRIPTOR.size:
        raise ValueError("short IQ chunk descriptor")
    (capture_id, total_words, offset_words,
     chunk_words, flags) = IQ_CHUNK_DESCRIPTOR.unpack_from(packet.payload)
    raw = packet.payload[IQ_CHUNK_DESCRIPTOR.size:]
    if chunk_words == 0 or len(raw) != chunk_words * 4:
        raise ValueError(
            f"IQ chunk size mismatch: words={chunk_words} bytes={len(raw)}"
        )
    if offset_words > total_words or chunk_words > total_words - offset_words:
        raise ValueError(
            f"IQ chunk bounds invalid: offset={offset_words}"
            f" words={chunk_words} total={total_words}"
        )
    words = struct.unpack(f"<{chunk_words}I", raw)
    return IQChunk(capture_id, total_words, offset_words, flags, words)


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
            actual_payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
            if actual_payload_crc != expected_payload_crc:
                nested_marker = self._wire.find(MAGIC, 1, wire_bytes)
                if nested_marker >= 0:
                    del self._wire[:nested_marker]
                else:
                    del self._wire[:wire_bytes]
                events.append((
                    "error",
                    "PAYLOAD_CRC"
                    f" type={packet_type} sequence={sequence}"
                    f" bytes={payload_bytes}"
                    f" expected={expected_payload_crc:08x}"
                    f" actual={actual_payload_crc:08x}",
                ))
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
    iq_words = [0x2522304C, 0xB36DF7F4, 0x2520F45E]
    iq_payload = IQ_DESCRIPTOR.pack(len(iq_words), IQ_FLAG_NONE) + struct.pack(
        f"<{len(iq_words)}I", *iq_words
    )
    iq = encode_packet(PACKET_IQ_U32_BLOCK, 5, 2500, iq_payload)
    chunk_words = iq_words[:2]
    chunk_payload = IQ_CHUNK_DESCRIPTOR.pack(
        9, len(iq_words), 0, len(chunk_words), 1
    ) + struct.pack(f"<{len(chunk_words)}I", *chunk_words)
    iq_chunk = encode_packet(PACKET_IQ_U32_CHUNK, 6, 2600, chunk_payload)
    damaged = bytearray(video)
    damaged[-1] ^= 0x80
    end = encode_packet(PACKET_STREAM_END, 7, 3000, b"")
    wire = (b"boot\r\n" + info + bytes(damaged) + b"noise\n" + video +
            iq + iq_chunk + end)

    decoder = StreamDecoder()
    events: list[tuple[str, object]] = []
    for offset in range(0, len(wire), 37):
        events.extend(decoder.feed(wire[offset:offset + 37]))
    lines = [value for kind, value in events if kind == "line"]
    packets = [value for kind, value in events if kind == "packet"]
    errors = [value for kind, value in events if kind == "error"]
    assert "boot" in lines
    assert "noise" in lines
    assert any(str(error).startswith("PAYLOAD_CRC") for error in errors)
    assert [packet.packet_type for packet in packets] == [
        PACKET_STREAM_INFO, PACKET_GRAY8_FRAME, PACKET_IQ_U32_BLOCK,
        PACKET_IQ_U32_CHUNK,
        PACKET_STREAM_END,
    ]
    assert packets[1].payload[FRAME_DESCRIPTOR.size:] == frame
    assert decode_iq_block(packets[2]) == iq_words
    assert list(decode_iq_chunk(packets[3]).words) == chunk_words
    print("c5vrx_usb_protocol: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
