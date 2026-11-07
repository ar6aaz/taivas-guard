// Local checks that run in the browser before anything is uploaded.

export type FileType = 'zip' | 'mdb';

export interface FileCheck {
  ok: boolean;
  type: FileType | null;
  message: string;
  domain: 'space' | 'terrestrial' | null;
  issue: number | null;
  contains: string[];
}

export const MAX_BYTES = 1024 * 1024 * 1024; // 1 GiB; the largest IFIC we've seen unzips to 287 MB

const JET = 'Standard Jet DB';
const ACE = 'Standard ACE DB';

export function sniffType(head: Uint8Array): FileType | null {
  if (head.length >= 4 && head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04) return 'zip';
  const label = String.fromCharCode(...head.slice(4, 19));
  if (label === JET || label === ACE) return 'mdb';
  return null;
}

/** Issue number and domain from names like ific3074.zip or brific3076.mdb. */
export function parseName(name: string): { domain: 'space' | 'terrestrial' | null; issue: number | null } {
  const lower = name.toLowerCase();
  const m = lower.match(/(\d{4})/);
  const domain = /(^|[^a-z])brific/.test(lower) ? 'terrestrial' : /(^|[^a-z])(ific|srs)/.test(lower) ? 'space' : null;
  return { domain, issue: m ? Number(m[1]) : null };
}

/** Reads file names from a zip's central directory without unpacking it. */
export function zipEntries(tail: Uint8Array): string[] {
  const view = new DataView(tail.buffer, tail.byteOffset, tail.byteLength);
  let eocd = -1;
  for (let i = tail.length - 22; i >= 0; i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) return [];
  const count = view.getUint16(eocd + 10, true);
  const dirSize = view.getUint32(eocd + 12, true);
  let p = eocd - dirSize;
  const names: string[] = [];
  const dec = new TextDecoder();
  for (let n = 0; n < count && p >= 0 && p + 46 <= tail.length; n++) {
    if (view.getUint32(p, true) !== 0x02014b50) break;
    const nameLen = view.getUint16(p + 28, true);
    const extra = view.getUint16(p + 30, true);
    const comment = view.getUint16(p + 32, true);
    names.push(dec.decode(tail.subarray(p + 46, p + 46 + nameLen)));
    p += 46 + nameLen + extra + comment;
  }
  return names;
}

export function evaluate(name: string, size: number, head: Uint8Array, tail: Uint8Array): FileCheck {
  const base = { type: null, domain: null, issue: null, contains: [] as string[] };
  if (size === 0) return { ...base, ok: false, message: 'This file is empty.' };
  if (size > MAX_BYTES) return { ...base, ok: false, message: 'This file is larger than the 1 GB limit.' };
  const type = sniffType(head);
  if (!type) {
    return { ...base, ok: false, message: 'This isn’t a ZIP or Access database. Choose an IFIC .zip or .mdb file, even if the extension looks right.' };
  }
  const contains = type === 'zip' ? zipEntries(tail) : [];
  const mdb = contains.find((n) => /\.(mdb|accdb)$/i.test(n));
  if (type === 'zip' && !mdb) {
    return { ...base, type, contains, ok: false, message: 'This ZIP doesn’t contain an .mdb database, so there’s nothing to analyse.' };
  }
  const parsed = parseName(mdb ?? name);
  const fallback = parseName(name);
  return {
    ok: true,
    type,
    contains,
    domain: parsed.domain ?? fallback.domain,
    issue: parsed.issue ?? fallback.issue,
    message: type === 'zip' ? `ZIP containing ${mdb}` : 'Access database',
  };
}

export async function checkFile(file: File): Promise<FileCheck> {
  const head = new Uint8Array(await file.slice(0, 32).arrayBuffer());
  const tailSize = Math.min(file.size, 1024 * 1024);
  const tail = new Uint8Array(await file.slice(file.size - tailSize).arrayBuffer());
  return evaluate(file.name, file.size, head, tail);
}

export async function sha256Hex(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}
