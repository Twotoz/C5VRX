// C5VRX-5 flight log in the browser (C5VRX by Twotoz and the C5VRX
// contributors). Reads the console dump (key W: FLOG_BEGIN / FLOG_D / FLOG_END)
// into a partition image and decodes it. Layout: main/flightlog_format.h,
// docs/FLIGHT_LOG.md; tools/flight_log.py reads the same image (.c5log).

export const SECTOR = 4096;
export const RECORD_BLOCKS = 255;
export const SNAP_FIRST = 256;
export const SNAP_SLOTS = 64;
export const SNAP_SECTORS = 2;
export const SNAP_HEAD = 64;
export const SNAP_BYTES = SNAP_SECTORS * SECTOR - SNAP_HEAD;
export const PARTITION_BYTES = (SNAP_FIRST + SNAP_SLOTS * SNAP_SECTORS) * SECTOR;
const BLOCK_HEAD = 20;
const CRC_AT = SECTOR - 4;
const MAGIC_BLOCK = 0x424c3543;
const MAGIC_SNAP = 0x534c3543;

export const EVENTS = { 1: 'channel', 2: 'profile', 3: 'kp', 4: 'cal_start', 5: 'cal_end', 6: 'dc_cal_needed',
  7: 'gain_mode', 8: 'menu', 9: 'fault', 10: 'dropout', 11: 'recovered', 12: 'snapshot', 13: 'dropped' };
export const DROPOUT = ['CALIBRATION_OR_PHY', 'FAST_EVIDENCE_LOSS', 'AT_RF_FLOOR', 'DEMOD_CLIFF', 'UNKNOWN'];
export const SPHASE = ['unverified', 'settled', 'drifted', 'unsettled', 'noise_limited', 'probe_failed'];
export const PROFILES = ['DEFAULT', 'MATCHED', 'NARROW', 'WIDE'];
export const RESET_REASONS = ['unknown', 'power-on', 'external', 'software', 'panic', 'interrupt watchdog',
  'task watchdog', 'watchdog', 'deep sleep', 'brownout', 'SDIO', 'USB', 'JTAG', 'eFuse', 'power glitch', 'CPU lockup'];
const TRIGGERS = { 1: 'dropout', 2: 'picture drop', 3: 'first carrier', 4: 'periodic' };

let crcTable;
export function crc32(bytes, crc = 0) {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  crc = ~crc >>> 0;
  for (let i = 0; i < bytes.length; i++) crc = crcTable[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return ~crc >>> 0;
}

// Collects a console dump. push() takes complete lines; `done` after FLOG_END.
export class FlightLogCapture {
  constructor() { this.reset(); }
  reset() {
    this.image = new Uint8Array(PARTITION_BYTES).fill(0xff);
    this.active = false;
    this.done = false;
    this.error = '';
    this.expectedBytes = 0;
    this.receivedBytes = 0;
    this.info = {};
  }
  start() { this.reset(); this.active = true; }
  get progress() { return this.expectedBytes ? Math.min(1, this.receivedBytes / this.expectedBytes) : 0; }
  // Returns true when the line belonged to the dump.
  push(line) {
    const at = line.indexOf('FLOG');
    if (at < 0) return false;
    line = line.slice(at).trim();
    if (line.startsWith('FLOG partition=missing')) { this.error = 'This module has no flight-log partition: flash the full firmware image once.'; this.active = false; return true; }
    if (!this.active) return line.startsWith('FLOG_D ');
    if (line.startsWith('FLOG_BEGIN')) {
      for (const [, k, v] of line.matchAll(/(\w+)=(\S+)/g)) this.info[k] = v;
      this.expectedBytes = 32 + Number(this.info.blocks || 0) * SECTOR + Number(this.info.snapshots || 0) * SNAP_SECTORS * SECTOR;
      return true;
    }
    if (line.startsWith('FLOG_D ')) {
      const [, offset, hex] = line.split(' ');
      const o = Number(offset);
      if (!Number.isInteger(o) || !hex || hex.length % 2 || o + hex.length / 2 > PARTITION_BYTES) return true;
      for (let i = 0; i < hex.length / 2; i++) this.image[o + i] = parseInt(hex.substr(i * 2, 2), 16);
      this.receivedBytes += hex.length / 2;
      return true;
    }
    if (line.startsWith('FLOG_END')) { this.active = false; this.done = true; return true; }
    if (line.startsWith('FLOG_ERR')) { this.error = line; return true; }
    return line.startsWith('FLOG');
  }
}

function view(bytes) { return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); }

function* records(block) {
  let at = BLOCK_HEAD;
  while (at + 2 <= CRC_AT) {
    const type = block[at], len = block[at + 1];
    if (type === 0 || type === 0xff || at + 2 + len > CRC_AT) break;
    yield [type, block.subarray(at + 2, at + 2 + len)];
    at += 2 + len;
  }
}

export function decodeSecond(p) {
  const d = view(p);
  const s = {
    t: d.getUint32(0, true), snr: d.getInt16(4, true) / 10, lines: d.getUint16(6, true) / 100,
    cn: d.getInt16(8, true), video: p[10], dropout: p[11], gainMin: p[12], gain: p[13], gainMax: p[14],
    clip: p[15] * 4, gainWrites: p[16], overloads: p[17], dcI: d.getInt8(18) * 0.05, dcQ: d.getInt8(19) * 0.05,
    offI: d.getInt8(20), offQ: d.getInt8(21), servo: p[22], afcOffset: d.getInt16(23, true),
    afcResidual: d.getInt16(25, true), afcSteps: p[27], profile: PROFILES[p[28] & 3], kp: (p[28] >> 2) & 7,
    lp: (p[28] >> 5) & 1, sphase: SPHASE[p[29]] ?? 'unknown', temp: d.getInt8(30), flags: d.getUint16(31, true)
  };
  s.cn = s.cn <= -990 ? null : s.cn / 10;
  s.temp = s.temp <= -100 ? null : s.temp;
  s.dc = Math.hypot(s.dcI, s.dcQ);
  s.dropoutCategory = s.dropout ? DROPOUT[s.dropout - 1] ?? 'UNKNOWN' : '';
  return s;
}

export function decodeImage(image) {
  const blocks = [];
  for (let k = 0; k < RECORD_BLOCKS; k++) {
    const b = image.subarray((1 + k) * SECTOR, (2 + k) * SECTOR);
    const d = view(b);
    if (d.getUint32(0, true) !== MAGIC_BLOCK || b[4] !== 1) continue;
    blocks.push({ seq: d.getUint32(12, true), session: d.getUint32(8, true), closed: d.getUint32(CRC_AT, true) === crc32(b.subarray(0, CRC_AT)), b });
  }
  blocks.sort((a, b) => a.seq - b.seq);
  const sessions = new Map();
  for (const blk of blocks) {
    let s = sessions.get(blk.session);
    if (!s) sessions.set(blk.session, s = { session: blk.session, seconds: [], events: [], carriers: [], traces: [], blocks: 0, openBlocks: 0 });
    s.blocks++;
    if (!blk.closed) s.openBlocks++;
    for (const [type, p] of records(blk.b)) {
      const d = view(p);
      if (type === 1 && p.length >= 48) {
        s.boot = { resetReason: RESET_REASONS[p[5]] ?? String(p[5]), firmware: new TextDecoder().decode(p.subarray(6, 30)).replace(/\0.*$/s, ''),
          calGeneration: d.getUint16(30, true), boardId: d.getUint32(33, true).toString(16).padStart(8, '0'),
          channelIndex: p[40], gainMode: p[43], freq: d.getUint16(46, true) };
      } else if (type === 2 && p.length >= 56) {
        const filters = {};
        ['NORMAL', 'NARROW', 'WIDE'].forEach((name, c) => {
          const e = 6 + c * 8;
          filters[name] = p[e] ? { bw20: p[e + 1], code: d.getInt8(e + 2), skirt: p[e + 3], widthKhz: d.getUint16(e + 4, true), nbwKhz: d.getUint16(e + 6, true) } : null;
        });
        s.calib = { manual: !!p[0], filterResult: ['', 'loaded', 'stored', 'other'][Math.min(p[1], 3)], durationMs: d.getUint32(2, true),
          filters, dcRows: p[45], dcMissing: p[46], dcTemplate: p[47], repairFlags: p[48], sphase: SPHASE[p[49]] ?? 'unknown' };
      } else if (type === 3 && p.length >= 17) {
        s.carriers.push({ tMs: d.getUint32(0, true), freq: d.getUint16(4, true), afcOffset: d.getInt16(6, true),
          porchKhz: d.getInt32(8, true), deviation: d.getInt16(12, true) / 100, profile: PROFILES[p[14] & 3],
          standard: ['', 'NTSC', 'PAL'][Math.min(p[15], 2)] });
      } else if (type === 4 && p.length >= 34) {
        s.seconds.push(decodeSecond(p));
      } else if (type === 5 && p.length >= 5) {
        s.events.push({ tMs: d.getUint32(0, true), event: EVENTS[p[4]] ?? String(p[4]), data: p.slice(5) });
      } else if (type === 6 && p.length >= 6) {
        s.traces.push({ onsetMs: d.getUint32(0, true), part: p[4], count: p[5] });
      }
    }
  }
  const snapshots = [];
  for (let k = 0; k < SNAP_SLOTS; k++) {
    const at = (SNAP_FIRST + k * SNAP_SECTORS) * SECTOR;
    const h = image.subarray(at, at + SNAP_HEAD);
    const d = view(h);
    if (d.getUint32(0, true) !== MAGIC_SNAP) continue;
    const iq = image.subarray(at + SNAP_HEAD, at + SNAP_SECTORS * SECTOR);
    snapshots.push({ slot: k, ok: d.getUint32(60, true) === crc32(iq, crc32(h.subarray(0, 60))), trigger: TRIGGERS[h[5]] ?? String(h[5]),
      session: d.getUint32(8, true), seq: d.getUint32(12, true), tMs: d.getUint32(16, true), freq: d.getUint16(20, true),
      gain: h[22], iq });
  }
  snapshots.sort((a, b) => a.seq - b.seq);
  return { sessions: [...sessions.values()].sort((a, b) => a.session - b.session), snapshots };
}

const median = (values) => {
  const v = values.filter((x) => x !== null && Number.isFinite(x)).sort((a, b) => a - b);
  return v.length ? v[Math.floor(v.length / 2)] : null;
};

// One line per session for the table.
export function summarize(session) {
  const sec = session.seconds;
  const withPicture = sec.filter((s) => s.lines > 0);
  return {
    session: session.session,
    durationS: sec.length ? sec[sec.length - 1].t - sec[0].t + 1 : 0,
    firmware: session.boot?.firmware ?? '',
    resetReason: session.boot?.resetReason ?? '',
    freq: session.boot?.freq ?? null,
    pictureS: withPicture.length,
    snrMedian: median(withPicture.map((s) => s.snr)),
    snrWorst: withPicture.length ? Math.min(...withPicture.map((s) => s.snr)) : null,
    dropouts: session.events.filter((e) => e.event === 'dropout').length,
    dcWorst: sec.length ? Math.max(...sec.map((s) => s.dc)) : null,
    tempMax: median(sec.map((s) => s.temp)) === null ? null : Math.max(...sec.filter((s) => s.temp !== null).map((s) => s.temp)),
    deviation: session.carriers.length ? session.carriers[session.carriers.length - 1].deviation : null
  };
}

export function secondsCsv(session) {
  const keys = ['t', 'snr', 'lines', 'cn', 'video', 'dropoutCategory', 'gainMin', 'gain', 'gainMax', 'clip', 'gainWrites',
    'overloads', 'dcI', 'dcQ', 'offI', 'offQ', 'servo', 'afcOffset', 'afcResidual', 'afcSteps', 'profile', 'kp', 'lp',
    'sphase', 'temp', 'flags'];
  const rows = session.seconds.map((s) => keys.map((k) => (s[k] === null || s[k] === undefined ? '' : typeof s[k] === 'number' ? +s[k].toFixed(3) : s[k])).join(','));
  return [keys.join(','), ...rows].join('\n') + '\n';
}
