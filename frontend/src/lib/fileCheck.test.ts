import { describe, expect, it } from 'vitest';
import { evaluate, parseName, sniffType, zipEntries } from './fileCheck';

const bytes = (s: string) => Uint8Array.from(s, (c) => c.charCodeAt(0));
const mdbHead = new Uint8Array([0, 1, 0, 0, ...bytes('Standard Jet DB'), 0]);

function zipWith(name: string): Uint8Array {
  const n = bytes(name);
  const central = new Uint8Array(46 + n.length);
  const c = new DataView(central.buffer);
  c.setUint32(0, 0x02014b50, true);
  c.setUint16(28, n.length, true);
  central.set(n, 46);
  const end = new Uint8Array(22);
  const e = new DataView(end.buffer);
  e.setUint32(0, 0x06054b50, true);
  e.setUint16(10, 1, true);
  e.setUint32(12, central.length, true);
  const out = new Uint8Array(4 + central.length + 22);
  out.set([0x50, 0x4b, 0x03, 0x04]);
  out.set(central, 4);
  out.set(end, 4 + central.length);
  return out;
}

describe('sniffType', () => {
  it('recognises real signatures rather than trusting extensions', () => {
    expect(sniffType(mdbHead)).toBe('mdb');
    expect(sniffType(new Uint8Array([0x50, 0x4b, 3, 4]))).toBe('zip');
    expect(sniffType(bytes('%PDF-1.7 not a database....'))).toBeNull();
  });
});

describe('parseName', () => {
  it('reads domain and issue from ITU file names', () => {
    expect(parseName('ific3074.zip')).toEqual({ domain: 'space', issue: 3074 });
    expect(parseName('brific3076.mdb')).toEqual({ domain: 'terrestrial', issue: 3076 });
    expect(parseName('my upload.zip')).toEqual({ domain: null, issue: null });
  });
});

describe('zipEntries', () => {
  it('lists file names from the central directory', () => {
    expect(zipEntries(zipWith('ific3083.mdb'))).toEqual(['ific3083.mdb']);
    expect(zipEntries(new Uint8Array(64))).toEqual([]);
  });
});

describe('evaluate', () => {
  it('accepts an IFIC zip and takes the issue from the inner database', () => {
    const z = zipWith('ific3083.mdb');
    expect(evaluate('download.zip', z.length, z, z)).toMatchObject({ ok: true, type: 'zip', domain: 'space', issue: 3083 });
  });
  it('rejects a zip with no database inside', () => {
    const z = zipWith('readme.txt');
    expect(evaluate('ific3083.zip', z.length, z, z)).toMatchObject({ ok: false, type: 'zip' });
  });
  it('rejects a renamed file, an empty file and an oversized file', () => {
    const pdf = bytes('%PDF-1.7 not a database....');
    expect(evaluate('ific3083.mdb', pdf.length, pdf, pdf).ok).toBe(false);
    expect(evaluate('ific3083.mdb', 0, mdbHead, mdbHead).ok).toBe(false);
    expect(evaluate('ific3083.mdb', 2 * 1024 ** 3, mdbHead, mdbHead).ok).toBe(false);
  });
});
