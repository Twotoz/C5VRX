# USB preview protocol v1

**IMPLEMENTED / NOT PHYSICALLY TESTED**

The USB Serial/JTAG byte stream carries normal newline-delimited diagnostics
and binary preview packets together. A Receiver Console parser scans for the
eight-byte magic, validates both CRCs, and resumes scanning after damage. It
never uses a binary payload byte as a delimiter.

All integers are little-endian. Each packet is:

| Offset | Size | Field |
|---:|---:|---|
| 0 | 8 | magic `00 43 35 56 52 58 A5 5A` |
| 8 | 1 | protocol version (`1`) |
| 9 | 1 | packet type |
| 10 | 2 | header bytes (`32`) |
| 12 | 4 | monotonically increasing packet sequence |
| 16 | 4 | payload bytes |
| 20 | 8 | device timestamp in microseconds |
| 28 | 4 | CRC-32/ISO-HDLC of header bytes 0..27 |
| 32 | N | payload |
| 32+N | 4 | CRC-32/ISO-HDLC of the payload |

Payloads are bounded to 1 MiB by the host parser. Unknown packet types with
valid framing can be skipped without losing synchronisation.

Packet types:

| Type | Name | Payload |
|---:|---|---|
| 1 | `STREAM_INFO` | eight-byte image descriptor |
| 2 | `GRAY8_FRAME` | descriptor followed by `stride * height` bytes |
| 3 | `STREAM_END` | 64-bit count of device-dropped frames |

The image descriptor is `width:u16, height:u16, stride:u16, pixel_format:u8,
flags:u8`. Pixel format 1 is GRAY8. Flag bit 0 means the frame was assembled
while the adaptive horizontal and vertical sync tracker was locked.

The firmware holds the C stdio lock across a complete packet. CRC and magic
recovery remain necessary because USB disconnect, reset or transport loss can
still leave a partial packet. `tools/c5vrx_usb_protocol.py --self-test` covers
arbitrary chunk boundaries, CRC rejection and recovery into the next packet.
