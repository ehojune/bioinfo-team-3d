// A metrics-only TrueType font for layout tests, built in memory (no file, no dependency).
// Every glyph is blank; Latin-range glyphs are 9/16 em wide and everything else (Hangul, CJK punctuation,
// symbols) is 1 em wide, with a 1 em line box. Forcing it on the demo page makes text widths and wrapping
// identical on Windows, macOS and Linux, so a layout snapshot does not depend on which system fonts exist.
'use strict';

// 1024 units per em and advances in whole sixteenths of an em (576 = 9/16, 1024 = 16/16) make every advance an exact
// multiple of 1/16 px at integer font sizes, so FreeType's 1/64 px rounding (Linux) cannot move a line break.
const UNITS = 1024, NARROW = 576, WIDE = 1024, ASCENT = 901, DESCENT = 123;
const NARROW_RANGES = [[0x20, 0x24F], [0x2000, 0x206F]];
const WIDE_RANGES = [[0x2070, 0x22FF], [0x2460, 0x27BF], [0x2B00, 0x2BFF], [0x3000, 0x30FF], [0x3130, 0x318F],
  [0x3200, 0x32FF], [0xAC00, 0xD7A3], [0xFF00, 0xFFEF]];

const u16 = n => { const b = Buffer.alloc(2); b.writeUInt16BE(n & 0xFFFF); return b; };
const i16 = n => { const b = Buffer.alloc(2); b.writeInt16BE(n); return b; };
const u32 = n => { const b = Buffer.alloc(4); b.writeUInt32BE(n >>> 0); return b; };
const pad4 = buf => Buffer.concat([buf, Buffer.alloc((4 - buf.length % 4) % 4)]);
const checksum = buf => { let sum = 0; const p = pad4(buf); for (let i = 0; i < p.length; i += 4) sum = (sum + p.readUInt32BE(i)) >>> 0; return sum; };

function buildTestFont() {
  // Glyph 0 is .notdef (a box); narrow glyphs follow; wide glyphs come last so hmtx only lists the narrow ones.
  const groups = [];
  let next = 1;
  const addRanges = ranges => { for (const [a, b] of ranges) { groups.push([a, b, next]); next += b - a + 1; } };
  addRanges(NARROW_RANGES);
  const narrowCount = next - 1;
  addRanges(WIDE_RANGES);
  const numGlyphs = next;
  const numberOfHMetrics = narrowCount + 2;  // .notdef, every narrow glyph, then the first wide one; the rest repeat it

  const cmapSub = Buffer.concat([u16(12), u16(0), u32(16 + groups.length * 12), u32(0), u32(groups.length),
    ...groups.map(([a, b, g]) => Buffer.concat([u32(a), u32(b), u32(g)]))]);
  const cmap = Buffer.concat([u16(0), u16(2), u16(0), u16(4), u32(20), u16(3), u16(10), u32(20), cmapSub]);

  const advance = g => (g === 0 ? WIDE : g <= narrowCount ? NARROW : WIDE);
  const hmtx = Buffer.concat([
    ...Array.from({length: numberOfHMetrics}, (_, g) => Buffer.concat([u16(advance(g)), i16(0)])),
    ...Array.from({length: numGlyphs - numberOfHMetrics}, () => i16(0))]);

  // .notdef: one square contour; every other glyph is empty (loca entries repeat).
  const glyph0 = pad4(Buffer.concat([i16(1), i16(100), i16(0), i16(900), i16(800), u16(3), u16(0),
    Buffer.from([1, 1, 1, 1]), i16(100), i16(800), i16(0), i16(-800), i16(0), i16(0), i16(800), i16(0)]));
  const loca = Buffer.concat([u32(0), ...Array.from({length: numGlyphs}, () => u32(glyph0.length))]);

  const head = Buffer.concat([u32(0x00010000), u32(0x00010000), u32(0), u32(0x5F0F3CF5), u16(0x000B), u16(UNITS),
    Buffer.alloc(16), i16(0), i16(0), i16(WIDE), i16(ASCENT), u16(0), u16(8), i16(2), i16(1), i16(0)]);
  const hhea = Buffer.concat([u32(0x00010000), i16(ASCENT), i16(-DESCENT), i16(0), u16(WIDE), i16(0), i16(0), i16(WIDE),
    i16(1), i16(0), i16(0), Buffer.alloc(8), i16(0), u16(numberOfHMetrics)]);
  const maxp = Buffer.concat([u32(0x00010000), u16(numGlyphs), u16(4), u16(1), u16(0), u16(0), u16(2), u16(0), u16(0),
    u16(0), u16(0), u16(0), u16(0), u16(0), u16(0)]);
  const os2 = Buffer.concat([u16(4), i16(NARROW), u16(400), u16(5), u16(0),
    i16(650), i16(600), i16(0), i16(75), i16(650), i16(600), i16(0), i16(350), i16(50), i16(300), i16(0), Buffer.alloc(10),
    u32(0xFFFFFFFF), u32(0xFFFFFFFF), u32(0), u32(0), Buffer.from('NONE'), u16(0x00C0), u16(0x20), u16(0xFFFF),
    i16(ASCENT), i16(-DESCENT), i16(0), u16(ASCENT), u16(DESCENT), u32(1), u32(0), i16(500), i16(700), u16(0), u16(0x20), u16(1)]);
  const post = Buffer.concat([u32(0x00030000), u32(0), i16(-100), i16(50), u32(0), u32(0), u32(0), u32(0), u32(0)]);
  const names = ['LabhqLayoutFont', 'Regular', 'LabhqLayoutFont Regular', 'LabhqLayoutFont Regular', 'LabhqLayoutFont-Regular'];
  const ids = [1, 2, 3, 4, 6];
  const strings = names.map(s => Buffer.from(s, 'utf16le').swap16());
  let off = 0;
  const records = strings.map((s, i) => { const r = Buffer.concat([u16(3), u16(1), u16(0x409), u16(ids[i]), u16(s.length), u16(off)]); off += s.length; return r; });
  const name = Buffer.concat([u16(0), u16(names.length), u16(6 + names.length * 12), ...records, ...strings]);

  const tables = {'OS/2': os2, cmap, glyf: glyph0, head, hhea, hmtx, loca, maxp, name, post};
  const tags = Object.keys(tables).sort();
  let offset = 12 + tags.length * 16;
  const dir = [], body = [];
  for (const tag of tags) {
    const data = tables[tag];
    dir.push(Buffer.concat([Buffer.from(tag), u32(checksum(data)), u32(offset), u32(data.length)]));
    const padded = pad4(data); body.push(padded); offset += padded.length;
  }
  const log2 = Math.floor(Math.log2(tags.length));
  const header = Buffer.concat([u32(0x00010000), u16(tags.length), u16((1 << log2) * 16), u16(log2), u16(tags.length * 16 - (1 << log2) * 16)]);
  const font = Buffer.concat([header, ...dir, ...body]);
  // head.checkSumAdjustment makes the whole-file checksum equal 0xB1B0AFBA.
  const headPos = font.readUInt32BE(12 + tags.indexOf('head') * 16 + 8);
  font.writeUInt32BE(0, headPos + 8);
  font.writeUInt32BE((0xB1B0AFBA - checksum(font)) >>> 0, headPos + 8);
  return font;
}

// Page-side script: install the font before page scripts run and force it on every element.
function fontInjectionSource(fontBase64) {
  return `(() => {
    const bytes = Uint8Array.from(atob('${fontBase64}'), c => c.charCodeAt(0));
    const face = new FontFace('LabhqLayoutFont', bytes.buffer, {weight: '100 900', style: 'normal'});
    document.fonts.add(face);
    window.__layoutFontLoaded = face.load().then(() => true, e => { window.__layoutFontError = String(e); return false; });
    const apply = () => {
      const s = document.createElement('style');
      s.textContent = '*,*::before,*::after{font-family:LabhqLayoutFont !important;font-synthesis:none !important;text-rendering:geometricPrecision !important}';
      document.head.appendChild(s);
    };
    if (document.head) apply(); else document.addEventListener('DOMContentLoaded', apply);
  })();`;
}

module.exports = {buildTestFont, fontInjectionSource};
