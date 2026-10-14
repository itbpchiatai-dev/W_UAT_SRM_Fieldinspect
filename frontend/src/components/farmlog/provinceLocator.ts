/**
 * provinceLocator — turns whatever text a plot carries in `province` into a
 * point on the offline Thailand map.
 *
 * WHY THIS EXISTS
 * ---------------
 * The Dashboard map plots a marker per plot from latitude/longitude. On real
 * data that map is EMPTY: of 738 active plots on UAT, 738 have no coordinate
 * at all. Every one of them does have a province. The map can therefore still
 * say something true — which provinces the work is in, and how much is in each
 * — provided three gaps are bridged first, none of which were:
 *
 *   1. the stored text is not canonical. The same province appears as
 *      "จ.ขอนแก่น" (157 plots) and "ขอนแก่น" (30), because the web form uses a
 *      master_data dropdown while the Excel import only ever length-checked it;
 *   2. master_data holds the 77 provinces in THAI, while thailandGeo.ts —
 *      generated from an English-language GeoJSON — names its shapes
 *      "Khon Kaen", "Mae Hong Son", "Bangkok Metropolis";
 *   3. thailandGeo.ts carries outlines only. There is no point per province to
 *      put a marker on.
 *
 * This module closes all three and nothing else: no rendering, no React, no
 * data fetching, so it can be unit-tested on strings and numbers alone.
 */
import { PROVINCE_SHAPES } from './thailandGeo';

// --- 1. The canonical Thai -> map-shape name table ---------------------------

/**
 * All 77 provinces. The LEFT side must match master_data(type='province')
 * exactly — that is the list the Plots form dropdown writes — and the RIGHT
 * side must match a PROVINCE_SHAPES name exactly.
 *
 * Hand-written because nothing in the repo related the two name spaces, and
 * machine-verified by the tests: 77 entries, no duplicates on either side, and
 * both sets fully consumed. A province that silently fails to match would not
 * throw; it would quietly vanish from the map, which is exactly the class of
 * bug this table exists to prevent.
 */
export const PROVINCE_THAI_TO_SHAPE: ReadonlyMap<string, string> = new Map([
  ['แม่ฮ่องสอน', 'Mae Hong Son'], ['ชุมพร', 'Chumphon'],
  ['นครศรีธรรมราช', 'Nakhon Si Thammarat'], ['ภูเก็ต', 'Phuket'],
  ['พังงา', 'Phangnga'], ['ระนอง', 'Ranong'], ['สุราษฎร์ธานี', 'Surat Thani'],
  ['กระบี่', 'Krabi'], ['พัทลุง', 'Phatthalung'], ['สตูล', 'Satun'],
  ['สงขลา', 'Songkhla'], ['ตรัง', 'Trang'], ['ยะลา', 'Yala'],
  ['เชียงราย', 'Chiang Rai'], ['เชียงใหม่', 'Chiang Mai'], ['ลำปาง', 'Lampang'],
  ['ลำพูน', 'Lamphun'], ['น่าน', 'Nan'], ['พะเยา', 'Phayao'], ['แพร่', 'Phrae'],
  ['พิษณุโลก', 'Phitsanulok'], ['สุโขทัย', 'Sukhothai'], ['อุตรดิตถ์', 'Uttaradit'],
  ['กาญจนบุรี', 'Kanchanaburi'], ['กำแพงเพชร', 'Kamphaeng Phet'],
  ['พิจิตร', 'Phichit'], ['เพชรบูรณ์', 'Phetchabun'], ['สุพรรณบุรี', 'Suphan Buri'],
  ['ตาก', 'Tak'], ['อุทัยธานี', 'Uthai Thani'], ['อ่างทอง', 'Ang Thong'],
  ['ชัยนาท', 'Chai Nat'], ['ลพบุรี', 'Lop Buri'], ['นครนายก', 'Nakhon Nayok'],
  ['ปราจีนบุรี', 'Prachin Buri'], ['นครสวรรค์', 'Nakhon Sawan'],
  ['พระนครศรีอยุธยา', 'Phra Nakhon Si Ayutthaya'], ['ปทุมธานี', 'Pathum Thani'],
  ['สิงห์บุรี', 'Sing Buri'], ['สระบุรี', 'Saraburi'],
  ['กรุงเทพมหานคร', 'Bangkok Metropolis'], ['นนทบุรี', 'Nonthaburi'],
  ['นครปฐม', 'Nakhon Pathom'], ['เพชรบุรี', 'Phetchaburi'],
  ['ประจวบคีรีขันธ์', 'Prachuap Khiri Khan'], ['ราชบุรี', 'Ratchaburi'],
  ['สมุทรปราการ', 'Samut Prakan'], ['สมุทรสาคร', 'Samut Sakhon'],
  ['สมุทรสงคราม', 'Samut Songkhram'], ['ศรีสะเกษ', 'Si Sa Ket'],
  ['อุบลราชธานี', 'Ubon Ratchathani'], ['อำนาจเจริญ', 'Amnat Charoen'],
  ['ยโสธร', 'Yasothon'], ['ชลบุรี', 'Chon Buri'], ['ฉะเชิงเทรา', 'Chachoengsao'],
  ['จันทบุรี', 'Chanthaburi'], ['สระแก้ว', 'Sa Kaeo'], ['ระยอง', 'Rayong'],
  ['ตราด', 'Trat'], ['บุรีรัมย์', 'Buri Ram'], ['ชัยภูมิ', 'Chaiyaphum'],
  ['ขอนแก่น', 'Khon Kaen'], ['กาฬสินธุ์', 'Kalasin'], ['มหาสารคาม', 'Maha Sarakham'],
  ['นครราชสีมา', 'Nakhon Ratchasima'], ['ร้อยเอ็ด', 'Roi Et'], ['สุรินทร์', 'Surin'],
  ['เลย', 'Loei'], ['หนองคาย', 'Nong Khai'], ['สกลนคร', 'Sakon Nakhon'],
  ['อุดรธานี', 'Udon Thani'], ['หนองบัวลำภู', 'Nong Bua Lam Phu'],
  ['นครพนม', 'Nakhon Phanom'], ['มุกดาหาร', 'Mukdahan'], ['นราธิวาส', 'Narathiwat'],
  ['ปัตตานี', 'Pattani'], ['บึงกาฬ', 'Bueng Kan'],
]);

/**
 * Short forms that are not prefixes and so cannot be stripped by rule. Only
 * Bangkok earns an entry: it is the one province Thai writing routinely
 * abbreviates. Everything else is handled by normalizeProvinceName below —
 * guessing at more would risk mapping a typo onto the wrong real province,
 * which is worse than leaving it unplaced and counted as unknown.
 */
const PROVINCE_ALIASES: ReadonlyMap<string, string> = new Map([
  ['กรุงเทพ', 'กรุงเทพมหานคร'],
  ['กรุงเทพฯ', 'กรุงเทพมหานคร'],
  ['กทม', 'กรุงเทพมหานคร'],
  ['กทม.', 'กรุงเทพมหานคร'],
]);

// --- 2. Normalisation --------------------------------------------------------

/**
 * Strip what people put in front of a province name, and the whitespace around
 * it, so "จ.ขอนแก่น", " จังหวัดขอนแก่น " and "ขอนแก่น" all become "ขอนแก่น".
 *
 * Deliberately conservative: it removes a known PREFIX and collapses spaces,
 * and does nothing else. It does not correct spelling and does not guess at a
 * nearest match. An unrecognised value should surface as unknown rather than
 * be placed on a province it may not belong to.
 */
export function normalizeProvinceName(raw: string | null | undefined): string {
  if (!raw) return '';
  let value = raw.replace(/\s+/g, ' ').trim();
  for (const prefix of ['จังหวัด', 'จ.', 'จ ']) {
    if (value.startsWith(prefix)) {
      value = value.slice(prefix.length).trim();
      break;
    }
  }
  return PROVINCE_ALIASES.get(value) ?? value;
}

// --- 3. A point inside each province ----------------------------------------

interface Point { x: number; y: number }

/** Split a baked "M x yL x y...Z" path into its closed rings. Eight provinces
 * have more than one (Satun has 14) — islands. */
function parseRings(d: string): Point[][] {
  const rings: Point[][] = [];
  for (const chunk of d.split('M').slice(1)) {
    const nums = chunk.match(/-?\d+(?:\.\d+)?/g);
    if (!nums || nums.length < 6) continue;
    const ring: Point[] = [];
    for (let i = 0; i + 1 < nums.length; i += 2) {
      ring.push({ x: Number(nums[i]), y: Number(nums[i + 1]) });
    }
    if (ring.length >= 3) rings.push(ring);
  }
  return rings;
}

/** Twice the signed area — sign tells winding, magnitude ranks rings. */
function signedArea2(ring: Point[]): number {
  let total = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    total += ring[j].x * ring[i].y - ring[i].x * ring[j].y;
  }
  return total;
}

function areaCentroid(ring: Point[]): Point {
  const a2 = signedArea2(ring);
  if (a2 === 0) return ring[0];
  let cx = 0;
  let cy = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const cross = ring[j].x * ring[i].y - ring[i].x * ring[j].y;
    cx += (ring[j].x + ring[i].x) * cross;
    cy += (ring[j].y + ring[i].y) * cross;
  }
  return { x: cx / (3 * a2), y: cy / (3 * a2) };
}

function isInside(ring: Point[], p: Point): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i];
    const b = ring[j];
    if ((a.y > p.y) !== (b.y > p.y)
      && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

/**
 * A point guaranteed to sit INSIDE the ring, at the height of the centroid.
 *
 * The area centroid of a concave shape can fall outside it — a crescent is the
 * textbook case, and several Thai provinces wrap around a neighbour. Placing a
 * marker there would put the label in the wrong province, which looks like a
 * data error rather than a geometry one. When that happens, this walks the
 * horizontal line through the centroid, finds the widest span that is actually
 * within the polygon, and takes its midpoint.
 */
function pointInsideRing(ring: Point[]): Point {
  const centroid = areaCentroid(ring);
  if (isInside(ring, centroid)) return centroid;

  const crossings: number[] = [];
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i];
    const b = ring[j];
    if ((a.y > centroid.y) !== (b.y > centroid.y)) {
      crossings.push(((b.x - a.x) * (centroid.y - a.y)) / (b.y - a.y) + a.x);
    }
  }
  crossings.sort((m, n) => m - n);
  let best = centroid.x;
  let widest = -1;
  for (let i = 0; i + 1 < crossings.length; i += 2) {
    const span = crossings[i + 1] - crossings[i];
    if (span > widest) {
      widest = span;
      best = (crossings[i] + crossings[i + 1]) / 2;
    }
  }
  return { x: best, y: centroid.y };
}

/** Computed once on first use — 77 provinces, parsed from paths that never
 * change at runtime. */
let anchorCache: Map<string, Point> | null = null;

function anchors(): Map<string, Point> {
  if (anchorCache) return anchorCache;
  const out = new Map<string, Point>();
  for (const shape of PROVINCE_SHAPES) {
    const rings = parseRings(shape.d);
    if (rings.length === 0) continue;
    // Largest ring = the mainland. Without this, the Satun marker could land
    // on one of its fourteen islands.
    let main = rings[0];
    let mainArea = Math.abs(signedArea2(rings[0]));
    for (const ring of rings.slice(1)) {
      const area = Math.abs(signedArea2(ring));
      if (area > mainArea) {
        main = ring;
        mainArea = area;
      }
    }
    out.set(shape.name, pointInsideRing(main));
  }
  anchorCache = out;
  return out;
}

// --- 4. The one function callers need ---------------------------------------

export interface ProvinceLocation {
  /** Canonical Thai name, as master_data spells it. */
  thaiName: string;
  /** The PROVINCE_SHAPES name this resolved to. */
  shapeName: string;
  /** SVG viewBox coordinates, in the same space as projectLngLat(). */
  x: number;
  y: number;
}

/**
 * Resolve the stored province text of a plot to a point on the map, or null
 * when it cannot be resolved with certainty. Callers should COUNT the nulls
 * and say so rather than drop them silently — a plot that cannot be placed is
 * still a plot, and hiding it is how a map comes to disagree with the table
 * beside it.
 */
export function locateProvince(raw: string | null | undefined): ProvinceLocation | null {
  const thaiName = normalizeProvinceName(raw);
  if (!thaiName) return null;
  const shapeName = PROVINCE_THAI_TO_SHAPE.get(thaiName);
  if (!shapeName) return null;
  const point = anchors().get(shapeName);
  if (!point) return null;
  return { thaiName, shapeName, x: point.x, y: point.y };
}

/** Test-support: drops the memoised anchors so a test can re-measure. */
export function resetProvinceAnchorCacheForTests(): void {
  anchorCache = null;
}
