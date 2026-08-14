import json, math, os

SRC = r'C:\Users\tunyawut.wo\AppData\Local\Temp\th.json'
OUT = r'D:\DEV-Tunyawut\SRM_FieldInspect\frontend\src\components\farmlog\thailandGeo.ts'

d = json.load(open(SRC, encoding='utf-8'))

minx = miny = 1e9
maxx = maxy = -1e9

def walk(c):
    global minx, miny, maxx, maxy
    if isinstance(c[0], (int, float)):
        x, y = c[0], c[1]
        minx = min(minx, x); maxx = max(maxx, x)
        miny = min(miny, y); maxy = max(maxy, y)
    else:
        for p in c:
            walk(p)

for f in d['features']:
    walk(f['geometry']['coordinates'])

MIN_LNG, MAX_LNG, MIN_LAT, MAX_LAT = minx, maxx, miny, maxy
mid = math.radians((MIN_LAT + MAX_LAT) / 2)
lon_scale = math.cos(mid)
VIEW_H = 1000.0
scale = VIEW_H / (MAX_LAT - MIN_LAT)
VIEW_W = (MAX_LNG - MIN_LNG) * lon_scale * scale


def proj(lng, lat):
    x = (lng - MIN_LNG) * lon_scale * scale
    y = (MAX_LAT - lat) * scale
    return x, y


def simplify(ring, eps=1.2):
    out = []
    for pt in ring:
        x, y = proj(pt[0], pt[1])
        if not out or (abs(x - out[-1][0]) + abs(y - out[-1][1])) >= eps:
            out.append((x, y))
    if len(out) >= 1 and out[0] != out[-1]:
        out.append(out[0])
    return out


def ring_to_d(ring):
    parts = []
    for i, (x, y) in enumerate(ring):
        cmd = 'M' if i == 0 else 'L'
        parts.append(cmd + '%.1f %.1f' % (x, y))
    return ''.join(parts) + 'Z'


feats = []
for f in d['features']:
    name = f['properties'].get('name', '')
    g = f['geometry']
    t = g['type']
    polys = []
    if t == 'Polygon':
        polys = [g['coordinates']]
    elif t == 'MultiPolygon':
        polys = g['coordinates']
    dparts = []
    for poly in polys:
        for ring in poly:
            r = simplify(ring)
            if len(r) >= 4:
                dparts.append(ring_to_d(r))
    if dparts:
        feats.append((name, ''.join(dparts)))

BS = chr(92)   # backslash
SQ = chr(39)   # single quote

L = []
L.append('/**')
L.append(' * Thailand province outlines as pre-projected SVG paths + the projection')
L.append(' * used to bake them. GENERATED ONCE from public GeoJSON (apisit/thailand.json,')
L.append(' * 77 provinces) via scripts/gen_thailand_geo.py — do not hand-edit. Fully')
L.append(" * offline: no map tiles, no runtime network, satisfies the strict nginx CSP")
L.append(" * (img-src 'self').")
L.append(' *')
L.append(' * projectLngLat() below MUST stay identical to the Python projection that')
L.append(' * produced these paths, so plot markers land in the right place on the map.')
L.append(' */')
L.append('export const VIEW_WIDTH = %.1f;' % VIEW_W)
L.append('export const VIEW_HEIGHT = %.1f;' % VIEW_H)
L.append('const MIN_LNG = %r;' % MIN_LNG)
L.append('const MAX_LNG = %r;' % MAX_LNG)
L.append('const MIN_LAT = %r;' % MIN_LAT)
L.append('const MAX_LAT = %r;' % MAX_LAT)
L.append('const LON_SCALE = %r;' % lon_scale)
L.append('const SCALE = %r;' % scale)
L.append('')
L.append('/** lng/lat (WGS84) -> SVG viewBox coordinates. Baked identically to the')
L.append(' * generator so markers align with the province outlines. */')
L.append('export function projectLngLat(lng: number, lat: number): { x: number; y: number } {')
L.append('  return {')
L.append('    x: (lng - MIN_LNG) * LON_SCALE * SCALE,')
L.append('    y: (MAX_LAT - lat) * SCALE,')
L.append('  };')
L.append('}')
L.append('')
L.append('/** True when a coordinate falls inside Thailand\'s bounding box (a cheap')
L.append(' * guard so an out-of-range plot does not render off-canvas). */')
L.append('export function isWithinThailand(lng: number, lat: number): boolean {')
L.append('  return lng >= MIN_LNG && lng <= MAX_LNG && lat >= MIN_LAT && lat <= MAX_LAT;')
L.append('}')
L.append('')
L.append('export interface ProvinceShape { name: string; d: string; }')
L.append('')
L.append('export const PROVINCE_SHAPES: ProvinceShape[] = [')
for name, dd in feats:
    nm = name.replace(BS, BS + BS).replace(SQ, BS + SQ)
    L.append("  { name: '" + nm + "', d: '" + dd + "' },")
L.append('];')
L.append('')

open(OUT, 'w', encoding='utf-8').write('\n'.join(L))
print('features:', len(feats), 'bytes:', os.path.getsize(OUT),
      'VIEW', round(VIEW_W, 1), 'x', VIEW_H)
