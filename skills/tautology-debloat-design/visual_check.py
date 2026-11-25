"""Screenshot verification, fast visual telemetry, and before/after region diffing.

Prefers Pillow when installed (fast, handles indexed/grayscale/16-bit PNG, JPEG, WebP, BMP),
with a zero-dependency pure-Python PNG decoder (RGB, RGBA, grayscale, indexed palette) and OS
converter fallback. All visual metrics are non-blocking telemetry meant to anchor human or
multimodal vision audits (see VISION_AUDIT.md) — never brittle pixel pass/fail classifiers.
"""
import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from collections import Counter
from pathlib import Path

SIG = b"\x89PNG\r\n\x1a\n"
MIN_SIDE = 200
CELL = 8
MAX_METRIC_SIDE = 480
ALPHA_MATTE = (128, 128, 128)


def _unfilter_png(raw, width, height, bpp):
    stride = width * bpp
    if len(raw) < height * (stride + 1):
        return None
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filt = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        if filt == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif filt == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif filt == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up = previous[i]
                corner = previous[i - bpp] if i >= bpp else 0
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                pred = left if pa <= pb and pa <= pc else (up if pb <= pc else corner)
                line[i] = (line[i] + pred) & 255
        elif filt != 0:
            return None
        rows.append(bytes(line))
        previous = line
    return rows


def read_png(path):
    """Decodes an 8-bit non-interlaced PNG (RGB, RGBA, grayscale, grayscale+alpha, or indexed PLTE)
    into (width, height, channels, rows) with channels in (3, 4)."""
    src = Path(path)
    if not src.is_file():
        return None
    try:
        data = src.read_bytes()
        if data[:8] != SIG:
            return None
        pos, chunks, header, palette, trns = 8, [], None, None, None
        while pos + 8 <= len(data):
            size, kind = struct.unpack(">I4s", data[pos:pos + 8])
            if pos + 8 + size > len(data):
                return None
            body = data[pos + 8:pos + 8 + size]
            if kind == b"IHDR":
                if len(body) != 13:
                    return None
                header = struct.unpack(">IIBBBBB", body)
            elif kind == b"PLTE":
                palette = body
            elif kind == b"tRNS":
                trns = body
            elif kind == b"IDAT":
                chunks.append(body)
            elif kind == b"IEND":
                break
            pos += 12 + size
        if not header:
            return None
        width, height, depth, color, _, _, interlace = header
        if depth != 8 or interlace or width <= 0 or height <= 0:
            return None
        bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
        if bpp is None:
            return None
        raw = zlib.decompress(b"".join(chunks))
        scanlines = _unfilter_png(raw, width, height, bpp)
        if scanlines is None:
            return None
        if color in (2, 6):
            return width, height, bpp, scanlines
        if color == 0:
            rows = [bytes(v for px in row for v in (px, px, px)) for row in scanlines]
            return width, height, 3, rows
        if color == 4:
            rows = [
                bytes(v for i in range(0, len(row), 2) for v in (row[i], row[i], row[i], row[i + 1]))
                for row in scanlines
            ]
            return width, height, 4, rows
        if color == 3:
            if not palette or len(palette) < 3:
                return None
            n_entries = len(palette) // 3
            has_alpha = trns is not None and len(trns) > 0
            table = []
            for idx in range(n_entries):
                r, g, b = palette[idx * 3:idx * 3 + 3]
                if has_alpha:
                    a = trns[idx] if idx < len(trns) else 255
                    table.append(bytes((r, g, b, a)))
                else:
                    table.append(bytes((r, g, b)))
            fallback = table[0]
            rows = [b"".join(table[p] if p < n_entries else fallback for p in row) for row in scanlines]
            return width, height, (4 if has_alpha else 3), rows
        return None
    except (OSError, struct.error, zlib.error, IndexError):
        return None


def write_png(path, width, height, rows, channels=3):
    """Writes an 8-bit non-interlaced RGB/RGBA PNG without external libraries."""
    color_type = 2 if channels == 3 else 6
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    raw = b"".join(b"\x00" + row[:width * channels] for row in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)

    blob = SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(blob)


MAX_METRIC_ROWS = 2400
BOMB_PIXELS = 40_000_000
_PILLOW_FAILED = object()
_DECODE_NOTES = {}


def _pillow():
    if os.environ.get("DEBLOAT_NO_PILLOW"):
        return None
    try:
        from PIL import Image  # type: ignore
        return Image
    except Exception:
        return None


def _decode_pillow(path):
    """Fast decoder via Pillow (handles RGB/RGBA/indexed/grayscale/16-bit PNG, JPEG, WebP, BMP).
    Oversized captures (decompression-bomb range) are box-reduced to <= BOMB_PIXELS instead of
    silently dropping into the slow pure-Python decoder; a format Pillow recognised but could not
    decode returns _PILLOW_FAILED so the slow fallbacks are skipped."""
    Image = _pillow()
    if Image is None:
        return None
    old_limit = Image.MAX_IMAGE_PIXELS
    try:
        img = Image.open(path)
    except Image.DecompressionBombError:
        Image.MAX_IMAGE_PIXELS = None
        try:
            img = Image.open(path)
        except Exception:
            return _PILLOW_FAILED
        finally:
            Image.MAX_IMAGE_PIXELS = old_limit
    except Exception:
        return None
    try:
        with img:
            w0, h0 = img.size
            if w0 * h0 > BOMB_PIXELS:
                factor = math.ceil(math.sqrt(w0 * h0 / BOMB_PIXELS))
                img.draft(img.mode, (w0 // factor, h0 // factor))
                img = img.reduce(max(1, math.ceil(img.size[0] / (w0 // factor))))
                _DECODE_NOTES[str(path)] = f"very large capture {w0}x{h0} box-reduced to {img.size[0]}x{img.size[1]}"
            has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            conv = img.convert("RGBA" if has_alpha else "RGB")
            w, h = conv.size
            ch = 4 if has_alpha else 3
            raw = conv.tobytes()
            stride = w * ch
            return w, h, ch, [raw[y * stride:(y + 1) * stride] for y in range(h)]
    except Exception:
        return _PILLOW_FAILED

def _decode_os(path):
    """Converts JPEG/WebP/BMP/unsupported PNG to a temp PNG using built-in OS tools."""
    src = Path(path)
    tmp = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="debloat-img-", suffix=".png")
        os.close(fd)
        tmp = Path(tmp_name)
        env = {**os.environ, "DEBLOAT_IMG_SRC": str(src.resolve()), "DEBLOAT_IMG_DST": str(tmp)}
        if sys.platform == "win32":
            ps = (
                "Add-Type -AssemblyName System.Drawing; "
                "$img = [System.Drawing.Image]::FromFile($env:DEBLOAT_IMG_SRC); "
                "try { $img.Save($env:DEBLOAT_IMG_DST, [System.Drawing.Imaging.ImageFormat]::Png) } "
                "finally { $img.Dispose() }"
            )
            cmd = ["powershell.exe", "-NoProfile", "-Command", ps]
        elif sys.platform == "darwin" and shutil.which("sips"):
            cmd = ["sips", "-s", "format", "png", str(src), "--out", str(tmp)]
        elif shutil.which("magick"):
            cmd = ["magick", str(src), str(tmp)]
        elif shutil.which("ffmpeg"):
            cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), str(tmp)]
        else:
            return None
        res = subprocess.run(cmd, env=env, capture_output=True, timeout=15)
        if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            return None
        return read_png(tmp)
    except Exception:
        return None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)



def read_image(path):
    """Decodes PNG/JPEG/BMP/WebP: prefers Pillow when installed for speed and full format support,
    falling back to the zero-dependency PNG decoder and OS tools."""
    src = Path(path)
    if not src.is_file():
        return None
    decoded = _decode_pillow(src)
    if decoded is _PILLOW_FAILED:
        return None
    if decoded is not None:
        return decoded
    decoded = read_png(src)
    if decoded is not None:
        return decoded
    return _decode_os(src)


def _composite_rows(width, height, channels, rows, matte=ALPHA_MATTE):
    """Returns 3-channel RGB rows with any RGBA alpha composited over an opaque neutral matte."""
    if channels == 3:
        return rows
    Image = _pillow()
    if Image is not None:
        try:
            src = Image.frombytes("RGBA", (width, height), b"".join(r[:width * 4] for r in rows))
            base = Image.new("RGBA", (width, height), matte + (255,))
            raw = Image.alpha_composite(base, src).convert("RGB").tobytes()
            stride = width * 3
            return [raw[y * stride:(y + 1) * stride] for y in range(height)]
        except Exception:
            pass
    mr, mg, mb = matte
    out = []
    for row in rows:
        buf = bytearray(width * 3)
        dst = 0
        for off in range(0, width * 4, 4):
            a = row[off + 3]
            if a == 255:
                buf[dst] = row[off]
                buf[dst + 1] = row[off + 1]
                buf[dst + 2] = row[off + 2]
            elif a == 0:
                buf[dst] = mr
                buf[dst + 1] = mg
                buf[dst + 2] = mb
            else:
                inv = 255 - a
                buf[dst] = (row[off] * a + mr * inv) // 255
                buf[dst + 1] = (row[off + 1] * a + mg * inv) // 255
                buf[dst + 2] = (row[off + 2] * a + mb * inv) // 255
            dst += 3
        out.append(bytes(buf))
    return out


def _box_reduce(width, height, rows3, step):
    """Area-average (box filter) downsample of 3-channel rows by an integer step. Averaging cancels
    ClearType R/G/B fringes and JPEG noise that point sampling turns into fake colour and edges."""
    if step <= 1:
        return width, height, list(rows3)
    dw, dh = max(1, width // step), max(1, height // step)
    Image = _pillow()
    if Image is not None:
        try:
            img = Image.frombytes("RGB", (width, height), b"".join(r[:width * 3] for r in rows3))
            raw = img.reduce(step).crop((0, 0, dw, dh)).tobytes()
            stride = dw * 3
            return dw, dh, [raw[y * stride:(y + 1) * stride] for y in range(dh)]
        except Exception:
            pass
    area = step * step
    out = []
    for by in range(dh):
        acc = [0] * (dw * 3)
        for y in range(by * step, by * step + step):
            row = rows3[y]
            for c in range(3):
                chan = row[c::3]
                for bx in range(dw):
                    acc[bx * 3 + c] += sum(chan[bx * step:(bx + 1) * step])
        out.append(bytes(v // area for v in acc))
    return dw, dh, out


# Perceptual lookups: sRGB -> linear light, relative luminance -> CIE L* (theme-symmetric thresholds).
_SRGB_LIN = [
    (c / 255) / 12.92 if c / 255 <= 0.04045 else (((c / 255) + 0.055) / 1.055) ** 2.4
    for c in range(256)
]
_LSTAR = [
    116 * ((i / 4095) ** (1 / 3)) - 16 if i / 4095 > 0.008856 else 903.3 * (i / 4095)
    for i in range(4096)
]
EDGE_DL = 3.0        # L* step between neighbours that counts as an edge
FG_DL = 2.5          # L* distance from the page background that counts as foreground
HUE_BIN = 30         # degrees per hue bucket for accent islands
CONTROL_MIN_H, CONTROL_MAX_H = 26, 72    # solid control height range in 1x CSS pixels (excludes >=80px KPI cards)
TARGET_MIN_H = 18                        # smallest solid hit-target height to audit (1x CSS px)
HIT_TARGET_MIN_H = 32                    # desktop minimum hit-target height (1x CSS px)
HIT_TARGET_MIN_GAP = 6                   # minimum separation between adjacent controls (1x CSS px)
LEFT_DRIFT_MIN_PX = 3                    # >= 3 CSS px off a rail counts as misalignment (ignores font side-bearings)
LEFT_DRIFT_MAX_PX = 14                   # <= 14 CSS px stays within one rail (16px+ is deliberate indent)
SURFACE_MIN_W, SURFACE_MIN_H = 80, 56    # minimum container size (1x CSS px) for surface nesting


def _rel_lum(r, g, b):
    return 0.2126 * _SRGB_LIN[r] + 0.7152 * _SRGB_LIN[g] + 0.0722 * _SRGB_LIN[b]


def _dilate(mask, dw, dh, rad, rx=None, ry=None):
    """Separable box dilation of a 0/1 bytearray mask by `rad` (or separate `rx`, `ry`) pixels."""
    rx = rad if rx is None else rx
    ry = rad if ry is None else ry
    tmp = bytearray(len(mask))
    if rx > 0:
        for y in range(dh):
            base = y * dw
            pre = [0]
            for x in range(dw):
                pre.append(pre[-1] + mask[base + x])
            for x in range(dw):
                if pre[min(dw, x + rx + 1)] - pre[max(0, x - rx)]:
                    tmp[base + x] = 1
    else:
        tmp[:] = mask
    if ry <= 0:
        return tmp
    out = bytearray(len(mask))
    for x in range(dw):
        pre = [0]
        for y in range(dh):
            pre.append(pre[-1] + tmp[y * dw + x])
        for y in range(dh):
            if pre[min(dh, y + ry + 1)] - pre[max(0, y - ry)]:
                out[y * dw + x] = 1
    return out


def _flood_bbox(seed, dw, dh, visited, match):
    """4-connected flood-fill from `seed`; returns (x0, y0, x1, y1, count, border_mask)."""
    n = dw * dh
    visited[seed] = 1
    stack = [seed]
    cnt = 0
    x0 = x1 = seed % dw
    y0 = y1 = seed // dw
    while stack:
        j = stack.pop()
        cnt += 1
        jx, jy = j % dw, j // dw
        if jx < x0:
            x0 = jx
        elif jx > x1:
            x1 = jx
        if jy < y0:
            y0 = jy
        elif jy > y1:
            y1 = jy
        for k in ((j - 1) if jx > 0 else -1, (j + 1) if jx + 1 < dw else -1, j - dw, j + dw):
            if 0 <= k < n and not visited[k] and match(k):
                visited[k] = 1
                stack.append(k)
    touches = (1 if x0 == 0 else 0) | (2 if x1 == dw - 1 else 0) | (4 if y0 == 0 else 0) | (8 if y1 == dh - 1 else 0)
    return x0, y0, x1, y1, cnt, touches


def _diff_row(ra, rb, y, w, w2, dx, tolerance, hot_cells):
    """Accumulates pixels differing by > tolerance on one aligned row into CELL-sized hot_cells."""
    changed = 0
    cy = y // CELL
    for x in range(w):
        sx = x - dx
        if not (0 <= sx < w2):
            continue
        oa, ob = x * 3, sx * 3
        diff = max(abs(ra[oa] - rb[ob]), abs(ra[oa + 1] - rb[ob + 1]), abs(ra[oa + 2] - rb[ob + 2]))
        if diff > tolerance:
            changed += 1
            key = (x // CELL, cy)
            box = hot_cells.get(key)
            if box is None:
                hot_cells[key] = [x, y, x + 1, y + 1, 1, diff]
            else:
                if x < box[0]:
                    box[0] = x
                if x + 1 > box[2]:
                    box[2] = x + 1
                if y + 1 > box[3]:
                    box[3] = y + 1
                box[4] += 1
                if diff > box[5]:
                    box[5] = diff
    return changed


def _refine_solid_box(rows3, width, height, x0, y0, x1, y1, step, col, tol=16):
    """Refines a coarse metric-grid solid fill box to exact full-resolution capture pixels."""
    r, g, b = col
    sx0 = max(0, x0 * step - step)
    sx1 = min(width, (x1 + 2) * step)
    sy0 = max(0, y0 * step - step)
    sy1 = min(height, (y1 + 2) * step)
    bx0 = max(0, x0 * step)
    bx1 = min(width - 1, (x1 + 1) * step - 1)
    by0 = max(0, y0 * step)
    by1 = min(height - 1, (y1 + 1) * step - 1)
    sample_ys = sorted({by0 + (by1 - by0) * f // 4 for f in (1, 2, 3)})
    fx0, fx1 = width, 0
    for sy in sample_ys:
        row = rows3[sy]
        for x in range(sx0, sx1):
            o = x * 3
            if abs(row[o] - r) <= tol and abs(row[o + 1] - g) <= tol and abs(row[o + 2] - b) <= tol:
                if x < fx0:
                    fx0 = x
                if x + 1 > fx1:
                    fx1 = x + 1
    if fx1 <= fx0:
        fx0, fx1 = bx0, bx1 + 1
    sample_xs = sorted({fx0 + (fx1 - 1 - fx0) * f // 4 for f in (1, 2, 3)})
    fy0, fy1 = height, 0
    for sy in range(sy0, sy1):
        row = rows3[sy]
        for x in sample_xs:
            o = x * 3
            if abs(row[o] - r) <= tol and abs(row[o + 1] - g) <= tol and abs(row[o + 2] - b) <= tol:
                if sy < fy0:
                    fy0 = sy
                if sy + 1 > fy1:
                    fy1 = sy + 1
                break
    if fy1 <= fy0:
        fy0, fy1 = by0, by1 + 1
    return fx0, fy0, fx1, fy1


def _measure_left_alignment(rows3, width, height, dw, dh, step, bg, bg_l, L, R, G, B, edge, dpr):
    """Detects vertical left-edge rails and stacked sections drifting 3..14 CSS px off their rail."""
    n = dw * dh
    br, bgg, bb = bg
    fg_mask = bytearray(n)
    for i in range(n):
        if (edge[i] or abs(L[i] - bg_l) >= FG_DL
                or max(abs(R[i] - br), abs(G[i] - bgg), abs(B[i] - bb)) > 32):
            fg_mask[i] = 1
    rx = max(2, int(round(10 * dpr / step)))
    dil = _dilate(fg_mask, dw, dh, 0, rx=rx, ry=0)
    visited = bytearray(n)
    blocks = []
    min_bw = max(1, int(24 * dpr / step))
    min_bh = max(1, int(4 * dpr / step))
    for i in range(n):
        if not dil[i] or visited[i]:
            continue
        x0, y0, x1, y1, _, _ = _flood_bbox(i, dw, dh, visited, lambda k: dil[k])
        # Re-trim horizontal dilation padding using the raw fg_mask inside the component's bbox.
        rx0, rx1 = dw, -1
        for gy in range(y0, y1 + 1):
            base = gy * dw
            for gx in range(x0, x1 + 1):
                if fg_mask[base + gx]:
                    if gx < rx0:
                        rx0 = gx
                    if gx > rx1:
                        rx1 = gx
        if rx1 < rx0:
            continue
        bw, bh = rx1 - rx0 + 1, y1 - y0 + 1
        if bw < min_bw or bh < min_bh or rx0 == 0:
            continue
        if bw * step >= 0.95 * width:
            continue
        # Refine exact left/right capture-pixel edges from full-res rows3.
        sy0, sy1 = y0 * step, min(height, (y1 + 1) * step)
        ystep = max(1, (sy1 - sy0) // 24)
        lx0, lx1 = max(0, rx0 * step - step), min(width, (rx0 + 2) * step)
        fx0 = width
        for sy in range(sy0, sy1, ystep):
            row = rows3[sy]
            for x in range(lx0, min(lx1, fx0)):
                o = x * 3
                r, g, b = row[o], row[o + 1], row[o + 2]
                if (abs(_LSTAR[int(_rel_lum(r, g, b) * 4095)] - bg_l) >= FG_DL
                        or max(abs(r - br), abs(g - bgg), abs(b - bb)) > 32):
                    fx0 = x
                    break
        if fx0 >= width:
            fx0 = rx0 * step
        fx1 = min(width, (rx1 + 1) * step)
        if fx0 <= 1 or (fx1 - fx0) < 24 * dpr:
            continue
        blocks.append({"x0": fx0, "x1": fx1, "y0": sy0, "y1": sy1, "cx": (fx0 + fx1) / 2.0})
    if len(blocks) < 2:
        return 0, 0, 0.0, []

    # Drop inner blocks vertically/horizontally nested strictly inside an outer container block.
    outer = []
    for idx, b in enumerate(blocks):
        inside = False
        for jdx, o in enumerate(blocks):
            if idx != jdx and o["x0"] <= b["x0"] and o["y0"] <= b["y0"] and o["x1"] >= b["x1"] and o["y1"] >= b["y1"]:
                if (o["x1"] - o["x0"]) * (o["y1"] - o["y0"]) > (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]):
                    inside = True
                    break
        if not inside:
            outer.append(b)
    if len(outer) < 2:
        return 0, 0, 0.0, []

    # Cluster into candidate left rails (consecutive x0 gap <= LEFT_DRIFT_MAX_PX * dpr).
    outer.sort(key=lambda d: (d["x0"], d["y0"]))
    max_gap = LEFT_DRIFT_MAX_PX * dpr
    min_drift = LEFT_DRIFT_MIN_PX * dpr
    center_tol = 4.0 * dpr
    clusters = [[outer[0]]]
    for b in outer[1:]:
        if b["x0"] - clusters[-1][-1]["x0"] <= max_gap and b["x0"] - clusters[-1][0]["x0"] <= 20 * dpr:
            clusters[-1].append(b)
        else:
            clusters.append([b])

    rails = 0
    misaligned = []
    for cl in clusters:
        # Keep only blocks that have at least one vertically disjoint peer in this cluster.
        peers = [
            b for i, b in enumerate(cl)
            if any(b["y1"] <= o["y0"] or o["y1"] <= b["y0"] for j, o in enumerate(cl) if i != j)
        ]
        if len(peers) < 2:
            continue
        # Exclude centered stacks (all peers share one horizontal center axis).
        cxs = sorted(b["cx"] for b in peers)
        med_cx = cxs[len(cxs) // 2]
        if all(abs(b["cx"] - med_cx) <= center_tol for b in peers) and max(b["x0"] for b in peers) - min(b["x0"] for b in peers) >= min_drift:
            continue
        rails += 1
        # Choose dominant rail x0 maximizing aligned peers within +/- 2 CSS px.
        snap_tol = 2.0 * dpr
        best_rx = min(
            {b["x0"] for b in peers},
            key=lambda rx: (-sum(1 for b in peers if abs(b["x0"] - rx) <= snap_tol), rx),
        )
        for b in peers:
            diff = abs(b["x0"] - best_rx)
            if min_drift <= diff <= max_gap:
                misaligned.append({
                    "y": b["y0"],
                    "x": b["x0"],
                    "rail_x": best_rx,
                    "drift_px": round(diff / dpr, 1),
                })
    misaligned.sort(key=lambda d: (-d["drift_px"], d["y"]))
    max_drift = misaligned[0]["drift_px"] if misaligned else 0.0
    return rails, len(misaligned), max_drift, misaligned[:4]


def _measure_surface_nesting(width, height, dw, dh, step, R, G, B, L, edge, dpr):
    """Measures box-inside-box surface nesting depth (both bordered and tonal borderless cards)."""
    n = dw * dh
    surf_edge = bytearray(edge)
    for y in range(1, dh):
        base = y * dw
        for x in range(1, dw):
            i = base + x
            if surf_edge[i]:
                continue
            li = L[i]
            if (abs(li - L[i - 1]) >= 1.2 or abs(li - L[i - dw]) >= 1.2
                    or abs(R[i] - R[i - 1]) >= 4 or abs(G[i] - G[i - 1]) >= 4 or abs(B[i] - B[i - 1]) >= 4
                    or abs(R[i] - R[i - dw]) >= 4 or abs(G[i] - G[i - dw]) >= 4 or abs(B[i] - B[i - dw]) >= 4):
                surf_edge[i] = 1
    visited = bytearray(n)
    boxes = []
    min_w = SURFACE_MIN_W * dpr
    min_h = SURFACE_MIN_H * dpr
    for i in range(n):
        if surf_edge[i] or visited[i]:
            continue
        r0, g0, b0 = R[i], G[i], B[i]
        x0, y0, x1, y1, cnt, touches = _flood_bbox(
            i, dw, dh, visited,
            lambda k, r0=r0, g0=g0, b0=b0: (
                not surf_edge[k] and abs(R[k] - r0) <= 12 and abs(G[k] - g0) <= 12 and abs(B[k] - b0) <= 12
            ),
        )
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        sx0, sy0, sx1, sy1 = x0 * step, y0 * step, (x1 + 1) * step, (y1 + 1) * step
        sw, sh = sx1 - sx0, sy1 - sy0
        if sw < min_w or sh < min_h:
            continue
        if touches == 15 or (sw >= 0.94 * width and sh >= 0.94 * height):
            continue
        if cnt < 24 or cnt < 0.02 * bw * bh:
            continue
        boxes.append((sw * sh, sx0, sy0, sx1, sy1))
    if not boxes:
        return 0, None
    boxes.sort(reverse=True)
    margin = max(6.0 * dpr, 2.0 * step)
    depths = [1] * len(boxes)
    for j in range(len(boxes)):
        aj, jx0, jy0, jx1, jy1 = boxes[j]
        best = 1
        for i in range(j):
            ai, ix0, iy0, ix1, iy1 = boxes[i]
            if (aj <= 0.90 * ai
                    and jx0 >= ix0 + margin and jy0 >= iy0 + margin
                    and jx1 <= ix1 - margin and jy1 <= iy1 - margin):
                if depths[i] + 1 > best:
                    best = depths[i] + 1
        depths[j] = best
    max_d = max(depths)
    idx = depths.index(max_d)
    _, dx0, dy0, dx1, dy1 = boxes[idx]
    deepest = {"x": dx0, "y": dy0, "w": dx1 - dx0, "h": dy1 - dy0, "depth": max_d} if max_d >= 2 else None
    return max_d, deepest


def _sample_stats(width, height, channels, rows, dpr=1.0, viewport_h=None):
    """Box-filtered (<=480px wide) perceptual telemetry:
    1) edge_density_pct: neighbours >= 3 L* apart (or a strong hue step); smooth gradients excluded
    2) spatial_edge_std, empty_tiles, viewport_bands (for long captures) and busiest tiles
    3) accent_islands / accent_hues: >= 50%-filled chromatic cells, split by 30-degree hue bucket
    4) solid_controls / solid_controls_row, plus hit-target telemetry (small_targets <32px, crowded <6px)
    5) contrast_lt3_pct / contrast_lt45_pct: WCAG ratio across each edge (full-resolution)
    6) gradient_area_pct: smooth ramps (gradients, glows, shadow halos) away from any edge
    7) left_rails / misaligned_blocks / max_left_drift_px: stacked-section left-edge alignment
    8) surface_nesting_depth: box-inside-box container nesting (bordered and tonal)
    Plus bg_hex (or "transparent"), colors, luma_buckets and foreground_pct (perceptual)."""
    dpr = max(0.5, float(dpr or 1.0))
    transparent = sum(row[3::4].count(0) for row in rows) if channels == 4 else 0
    transparent_pct = 100.0 * transparent / max(1, width * height)
    rows3 = _composite_rows(width, height, channels, rows)
    step = max(1, math.ceil(width / MAX_METRIC_SIDE), math.ceil(height / MAX_METRIC_ROWS))
    dw, dh, grid = _box_reduce(width, height, rows3, step)
    n = dw * dh
    R, G, B = bytearray(n), bytearray(n), bytearray(n)
    for y, row in enumerate(grid):
        R[y * dw:(y + 1) * dw] = row[0:dw * 3:3]
        G[y * dw:(y + 1) * dw] = row[1:dw * 3:3]
        B[y * dw:(y + 1) * dw] = row[2:dw * 3:3]
    lin = _SRGB_LIN
    Y = [0.2126 * lin[r] + 0.7152 * lin[g] + 0.0722 * lin[b] for r, g, b in zip(R, G, B)]
    L = [_LSTAR[int(v * 4095)] for v in Y]

    # Decide viewport-band tiling for long full-page captures.
    if viewport_h and int(viewport_h) > 0:
        vp_h = int(viewport_h)
        use_vp_bands = height > vp_h
    else:
        vp_h = max(MIN_SIDE, int(round(width * (2.0 if width < 600 * dpr else 0.625))))
        use_vp_bands = height > int(1.5 * vp_h)
    n_vp = max(2, math.ceil(height / vp_h)) if use_vp_bands else 0
    vp_q_edges = [[0, 0, 0, 0] for _ in range(n_vp)]
    vp_q_totals = [[0, 0, 0, 0] for _ in range(n_vp)]
    vp_lt3 = [0] * n_vp
    vp_sampled = [0] * n_vp
    vp_ramp = [0] * n_vp
    vp_fg = [0] * n_vp
    vp_px = [0] * n_vp

    # Background: the modal colour bucket, reported as that bucket's mean colour.
    keys = [((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3) for r, g, b in zip(R, G, B)]
    win = Counter(keys).most_common(1)[0][0]
    sel = [i for i, k in enumerate(keys) if k == win]
    bg = tuple(sum(C[i] for i in sel) // len(sel) for C in (R, G, B))
    bg_l = _LSTAR[int(_rel_lum(*bg) * 4095)]
    coarse_colors = {(R[i], G[i], B[i]) for i in range(0, n, 4)}
    luma_buckets = {int(v) // 6 for v in L}

    # Edges, contrast across edges, tiles.
    edge = bytearray(n)
    tile_edges, tile_totals = [0] * 16, [0] * 16
    edge_rows, edge_cols = set(), set()
    edge_px = lt3 = lt45 = 0
    max_ratio = 1.0
    for y in range(1, dh):
        base = y * dw
        ty = min(3, (y * 4) // dh)
        vbi = min(n_vp - 1, (y * step) // vp_h) if use_vp_bands else -1
        for x in range(1, dw):
            i = base + x
            tx = min(3, (x * 4) // dw)
            tidx = ty * 4 + tx
            tile_totals[tidx] += 1
            if vbi >= 0:
                vp_q_totals[vbi][tx] += 1
            li = L[i]
            dl, du = abs(li - L[i - 1]), abs(li - L[i - dw])
            if dl < EDGE_DL and du < EDGE_DL:
                hl = abs(R[i] - R[i - 1]) + abs(G[i] - G[i - 1]) + abs(B[i] - B[i - 1])
                hu = abs(R[i] - R[i - dw]) + abs(G[i] - G[i - dw]) + abs(B[i] - B[i - dw])
                if hl < 60 and hu < 60:
                    continue
            edge[i] = 1
            edge_px += 1
            tile_edges[tidx] += 1
            if vbi >= 0:
                vp_q_edges[vbi][tx] += 1
            edge_rows.add(y)
            edge_cols.add(x)
    # WCAG contrast at each edge, read from full-resolution pixels (the 3x3 metric-pixel window
    # around the edge): box filtering blends thin glyph strokes, so the metric grid understates it.
    edge_list = [i for i in range(n) if edge[i]]
    stride = max(1, len(edge_list) // 6000)
    sampled = 0
    for i in edge_list[::stride]:
        x, y = i % dw, i // dw
        sx0, sx1 = max(0, (x - 1) * step), min(width, (x + 2) * step)
        lo, hi = 1.0, 0.0
        for sy in range(max(0, (y - 1) * step), min(height, (y + 2) * step)):
            row = rows3[sy]
            for o in range(sx0 * 3, sx1 * 3, 3):
                v = 0.2126 * lin[row[o]] + 0.7152 * lin[row[o + 1]] + 0.0722 * lin[row[o + 2]]
                if v < lo:
                    lo = v
                if v > hi:
                    hi = v
        ratio = (hi + 0.05) / (lo + 0.05)
        sampled += 1
        max_ratio = max(max_ratio, ratio)
        vbi = min(n_vp - 1, (y * step) // vp_h) if use_vp_bands else -1
        if vbi >= 0:
            vp_sampled[vbi] += 1
        if ratio < 4.5:
            lt45 += 1
            if ratio < 3.0:
                lt3 += 1
                if vbi >= 0:
                    vp_lt3[vbi] += 1
    total_inner = max(1, (dw - 1) * (dh - 1))
    edge_density_pct = 100.0 * edge_px / total_inner
    tile_pcts = [(100.0 * tile_edges[i] / tile_totals[i]) if tile_totals[i] else 0.0 for i in range(16)]
    mean_tile = sum(tile_pcts) / 16.0
    spatial_edge_std = math.sqrt(sum((p - mean_tile) ** 2 for p in tile_pcts) / 16.0)
    empty_tiles = sum(1 for p in tile_pcts if p < 0.2)
    busiest = []
    if use_vp_bands:
        vp_tiles = []
        for b in range(n_vp):
            by0, by1 = b * vp_h, min(height, (b + 1) * vp_h)
            for tx in range(4):
                tot = vp_q_totals[b][tx]
                pct = (100.0 * vp_q_edges[b][tx] / tot) if tot else 0.0
                if pct > 0:
                    x0, x1 = (tx * dw) // 4, ((tx + 1) * dw) // 4
                    vp_tiles.append((pct, x0 * step, by0, (x1 - x0) * step, by1 - by0, b + 1))
        vp_tiles.sort(reverse=True)
        for pct, bx, by, bw_, bh_, fold in vp_tiles[:3]:
            busiest.append({"x": bx, "y": by, "w": bw_, "h": bh_, "edge_pct": round(pct, 1), "fold": fold})
    else:
        for idx in sorted(range(16), key=lambda t: -tile_pcts[t])[:3]:
            if tile_pcts[idx] <= 0:
                break
            tx, ty = idx % 4, idx // 4
            x0, x1 = (tx * dw) // 4, ((tx + 1) * dw) // 4
            y0, y1 = (ty * dh) // 4, ((ty + 1) * dh) // 4
            busiest.append({"x": x0 * step, "y": y0 * step, "w": (x1 - x0) * step, "h": (y1 - y0) * step,
                            "edge_pct": round(tile_pcts[idx], 1)})

    # Smooth ramps away from edges: luminance or isoluminant hue gradients, glows and shadow halos.
    near = _dilate(edge, dw, dh, 4)
    ramp = 0
    for y in range(4, dh):
        base = y * dw
        vbi = min(n_vp - 1, (y * step) // vp_h) if use_vp_bands else -1
        for x in range(4, dw):
            i = base + x
            if near[i]:
                continue
            s = max(abs(L[i] - L[i - 4]), abs(L[i] - L[i - 4 * dw]))
            if 0.3 <= s < 12:
                ramp += 1
                if vbi >= 0:
                    vp_ramp[vbi] += 1
                continue
            if x >= 12 and not near[i - 12]:
                ch = abs(R[i] - R[i - 12]) + abs(G[i] - G[i - 12]) + abs(B[i] - B[i - 12])
                if 2 <= ch < 60:
                    ramp += 1
                    if vbi >= 0:
                        vp_ramp[vbi] += 1
                    continue
            if y >= 12 and not near[i - 12 * dw]:
                cv = abs(R[i] - R[i - 12 * dw]) + abs(G[i] - G[i - 12 * dw]) + abs(B[i] - B[i - 12 * dw])
                if 2 <= cv < 60:
                    ramp += 1
                    if vbi >= 0:
                        vp_ramp[vbi] += 1
    gradient_area_pct = 100.0 * ramp / max(1, n)

    # Accent islands: >= 50%-filled 4x4 cells of one hue bucket, 4-connected, >= 0.05% of the image.
    cell_hues = Counter()
    for i in range(n):
        r, g, b = R[i], G[i], B[i]
        mx = r if r >= g and r >= b else (g if g >= b else b)
        mn = r if r <= g and r <= b else (g if g <= b else b)
        chroma = mx - mn
        if chroma < 50:
            continue
        if mx == r:
            hue = ((g - b) / chroma) % 6
        elif mx == g:
            hue = (b - r) / chroma + 2
        else:
            hue = (r - g) / chroma + 4
        cell_hues[((i % dw) // 4, (i // dw) // 4, int(hue * 60) // HUE_BIN % (360 // HUE_BIN))] += 1
    hot = {}
    for (cx, cy, hb), cnt in cell_hues.items():
        if cnt >= 8 and cnt > hot.get((cx, cy), (0, -1))[1]:
            hot[(cx, cy)] = (hb, cnt)
    min_island = max(8, int(0.0005 * n))
    seen = set()
    islands = []
    for cell, (hb, _) in hot.items():
        if cell in seen:
            continue
        stack, px_sum = [cell], 0
        seen.add(cell)
        x0 = x1 = cell[0]
        y0 = y1 = cell[1]
        while stack:
            cx, cy = stack.pop()
            px_sum += hot[(cx, cy)][1]
            x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
            for nb in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if nb not in seen and nb in hot and hot[nb][0] == hb:
                    seen.add(nb)
                    stack.append(nb)
        if px_sum >= min_island:
            islands.append({"hue": hb * HUE_BIN, "x": x0 * 4 * step, "y": y0 * 4 * step,
                            "w": (x1 - x0 + 1) * 4 * step, "h": (y1 - y0 + 1) * 4 * step, "pixels": px_sum})
    islands.sort(key=lambda d: -d["pixels"])

    # Solid controls & hit-target metrics: flat non-background fills refined to full-res pixels.
    visited = bytearray(n)
    controls = []
    all_targets = []
    small_targets = []
    br, bgg, bb = bg
    coarse_min_h = max(12, int(TARGET_MIN_H * dpr) - step)
    coarse_max_h = int(CONTROL_MAX_H * dpr) + step
    for i in range(n):
        if visited[i]:
            continue
        r, g, b = R[i], G[i], B[i]
        if abs(L[i] - bg_l) < 6 and abs(r - br) <= 24 and abs(g - bgg) <= 24 and abs(b - bb) <= 24:
            visited[i] = 1
            continue
        x0, y0, x1, y1, cnt, _ = _flood_bbox(
            i, dw, dh, visited,
            lambda k, r=r, g=g, b=b: abs(R[k] - r) <= 10 and abs(G[k] - g) <= 10 and abs(B[k] - b) <= 10,
        )
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        src_w, src_h = bw * step, bh * step
        if not (coarse_min_h <= src_h <= coarse_max_h and src_w >= 1.3 * src_h
                and src_w <= 0.45 * width and cnt >= 0.55 * bw * bh):
            continue
        fx0, fy0, fx1, fy1 = _refine_solid_box(rows3, width, height, x0, y0, x1, y1, step, (r, g, b))
        fw, fh = fx1 - fx0, fy1 - fy0
        css_w, css_h = fw / dpr, fh / dpr
        if fw < 1.4 * fh or fw > 0.45 * width:
            continue
        box = {"x": fx0, "y": fy0, "w": fw, "h": fh, "css_h": round(css_h, 1), "cy": fy0 + fh / 2.0}
        if CONTROL_MIN_H <= css_h <= CONTROL_MAX_H:
            controls.append(box)
            all_targets.append(box)
            if css_h < HIT_TARGET_MIN_H:
                small_targets.append(box)
        elif TARGET_MIN_H <= css_h < CONTROL_MIN_H and css_w >= max(44.0, 1.8 * css_h) and css_w <= 8.0 * css_h and cnt >= 0.65 * bw * bh:
            all_targets.append(box)
            small_targets.append(box)

    def _max_in_row(items):
        ordered = sorted(items, key=lambda d: (d["cy"], d["x"]))
        best, start = 0, 0
        for idx in range(len(ordered)):
            while ordered[idx]["cy"] - ordered[start]["cy"] > max(ordered[start]["h"], ordered[idx]["h"]) / 2.0:
                start += 1
            best = max(best, idx - start + 1)
        return best

    best_row = _max_in_row(controls)
    # Detect crowded adjacent hit targets (< 6 CSS px horizontal gap on the same row).
    crowded_pairs = []
    row_groups = []
    for t in sorted(all_targets, key=lambda d: (d["cy"], d["x"])):
        placed = False
        for grp in row_groups:
            ref = grp[0]
            if abs(t["cy"] - ref["cy"]) <= max(t["h"], ref["h"]) / 2.0:
                grp.append(t)
                placed = True
                break
        if not placed:
            row_groups.append([t])
    for grp in row_groups:
        grp.sort(key=lambda d: d["x"])
        for c1, c2 in zip(grp, grp[1:]):
            gap_css = (c2["x"] - (c1["x"] + c1["w"])) / dpr
            if 0.0 <= gap_css < HIT_TARGET_MIN_GAP:
                crowded_pairs.append({
                    "x": c1["x"] + c1["w"],
                    "y": min(c1["y"], c2["y"]),
                    "gap_px": round(gap_css, 1),
                })
    min_target_h = round(min(t["css_h"] for t in all_targets), 1) if all_targets else None

    # Left-edge alignment & surface nesting depth.
    left_rails, misaligned_blocks, max_left_drift, misaligned_samples = _measure_left_alignment(
        rows3, width, height, dw, dh, step, bg, bg_l, L, R, G, B, edge, dpr
    )
    surface_nesting_depth, deepest_surface = _measure_surface_nesting(
        width, height, dw, dh, step, R, G, B, L, edge, dpr
    )

    transparent_bg = transparent_pct >= 30.0
    if transparent_bg:
        fg_pct = 100.0 - transparent_pct
        if use_vp_bands:
            for y in range(height):
                vbi = min(n_vp - 1, y // vp_h)
                vp_px[vbi] += width
                vp_fg[vbi] += width - rows[y][3::4].count(0)
    else:
        fg = 0
        for i in range(n):
            vbi = min(n_vp - 1, ((i // dw) * step) // vp_h) if use_vp_bands else -1
            if vbi >= 0:
                vp_px[vbi] += 1
            if abs(L[i] - bg_l) >= FG_DL or max(abs(R[i] - br), abs(G[i] - bgg), abs(B[i] - bb)) > 32:
                fg += 1
                if vbi >= 0:
                    vp_fg[vbi] += 1
        fg_pct = 100.0 * fg / max(1, n)
    effective_fg = fg_pct if edge_density_pct >= 0.05 else 0.0

    viewport_bands = []
    if use_vp_bands:
        for b in range(n_vp):
            by0, by1 = b * vp_h, min(height, (b + 1) * vp_h)
            tot_e = sum(vp_q_totals[b])
            ed_b = (100.0 * sum(vp_q_edges[b]) / tot_e) if tot_e else 0.0
            q_empty = sum(1 for q in range(4) if (100.0 * vp_q_edges[b][q] / vp_q_totals[b][q] if vp_q_totals[b][q] else 0.0) < 0.2)
            band_ctrls = [c for c in controls if by0 <= c["cy"] < by1]
            band_isl = sum(1 for isl in islands if by0 <= isl["y"] + isl["h"] / 2.0 < by1)
            band_px_m = max(1, vp_px[b] if not transparent_bg else (by1 - by0) // step * dw)
            viewport_bands.append({
                "fold": b + 1,
                "y": by0,
                "h": by1 - by0,
                "edge_density_pct": round(ed_b, 2),
                "empty_quarters": q_empty,
                "fg_pct": round((100.0 * vp_fg[b] / max(1, vp_px[b])) if ed_b >= 0.05 else 0.0, 1),
                "gradient_area_pct": round(100.0 * vp_ramp[b] / max(1, band_px_m), 1),
                "contrast_lt3_pct": round(100.0 * vp_lt3[b] / vp_sampled[b], 1) if vp_sampled[b] else 0.0,
                "accent_islands": band_isl,
                "solid_controls_row": _max_in_row(band_ctrls),
            })

    return {
        "colors": len(coarse_colors),
        "luma_buckets": len(luma_buckets),
        "bg": bg,
        "bg_hex": "transparent" if transparent_bg else "#%02x%02x%02x" % bg,
        "transparent_pct": round(transparent_pct, 1),
        "foreground_pct": round(effective_fg, 1),
        "edge_density_pct": round(edge_density_pct, 2),
        "edge_rows": len(edge_rows),
        "edge_cols": len(edge_cols),
        "spatial_edge_std": round(spatial_edge_std, 2),
        "empty_tiles": empty_tiles,
        "busiest_tiles": busiest,
        "accent_islands": len(islands),
        "accent_hues": len({d["hue"] for d in islands}),
        "accent_blobs": islands[:5],
        "solid_controls": len(controls),
        "solid_controls_row": best_row,
        "small_targets": len(small_targets),
        "small_target_boxes": [{"x": t["x"], "y": t["y"], "w": t["w"], "h": t["h"], "css_h": t["css_h"]} for t in small_targets[:4]],
        "min_target_h": min_target_h,
        "crowded_controls": len(crowded_pairs),
        "crowded_pairs": crowded_pairs[:4],
        "left_rails": left_rails,
        "misaligned_blocks": misaligned_blocks,
        "max_left_drift_px": max_left_drift,
        "misaligned_samples": misaligned_samples,
        "surface_nesting_depth": surface_nesting_depth,
        "deepest_surface": deepest_surface,
        "viewport_height": vp_h if use_vp_bands else height,
        "viewport_bands": viewport_bands,
        "contrast_lt3_pct": round(100.0 * lt3 / sampled, 1) if sampled else 0.0,
        "contrast_lt45_pct": round(100.0 * lt45 / sampled, 1) if sampled else 0.0,
        "max_edge_contrast": round(max_ratio, 2),
        "gradient_area_pct": round(gradient_area_pct, 1),
        "metric_step": step,
        "dpr": dpr,
        "has_alpha": channels == 4,
    }


def _inspect_decoded(decoded, path=None, dpr=1.0, viewport_h=None):
    if decoded is None:
        return {"ok": False, "reason": "unreadable or unsupported image format (expected PNG, JPEG, BMP, or WebP)"}
    width, height, channels, rows = decoded
    if width < MIN_SIDE or height < MIN_SIDE:
        return {"ok": False, "width": width, "height": height, "reason": f"image too small ({width}x{height})"}
    stats = _sample_stats(width, height, channels, rows, dpr=dpr, viewport_h=viewport_h)
    # A real UI capture shows structure: edges spread over several rows and columns. A blank page,
    # a smooth gradient or two flat colour bands do not, however many luminance levels they hold.
    ok = stats["edge_density_pct"] >= 0.1 and stats["edge_rows"] >= 3 and stats["edge_cols"] >= 3
    reason = "ok" if ok else "no UI structure (blank, smooth gradient or flat colour bands)"
    warnings = []
    if path is not None:
        note = _DECODE_NOTES.pop(str(Path(path)), None)
        if note:
            warnings.append(note)
        if Path(path).suffix.lower() in (".jpg", ".jpeg"):
            warnings.append("JPEG capture: compression noise inflates edge, colour and gradient metrics; capture PNG")
    if ok and stats["max_edge_contrast"] < 1.5:
        warnings.append("possible loading/skeleton capture: every edge is below 1.5:1 contrast")
    if stats["bg_hex"] == "transparent":
        warnings.append(f"transparent background ({stats['transparent_pct']}% of pixels): fg is the opaque coverage")
    return {"ok": ok, "width": width, "height": height, "reason": reason, "warnings": warnings, **stats}


def inspect_image(path, dpr=1.0, viewport_h=None):
    """Verifies that a UI capture image is readable, sized >= 200px, and not blank, and returns telemetry."""
    return _inspect_decoded(read_image(path), path, dpr=dpr, viewport_h=viewport_h)


def _likely_offset(w, h, rows_a, rows_b):
    """Finds a small global pixel shift (-4..4) in a central patch. A non-zero shift is accepted only
    when it explains the difference: it must at least halve the unshifted error, and the unshifted
    error must not already be near zero (a local edit is not a scroll)."""
    x0, x1 = w // 6, w - w // 6
    y0, y1 = h // 6, h - h // 6
    step = max(2, min(6, (x1 - x0) // 60))
    totals = {}
    samples = 0
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            total = count = 0
            for y in range(y0 + 4, y1 - 4, step):
                ra, rb = rows_a[y], rows_b[y - dy]
                for x in range(x0 + 4, x1 - 4, step):
                    oa, ob = x * 3, (x - dx) * 3
                    total += abs(ra[oa] - rb[ob]) + abs(ra[oa + 1] - rb[ob + 1]) + abs(ra[oa + 2] - rb[ob + 2])
                    count += 1
            totals[(dx, dy)] = total
            samples = count
    if not totals or not samples:
        return (0, 0)
    best = min(totals, key=lambda k: (totals[k], abs(k[0]) + abs(k[1])))
    sad0 = totals[(0, 0)]
    if best == (0, 0) or sad0 / samples < 2 or totals[best] >= 0.5 * sad0:
        return (0, 0)
    return best


def _normalise_scale(w1, h1, rows_a, w2, h2, rows_b):
    """Box-reduces the larger capture when the two differ by an integer DPR factor."""
    for big_first in (True, False):
        bw, bh, sw, sh = (w1, h1, w2, h2) if big_first else (w2, h2, w1, h1)
        if sw and bw % sw == 0 and bw // sw >= 2 and abs(bh - (bw // sw) * sh) <= bw // sw:
            k = bw // sw
            if big_first:
                w1, h1, rows_a = _box_reduce(w1, h1, rows_a, k)
            else:
                w2, h2, rows_b = _box_reduce(w2, h2, rows_b, k)
            return w1, h1, rows_a, w2, h2, rows_b, f"normalised {k}x -> 1x"
    return w1, h1, rows_a, w2, h2, rows_b, None


def _row_signatures(w, rows):
    """Compact horizontal fingerprints (<=64 samples/row) plus coarse mean/activity keys for fast reflow matching."""
    samples = min(w, 64)
    xs = [i * w // samples for i in range(samples)]
    sigs, keys, flat = [], [], []
    for row in rows:
        buf = bytearray(samples * 3)
        sr = sg = sb = 0
        r0, g0, b0 = row[xs[0] * 3], row[xs[0] * 3 + 1], row[xs[0] * 3 + 2]
        active = 0
        for idx, x in enumerate(xs):
            o = x * 3
            r, g, b = row[o], row[o + 1], row[o + 2]
            buf[idx * 3] = r
            buf[idx * 3 + 1] = g
            buf[idx * 3 + 2] = b
            sr += r
            sg += g
            sb += b
            if abs(r - r0) > 8 or abs(g - g0) > 8 or abs(b - b0) > 8:
                active += 1
        sigs.append(bytes(buf))
        keys.append((sr // samples, sg // samples, sb // samples, active))
        flat.append(active == 0)
    return sigs, keys, flat


def _row_sig_diff(sa, sb):
    return sum(abs(a - b) for a, b in zip(sa, sb)) / max(1, len(sa))


def _band_sig_diff(sig_a, y0_a, sig_b, y0_b, bh, stride=2):
    total = 0.0
    cnt = 0
    for k in range(0, bh, stride):
        total += _row_sig_diff(sig_a[y0_a + k], sig_b[y0_b + k])
        cnt += 1
    return total / max(1, cnt)


def _align_reflow_bands(w, h1, rows_a, h2, rows_b, tolerance):
    """Finds monotonic horizontal bands with per-band vertical shifts (reflow after inserting/removing
    a section). Returns a list of {'y0': int, 'y1': int, 'dy': int} segments (in before-image y coords,
    mapping row y -> y + dy in after) when genuine non-zero reflow is found, or None otherwise."""
    if min(h1, h2) < 96:
        return None
    bh = max(24, min(40, min(h1, h2) // 10))
    sig_a, key_a, flat_a = _row_signatures(w, rows_a)
    sig_b, key_b, flat_b = _row_signatures(w, rows_b)
    n_bands = h1 // bh
    if n_bands < 3:
        return None

    band_dy = [None] * n_bands
    claimed_b = bytearray(h2)
    zero_thresh = max(2.0, tolerance * 0.4)
    # Pass 1: anchor bands that already match at dy = 0 so identical repeating stripes never drift.
    for bi in range(n_bands):
        y0, y1 = bi * bh, (bi + 1) * bh
        if y1 > h2:
            continue
        non_flat = sum(1 for y in range(y0, y1) if not flat_a[y])
        if non_flat < max(3, bh // 5):
            continue
        if _band_sig_diff(sig_a, y0, sig_b, y0, bh) <= zero_thresh:
            band_dy[bi] = 0
            for y in range(y0, y1):
                claimed_b[y] = 1

    # Pass 2: search monotonic unclaimed intervals in after for shifted non-trivial bands.
    shift_thresh = min(2.5, max(1.8, tolerance * 0.3))
    unshifted_min = max(4.0, tolerance * 0.6)
    last_dy = None
    for bi in range(n_bands):
        if band_dy[bi] is not None:
            last_dy = band_dy[bi]
            continue
        y0, y1 = bi * bh, (bi + 1) * bh
        non_flat = sum(1 for y in range(y0, y1) if not flat_a[y])
        if non_flat < max(4, bh // 4) or len(set(sig_a[y0:y1])) < 3:
            continue
        unshifted = _band_sig_diff(sig_a, y0, sig_b, y0, bh) if y1 <= h2 else 255.0
        if unshifted <= unshifted_min:
            continue
        # Monotonic search bounds from nearest matched neighbours.
        lo_b = 0
        for p in range(bi - 1, -1, -1):
            if band_dy[p] is not None:
                lo_b = max(0, (p + 1) * bh + band_dy[p])
                break
        hi_b = h2 - bh
        for q in range(bi + 1, n_bands):
            if band_dy[q] is not None:
                hi_b = min(hi_b, q * bh + band_dy[q] - bh)
                break
        if hi_b < lo_b:
            continue
        # Fast path: try continuing the previous non-zero shift first.
        best_yb, best_d = None, shift_thresh + 1.0
        if last_dy is not None and last_dy != 0:
            cand = y0 + last_dy
            if lo_b <= cand <= hi_b and not any(claimed_b[cand:cand + bh]):
                d = _band_sig_diff(sig_a, y0, sig_b, cand, bh)
                if d <= shift_thresh and d <= 0.3 * unshifted:
                    best_yb, best_d = cand, d
        if best_yb is None:
            # Coarse scan every 4 rows filtered by band-average colour/activity key.
            ka_r = sum(key_a[y][0] for y in range(y0, y1, 4))
            ka_g = sum(key_a[y][1] for y in range(y0, y1, 4))
            ka_b = sum(key_a[y][2] for y in range(y0, y1, 4))
            k_cnt = len(range(y0, y1, 4))
            ka = (ka_r // k_cnt, ka_g // k_cnt, ka_b // k_cnt)
            coarse_best = None
            coarse_d = 12.0
            for yb in range(lo_b, hi_b + 1, 4):
                if claimed_b[yb] or claimed_b[yb + bh - 1]:
                    continue
                if abs(key_b[yb][0] - key_a[y0][0]) > 20 and abs(key_b[yb + bh // 2][0] - key_a[y0 + bh // 2][0]) > 20:
                    continue
                kbr = sum(key_b[y][0] for y in range(yb, yb + bh, 4)) // k_cnt
                kbg = sum(key_b[y][1] for y in range(yb, yb + bh, 4)) // k_cnt
                kbb = sum(key_b[y][2] for y in range(yb, yb + bh, 4)) // k_cnt
                if abs(kbr - ka[0]) > 14 or abs(kbg - ka[1]) > 14 or abs(kbb - ka[2]) > 14:
                    continue
                d = _band_sig_diff(sig_a, y0, sig_b, yb, bh, stride=4)
                if d < coarse_d:
                    coarse_d = d
                    coarse_best = yb
            if coarse_best is not None:
                for yb in range(max(lo_b, coarse_best - 4), min(hi_b, coarse_best + 4) + 1):
                    if any(claimed_b[yb:yb + bh]):
                        continue
                    d = _band_sig_diff(sig_a, y0, sig_b, yb, bh, stride=2)
                    if d < best_d and d <= shift_thresh and d <= 0.3 * unshifted:
                        best_d = d
                        best_yb = yb
        if best_yb is not None:
            dy = best_yb - y0
            band_dy[bi] = dy
            last_dy = dy
            for y in range(best_yb, best_yb + bh):
                claimed_b[y] = 1

    # Merge contiguous bands with the same dy.
    segs = []
    for bi, dy in enumerate(band_dy):
        if dy is None:
            continue
        y0, y1 = bi * bh, (bi + 1) * bh
        if segs and segs[-1][2] == dy and segs[-1][1] == y0:
            segs[-1][1] = y1
        else:
            segs.append([y0, y1, dy])
    if not any(dy != 0 and (y1 - y0) >= bh for y0, y1, dy in segs):
        return None

    # Expand each segment row-by-row for 1-pixel boundary precision without crossing neighbours.
    used_a = bytearray(h1)
    used_b = bytearray(h2)
    for y0, y1, dy in segs:
        for y in range(y0, y1):
            used_a[y] = 1
            used_b[y + dy] = 1
    row_tol = max(2.5, tolerance * 0.4)
    for idx, seg in enumerate(segs):
        y0, y1, dy = seg
        prev_end_a = segs[idx - 1][1] if idx > 0 else 0
        next_start_a = segs[idx + 1][0] if idx + 1 < len(segs) else h1
        while y0 > prev_end_a and 0 <= y0 - 1 + dy < h2 and not used_a[y0 - 1] and not used_b[y0 - 1 + dy]:
            if _row_sig_diff(sig_a[y0 - 1], sig_b[y0 - 1 + dy]) <= row_tol:
                y0 -= 1
                used_a[y0] = 1
                used_b[y0 + dy] = 1
            else:
                break
        while y1 < next_start_a and 0 <= y1 + dy < h2 and not used_a[y1] and not used_b[y1 + dy]:
            if _row_sig_diff(sig_a[y1], sig_b[y1 + dy]) <= row_tol:
                used_a[y1] = 1
                used_b[y1 + dy] = 1
                y1 += 1
            else:
                break
        seg[0], seg[1] = y0, y1
    merged = []
    for y0, y1, dy in segs:
        if merged and merged[-1]["dy"] == dy and merged[-1]["y1"] == y0:
            merged[-1]["y1"] = y1
        else:
            merged.append({"y0": y0, "y1": y1, "dy": dy})
    return merged


def compare_images(before_path, after_path, tolerance=8, max_regions=12, dpr=1.0, viewport_h=None):
    """Compares before/after images: returns size delta, global or per-band reflow shifts,
    telemetry deltas, and changed boxes."""
    dec_a = read_image(before_path)
    dec_b = read_image(after_path)
    info_a = _inspect_decoded(dec_a, before_path, dpr=dpr, viewport_h=viewport_h)
    info_b = _inspect_decoded(dec_b, after_path, dpr=dpr, viewport_h=viewport_h)
    if not info_a["ok"] or not info_b["ok"]:
        return {"ok": False, "before": info_a, "after": info_b, "regions": []}
    w1, h1, ch_a, raw_a = dec_a
    w2, h2, ch_b, raw_b = dec_b
    rows_a = _composite_rows(w1, h1, ch_a, raw_a)
    rows_b = _composite_rows(w2, h2, ch_b, raw_b)
    orig_sizes = ((w1, h1), (w2, h2))
    warnings = []
    note = None
    if (w1, h1) != (w2, h2):
        w1, h1, rows_a, w2, h2, rows_b, note = _normalise_scale(w1, h1, rows_a, w2, h2, rows_b)
        if note:
            warnings.append(note)
        elif w1 != w2:
            warnings.append(f"DPR/viewport mismatch ({w1}px vs {w2}px wide): deltas are not comparable; "
                            "capture both at the same viewport, DPR, theme and format")
    w, h = min(w1, w2), min(h1, h2)
    dx, dy = _likely_offset(w, h, rows_a, rows_b) if (w1, h1) == (w2, h2) else (0, 0)
    band_shifts = _align_reflow_bands(w, h1, rows_a, h2, rows_b, tolerance) if (w1 == w2 and (dx, dy) == (0, 0)) else None

    hot_cells = {}  # (cx, cy) -> [min_x, min_y, max_x, max_y, count, max_diff]
    changed_px = 0
    union_px = w1 * h1 + w2 * h2 - w * h
    extra_regions = []
    if band_shifts is not None:
        # Per-band aligned diff: compare aligned bands at their shifted rows; count unmatched
        # before/after gaps as removed/added strips.
        row_map = [None] * h1
        matched_b = bytearray(h2)
        for seg in band_shifts:
            sdy = seg["dy"]
            for y in range(seg["y0"], seg["y1"]):
                row_map[y] = sdy
                matched_b[y + sdy] = 1
        for y in range(h1):
            sdy = row_map[y]
            if sdy is None:
                continue
            ra, rb = rows_a[y], rows_b[y + sdy]
            if ra == rb:
                continue
            changed_px += _diff_row(ra, rb, y, w, w2, 0, tolerance, hot_cells)
        # Unmatched strips in before (removed/replaced) and in after (inserted beyond height).
        y = 0
        while y < h1:
            if row_map[y] is not None:
                y += 1
                continue
            gy0 = y
            while y < h1 and row_map[y] is None:
                y += 1
            strip_px = w1 * (y - gy0)
            changed_px += strip_px
            extra_regions.append((strip_px, 0, gy0, w1, y - gy0, 255))
        # Unmatched strips in after that are not just a 1:1 replacement of an unmatched before strip.
        unmatched_a = sum(1 for m in row_map if m is None)
        unmatched_b_runs = []
        y = 0
        while y < h2:
            if matched_b[y]:
                y += 1
                continue
            gy0 = y
            while y < h2 and not matched_b[y]:
                y += 1
            unmatched_b_runs.append((gy0, y))
        unmatched_b_total = sum(gy1 - gy0 for gy0, gy1 in unmatched_b_runs)
        if unmatched_b_total > unmatched_a:
            extra_h = unmatched_b_total - unmatched_a
            # Attribute excess inserted height to the unmatched after strips.
            rem = extra_h
            for gy0, gy1 in unmatched_b_runs:
                if rem <= 0:
                    break
                take = min(rem, gy1 - gy0)
                strip_px = w2 * take
                changed_px += strip_px
                extra_regions.append((strip_px, 0, gy0, w2, take, 255))
                rem -= take
    else:
        for y in range(h):
            sy = y - dy
            if not (0 <= sy < h2):
                continue
            ra, rb = rows_a[y], rows_b[sy]
            if dx == 0 and w1 == w2 and ra == rb:
                continue
            changed_px += _diff_row(ra, rb, y, w, w2, dx, tolerance, hot_cells)

    seen = set()
    regions = list(extra_regions)
    for cell in hot_cells:
        if cell in seen:
            continue
        stack = [cell]
        seen.add(cell)
        min_x, min_y, max_x, max_y, px_sum, peak = w, h, 0, 0, 0, 0
        while stack:
            cx, cy = stack.pop()
            bx0, by0, bx1, by1, cnt, p_diff = hot_cells[(cx, cy)]
            min_x, min_y = min(min_x, bx0), min(min_y, by0)
            max_x, max_y = max(max_x, bx1), max(max_y, by1)
            px_sum += cnt
            peak = max(peak, p_diff)
            for ny in range(cy - 1, cy + 2):
                for nx in range(cx - 1, cx + 2):
                    if (nx, ny) in hot_cells and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
        regions.append((px_sum, min_x, min_y, max_x - min_x, max_y - min_y, peak))

    # Account for cropped/added area when dimensions change (only when reflow alignment didn't already account for it).
    size_changed = orig_sizes[0] != orig_sizes[1]
    if band_shifts is None and h1 != h2:
        dh_diff = abs(h1 - h2)
        strip_w = w1 if h1 > h2 else w2
        strip_px = strip_w * dh_diff
        changed_px += strip_px
        regions.append((strip_px, 0, h, strip_w, dh_diff, 255))
    if w1 != w2:
        dw_diff = abs(w1 - w2)
        strip_h = h
        strip_px = dw_diff * strip_h
        changed_px += strip_px
        regions.append((strip_px, w, 0, dw_diff, strip_h, 255))

    # Direction-aware warnings: a "cleaner" capture can be one that lost contrast, gained glow,
    # deepened nesting, or introduced cramped/misaligned controls.
    d_edge = info_b["edge_density_pct"] - info_a["edge_density_pct"]
    d_low = info_b["contrast_lt3_pct"] - info_a["contrast_lt3_pct"]
    d_grad = info_b["gradient_area_pct"] - info_a["gradient_area_pct"]
    if d_edge <= -0.5 and d_low >= 5:
        warnings.append(f"edge density fell {d_edge:+.2f} pp but low-contrast edges rose {d_low:+.1f} pp: "
                        "the calmer look may be lost contrast, not less clutter")
    if d_grad >= 5:
        warnings.append(f"gradient/halo area grew {d_grad:+.1f} pp: check for new decorative gradients, glows or shadows")
    if info_b["solid_controls_row"] > info_a["solid_controls_row"] and info_b["solid_controls_row"] >= 2:
        warnings.append(f"solid controls in one row {info_a['solid_controls_row']} -> {info_b['solid_controls_row']}: "
                        "competing filled CTAs")
    if info_b["surface_nesting_depth"] > info_a["surface_nesting_depth"] and info_b["surface_nesting_depth"] > 2:
        warnings.append(f"surface nesting depth grew {info_a['surface_nesting_depth']} -> {info_b['surface_nesting_depth']} (>2 levels)")
    if info_b["small_targets"] > info_a["small_targets"] or info_b["crowded_controls"] > info_a["crowded_controls"]:
        warnings.append(
            f"hit-target regression: <32px targets {info_a['small_targets']} -> {info_b['small_targets']}, "
            f"crowded (<6px) pairs {info_a['crowded_controls']} -> {info_b['crowded_controls']}"
        )
    if info_b["misaligned_blocks"] > info_a["misaligned_blocks"]:
        warnings.append(
            f"left-edge alignment regression: misaligned blocks {info_a['misaligned_blocks']} -> "
            f"{info_b['misaligned_blocks']} (max drift {info_b['max_left_drift_px']}px)"
        )

    regions.sort(reverse=True)
    return {
        "ok": True,
        "width": w,
        "height": h,
        "before_size": orig_sizes[0],
        "after_size": orig_sizes[1],
        "size_changed": size_changed,
        "normalised": note,
        "offset": (dx, dy),
        "band_shifts": band_shifts or [],
        "changed_pct": round(min(100.0, 100.0 * changed_px / max(1, union_px)), 2),
        "density_before": info_a["foreground_pct"],
        "density_after": info_b["foreground_pct"],
        "density_delta": round(info_b["foreground_pct"] - info_a["foreground_pct"], 1),
        "edge_density_before": info_a["edge_density_pct"],
        "edge_density_after": info_b["edge_density_pct"],
        "edge_density_delta": round(d_edge, 2),
        "contrast_lt3_delta": round(d_low, 1),
        "gradient_area_delta": round(d_grad, 1),
        "warnings": warnings,
        "before": info_a,
        "after": info_b,
        "regions": [{"x": x, "y": y, "w": rw, "h": rh, "pixels": cnt, "max_diff": pk}
                    for cnt, x, y, rw, rh, pk in regions[:max_regions]],
    }


def format_inspect(path, info):
    if not info["ok"]:
        return f"FAIL {path}: {info['reason']}"
    lines = [
        f"OK   {path}: {info['width']}x{info['height']}, bg {info['bg_hex']}, "
        f"edge density {info['edge_density_pct']}%, spatial std {info['spatial_edge_std']} "
        f"({info['empty_tiles']}/16 empty tiles), accent islands {info['accent_islands']} "
        f"({info['accent_hues']} hue(s)), solid controls {info['solid_controls']} "
        f"(max {info['solid_controls_row']} in one row), gradient/halo {info['gradient_area_pct']}%, "
        f"low-contrast edges {info['contrast_lt3_pct']}% (<3:1), {info['contrast_lt45_pct']}% (<4.5:1), "
        f"fg {info['foreground_pct']}%"
    ]
    min_h_str = f" (min {info['min_target_h']}px)" if info.get("min_target_h") is not None else ""
    align_str = (
        f"{info['misaligned_blocks']} misaligned (max {info['max_left_drift_px']}px)"
        if info.get("misaligned_blocks") else "aligned"
    )
    lines.append(
        f"  layout: surface nesting depth {info['surface_nesting_depth']}, "
        f"left rails {info['left_rails']} ({align_str}), "
        f"hit targets {info['small_targets']} <32px{min_h_str}, {info['crowded_controls']} crowded (<6px)"
    )
    if info.get("deepest_surface") and info["surface_nesting_depth"] > 2:
        ds = info["deepest_surface"]
        lines.append(f"  deepest nested surface: x={ds['x']} y={ds['y']} {ds['w']}x{ds['h']} (depth {ds['depth']})")
    if info.get("misaligned_samples"):
        lines.append("  left-edge drift: " + "; ".join(
            f"y={m['y']} x={m['x']} vs rail x={m['rail_x']} ({m['drift_px']}px)" for m in info["misaligned_samples"]))
    if info.get("small_target_boxes"):
        lines.append("  small targets (<32px): " + "; ".join(
            f"x={t['x']} y={t['y']} {t['w']}x{t['h']} ({t['css_h']}px)" for t in info["small_target_boxes"]))
    if info.get("crowded_pairs"):
        lines.append("  crowded targets (<6px): " + "; ".join(
            f"x={p['x']} y={p['y']} (gap {p['gap_px']}px)" for p in info["crowded_pairs"]))
    if info.get("viewport_bands"):
        lines.append(f"  viewport bands ({len(info['viewport_bands'])} folds, ~{info['viewport_height']}px each):")
        for vb in info["viewport_bands"]:
            lines.append(
                f"    fold {vb['fold']} (y={vb['y']}..{vb['y'] + vb['h']}): "
                f"edges {vb['edge_density_pct']}%, fg {vb['fg_pct']}%, grad {vb['gradient_area_pct']}%, "
                f"<3:1 {vb['contrast_lt3_pct']}%, islands {vb['accent_islands']}, "
                f"controls_row {vb['solid_controls_row']}, {vb['empty_quarters']}/4 empty"
            )
    if info.get("busiest_tiles"):
        lines.append("  busiest tiles: " + "; ".join(
            (f"fold {t['fold']} " if "fold" in t else "")
            + f"x={t['x']} y={t['y']} {t['w']}x{t['h']} ({t['edge_pct']}% edges)"
            for t in info["busiest_tiles"]))
    if info.get("accent_blobs"):
        lines.append("  accent blobs: " + "; ".join(
            f"hue {b['hue']}-{b['hue'] + HUE_BIN} at x={b['x']} y={b['y']} {b['w']}x{b['h']}" for b in info["accent_blobs"]))
    lines += [f"  WARN {w}" for w in info.get("warnings", [])]
    return "\n".join(lines)


def format_compare(res):
    if not res["ok"]:
        bad = [res["before"], res["after"]]
        reasons = "; ".join(r["reason"] for r in bad if not r["ok"])
        return f"FAIL image compare: {reasons}"
    bw, bh = res["before_size"]
    aw, ah = res["after_size"]
    size_desc = f"{bw}x{bh} -> {aw}x{ah} (size changed)" if res["size_changed"] else f"{bw}x{bh}"
    shift_desc = (
        f"reflow {len(res['band_shifts'])} band(s)"
        if res.get("band_shifts")
        else f"offset dx={res['offset'][0]}, dy={res['offset'][1]}"
    )
    lines = [
        f"Compare {size_desc} ({shift_desc}): "
        f"{res['changed_pct']}% changed, edge density {res['edge_density_before']}% -> {res['edge_density_after']}% "
        f"({res['edge_density_delta']:+.2f} pp), fg {res['density_before']}% -> {res['density_after']}% "
        f"({res['density_delta']:+.1f} pp), low-contrast edges {res['contrast_lt3_delta']:+.1f} pp, "
        f"gradient/halo {res['gradient_area_delta']:+.1f} pp, {len(res['regions'])} region(s)"
    ]
    if res.get("band_shifts"):
        lines.append("  reflow bands: " + ", ".join(
            f"y={s['y0']}..{s['y1']} dy={s['dy']:+d}" for s in res["band_shifts"]))
    lines += [f"  WARN {w}" for w in res.get("warnings", [])]
    for idx, reg in enumerate(res["regions"], 1):
        lines.append(
            f"  region {idx}: x={reg['x']}, y={reg['y']}, {reg['w']}x{reg['h']} "
            f"({reg['pixels']} px, max diff {reg['max_diff']})"
        )
    return "\n".join(lines)


def _canvas(w, h, bg, shapes):
    """Test helper: RGB rows of a `bg` canvas with filled (x0, y0, x1, y1, colour) rectangles."""
    rows = [bytearray(bytes(bg) * w) for _ in range(h)]
    for x0, y0, x1, y1, col in shapes:
        seg = bytes(col) * (x1 - x0)
        for y in range(y0, y1):
            rows[y][x0 * 3:x1 * 3] = seg
    return [bytes(r) for r in rows]


def selftest():
    """Verifies decoding, compositing, structure check, perceptual telemetry, CTA blobs, accent
    islands vs ClearType fringes, contrast, gradients, offset and DPR handling in compare."""
    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="visual-check-"))
    try:
        w, h = 400, 300
        white, ink = (255, 255, 255), (17, 17, 17)
        # 1. Uniform blank image fails
        blank_p = tmp / "blank.png"
        write_png(blank_p, w, h, [bytes([18, 18, 22] * w) for _ in range(h)])
        if inspect_image(blank_p)["ok"]:
            problems.append("uniform blank PNG was accepted")

        # 2. Clean 3-color flat UI passes with 1 accent island and 1 solid control
        flat_p = tmp / "flat.png"
        write_png(flat_p, w, h, _canvas(w, h, white, [(40, 50, 360, 66, ink), (40, 200, 160, 236, (37, 99, 235))]))
        flat_info = inspect_image(flat_p)
        if (not flat_info["ok"] or flat_info["accent_islands"] != 1 or flat_info["edge_density_pct"] <= 0
                or flat_info["solid_controls"] != 1 or flat_info["gradient_area_pct"] > 1):
            problems.append(f"clean 3-color flat UI telemetry wrong: {flat_info!r}")

        # 3. Blank purple gradient fails (no structure) but reports its gradient area
        grad_rows = [
            bytes([int(20 + 180 * y / h), int(15 + 60 * y / h), int(60 + 160 * y / h)] * w)
            for y in range(h)
        ]
        grad_p = tmp / "grad.png"
        write_png(grad_p, w, h, grad_rows)
        grad_info = inspect_image(grad_p)
        if grad_info["ok"] or grad_info["edge_density_pct"] > 0.05 or grad_info["foreground_pct"] > 0.0 \
                or grad_info["gradient_area_pct"] < 50:
            problems.append(f"blank gradient accepted or misread: {grad_info!r}")

        # 3b. Two flat colour bands fail
        bands_p = tmp / "bands.png"
        write_png(bands_p, w, h, _canvas(w, h, white, [(0, 150, w, h, (30, 30, 40))]))
        if inspect_image(bands_p)["ok"]:
            problems.append("two flat colour bands were accepted as a UI capture")

        # 3c. Isoluminant indigo->purple gradient hero registers gradient_area_pct
        iso_rows = [bytearray(bytes(white) * w) for _ in range(h)]
        for y in range(20, 180):
            buf = bytearray((w - 40) * 3)
            for idx in range(w - 40):
                t_ = idx / max(1, w - 41)
                buf[idx * 3] = int(99 + 69 * t_)
                buf[idx * 3 + 1] = int(102 - 17 * t_)
                buf[idx * 3 + 2] = int(241 + 6 * t_)
            iso_rows[y][20 * 3:(w - 20) * 3] = buf
        for y in range(210, 220):
            iso_rows[y][20 * 3:220 * 3] = bytes(ink) * 200
        iso_p = tmp / "iso_grad.png"
        write_png(iso_p, w, h, [bytes(r) for r in iso_rows])
        iso_info = inspect_image(iso_p)
        if not iso_info["ok"] or iso_info["gradient_area_pct"] < 30:
            problems.append(f"isoluminant hue gradient missed: {iso_info!r}")

        # 4. Low-contrast dark nested card grid registers edges (non-zero edge density)
        grid_rows = []
        for y in range(h):
            row = bytearray()
            for x in range(w):
                if (x % 40 == 0) or (y % 40 == 0):
                    row.extend((40, 40, 44))
                elif 20 <= x < 380 and 20 <= y < 280:
                    row.extend((32, 32, 36))
                else:
                    row.extend((24, 24, 28))
            grid_rows.append(bytes(row))
        grid_p = tmp / "grid.png"
        write_png(grid_p, w, h, grid_rows)
        grid_info = inspect_image(grid_p)
        if not grid_info["ok"] or grid_info["edge_density_pct"] <= 1.0:
            problems.append(f"low-contrast dark grid missed edges: {grid_info!r}")

        # 5. Transparent RGBA modal: bg reported transparent, fg = opaque coverage
        rgba_rows = []
        for y in range(h):
            row = bytearray()
            for x in range(w):
                if 100 <= x < 300 and 50 <= y < 250:
                    row.extend((255, 255, 255, 255))
                elif 0 <= x < 40 and 0 <= y < 30:
                    row.extend((17, 17, 17, 255))
                else:
                    row.extend((0, 0, 0, 0))
            rgba_rows.append(bytes(row))
        rgba_p = tmp / "modal_rgba.png"
        write_png(rgba_p, w, h, rgba_rows, channels=4)
        rgba_info = inspect_image(rgba_p)
        if not rgba_info["ok"] or rgba_info["bg_hex"] != "transparent" or not 30 <= rgba_info["foreground_pct"] <= 40:
            problems.append(f"RGBA transparent modal misreported bg/fg: {rgba_info!r}")

        # 6. Indexed-color (color_type=3) valid PNG decodes via zero-dep read_png
        def chunk(k, b):
            return struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b) & 0xffffffff)

        plte = bytes([255, 255, 255, 17, 17, 17, 37, 99, 235])
        ihdr_idx = struct.pack(">IIBBBBB", 240, 220, 8, 3, 0, 0, 0)
        raw_idx = b"".join(
            b"\x00" + bytes([1 if 40 <= x < 200 and 40 <= y < 60 else 0 for x in range(240)])
            for y in range(220)
        )
        idx_p = tmp / "indexed.png"
        idx_p.write_bytes(SIG + chunk(b"IHDR", ihdr_idx) + chunk(b"PLTE", plte) + chunk(b"IDAT", zlib.compress(raw_idx)) + chunk(b"IEND", b""))
        if read_png(idx_p) is None or not inspect_image(idx_p)["ok"]:
            problems.append("indexed PNG (color_type=3) failed to decode or inspect")

        # 7. Vertical shrink compare_images reports size change and ~50% changed
        tall_p, short_p = tmp / "tall.png", tmp / "short.png"
        tall_rows = [
            bytes([255, 255, 255] if (y % 20) else [30, 30, 30]) * 240
            for y in range(600)
        ]
        write_png(tall_p, 240, 600, tall_rows)
        write_png(short_p, 240, 300, tall_rows[:300])
        cmp_h = compare_images(tall_p, short_p)
        fmt = format_compare(cmp_h)
        if not cmp_h["ok"] or not cmp_h["size_changed"] or cmp_h["changed_pct"] < 45.0 or "240x600 -> 240x300" not in fmt:
            problems.append(f"vertical shrink compare failed: {fmt!r} {cmp_h!r}")

        # 8. ClearType-style colour fringes around grey text strokes are not accent islands
        fringe = []
        for y in range(h):
            row = bytearray(bytes(white) * w)
            if any(top <= y < top + 10 for top in range(30, 270, 24)):
                for x in range(20, 380):
                    phase = x % 5
                    col = ((230, 120, 60), (40, 40, 40), (60, 120, 230), white, white)[phase]
                    row[x * 3:x * 3 + 3] = bytes(col)
            fringe.append(bytes(row))
        fringe_p = tmp / "fringe.png"
        write_png(fringe_p, w, h, fringe)
        fringe_info = inspect_image(fringe_p)
        if fringe_info["accent_islands"] != 0:
            problems.append(f"subpixel text fringes counted as accent islands: {fringe_info['accent_islands']}")

        # 9. Competing CTAs: three adjacent hued buttons and three neutral (shadcn default) buttons
        hued = _canvas(w, h, white, [(20, 40, 120, 76, (37, 99, 235)), (120, 40, 220, 76, (124, 58, 237)),
                                     (220, 40, 320, 76, (219, 39, 119)), (20, 150, 380, 160, ink)])
        hued_p = tmp / "hued.png"
        write_png(hued_p, w, h, hued)
        hued_info = inspect_image(hued_p)
        if hued_info["accent_hues"] < 2 or hued_info["accent_islands"] < 3 or hued_info["solid_controls_row"] < 3:
            problems.append(f"adjacent hued CTAs merged or missed: {hued_info!r}")
        neutral = _canvas(w, h, white, [(20, 40, 110, 76, (24, 24, 27)), (130, 40, 220, 76, (24, 24, 27)),
                                        (240, 40, 330, 76, (24, 24, 27)), (20, 150, 380, 160, (120, 120, 120))])
        neutral_p = tmp / "neutral.png"
        write_png(neutral_p, w, h, neutral)
        neutral_info = inspect_image(neutral_p)
        if neutral_info["solid_controls_row"] != 3:
            problems.append(f"neutral solid CTAs not counted: {neutral_info!r}")

        # 10. Low-contrast grey text vs dark text: the contrast histogram separates them
        dark_text = _canvas(w, h, white, [(20, y, 300, y + 4, ink) for y in range(30, 270, 16)])
        pale_text = _canvas(w, h, white, [(20, y, 300, y + 4, (190, 190, 196)) for y in range(30, 270, 16)])
        dark_p, pale_p = tmp / "dark_text.png", tmp / "pale_text.png"
        write_png(dark_p, w, h, dark_text)
        write_png(pale_p, w, h, pale_text)
        dark_info, pale_info = inspect_image(dark_p), inspect_image(pale_p)
        if dark_info["contrast_lt3_pct"] > 10 or pale_info["contrast_lt3_pct"] < 90:
            problems.append(f"contrast histogram wrong: dark {dark_info['contrast_lt3_pct']}% pale {pale_info['contrast_lt3_pct']}%")
        cmp_c = compare_images(dark_p, pale_p)
        if cmp_c["contrast_lt3_delta"] < 50:
            problems.append(f"compare missed the contrast regression: {format_compare(cmp_c)}")

        # 11. Tonal nesting reads alike in light and dark themes (perceptual thresholds)
        light = _canvas(w, h, white, [(100, 75, 300, 225, (244, 244, 245))])
        darkm = _canvas(w, h, (9, 9, 11), [(100, 75, 300, 225, (24, 24, 27))])
        light_p, darkm_p = tmp / "light.png", tmp / "darkm.png"
        write_png(light_p, w, h, light)
        write_png(darkm_p, w, h, darkm)
        li, di = inspect_image(light_p), inspect_image(darkm_p)
        if abs(li["foreground_pct"] - di["foreground_pct"]) > 5 or li["foreground_pct"] < 20:
            problems.append(f"tonal nesting asymmetric: light fg {li['foreground_pct']}% dark fg {di['foreground_pct']}%")

        # 12. A local edit does not produce a spurious global offset or regions above it
        page = _canvas(w, h, white, [(0, 0, w, 40, (24, 24, 27)), (20, 60, 120, 96, (37, 99, 235)),
                                     (20, 120, 380, 124, ink), (20, 140, 380, 144, ink), (20, 160, 380, 164, ink)])
        edited = _canvas(w, h, white, [(0, 0, w, 40, (24, 24, 27)), (20, 60, 120, 96, (37, 99, 235)),
                                       (20, 120, 380, 124, ink), (20, 140, 380, 144, ink), (20, 160, 380, 164, ink),
                                       (20, 200, 200, 230, (219, 39, 119))])
        page_p, edit_p = tmp / "page.png", tmp / "edited.png"
        write_png(page_p, w, h, page)
        write_png(edit_p, w, h, edited)
        cmp_e = compare_images(page_p, edit_p)
        if cmp_e["offset"] != (0, 0) or any(r["y"] < 195 for r in cmp_e["regions"]):
            problems.append(f"local edit produced a spurious offset/regions: {format_compare(cmp_e)}")

        # 13. A 2x capture of the same screen is normalised, not reported as a big change
        page2 = [bytes(b for i in range(0, len(r), 3) for b in r[i:i + 3] * 2) for r in page for _ in range(2)]
        page2_p = tmp / "page2x.png"
        write_png(page2_p, w * 2, h * 2, page2)
        cmp_d = compare_images(page2_p, page_p)
        if cmp_d.get("normalised") != "normalised 2x -> 1x" or cmp_d["changed_pct"] > 1:
            problems.append(f"2x vs 1x capture not normalised: {format_compare(cmp_d)}")

        # 14. Hit-target telemetry: sub-32px buttons and <6px crowded buttons vs clean spacing
        cramped = _canvas(w, h, white, [
            (30, 40, 120, 64, (37, 99, 235)),     # 90x24 (<32px)
            (123, 40, 213, 64, (24, 24, 27)),     # 90x24, 3px gap (<6px)
            (30, 100, 130, 136, (37, 99, 235)),   # 100x36
            (134, 100, 234, 136, (24, 24, 27)),   # 100x36, 4px gap (<6px)
            (30, 180, 350, 190, ink),
        ])
        cramped_p = tmp / "cramped.png"
        write_png(cramped_p, w, h, cramped)
        cramped_info = inspect_image(cramped_p)
        if (cramped_info["small_targets"] != 2 or cramped_info["min_target_h"] != 24.0
                or cramped_info["crowded_controls"] != 2):
            problems.append(f"hit-target telemetry wrong on cramped buttons: {cramped_info!r}")
        if neutral_info["small_targets"] != 0 or neutral_info["crowded_controls"] != 0:
            problems.append(f"clean 36px/20px-gap buttons flagged as cramped: {neutral_info!r}")

        # 15. Left-edge alignment: stacked sections with 6-8px drift vs aligned rail + indented rail + centered stack
        misaligned = _canvas(w, h, white, [
            (40, 30, 300, 46, ink),
            (48, 70, 310, 86, ink),               # +8px drift
            (40, 110, 280, 126, ink),
            (34, 150, 290, 166, ink),             # -6px drift
            (40, 190, 150, 226, (37, 99, 235)),
        ])
        mis_p = tmp / "misaligned.png"
        write_png(mis_p, w, h, misaligned)
        mis_info = inspect_image(mis_p)
        if mis_info["left_rails"] < 1 or mis_info["misaligned_blocks"] != 2 or mis_info["max_left_drift_px"] < 7.5:
            problems.append(f"left-edge drift missed: {mis_info!r}")
        indented_clean = _canvas(w, h, white, [
            (40, 30, 300, 46, ink),
            (40, 70, 280, 86, ink),
            (64, 110, 300, 126, ink),             # 24px deliberate indent -> separate rail
            (64, 150, 290, 166, ink),
        ])
        ind_p = tmp / "indented_clean.png"
        write_png(ind_p, w, h, indented_clean)
        ind_info = inspect_image(ind_p)
        if ind_info["misaligned_blocks"] != 0 or ind_info["left_rails"] != 2:
            problems.append(f"clean indented layout falsely flagged: {ind_info!r}")
        centered_clean = _canvas(w, h, white, [
            (140, 40, 260, 56, ink),              # cx=200
            (132, 80, 268, 96, ink),              # cx=200 (8px narrower left edge, same center)
            (146, 120, 254, 156, (37, 99, 235)),  # cx=200
        ])
        ctr_p = tmp / "centered_clean.png"
        write_png(ctr_p, w, h, centered_clean)
        ctr_info = inspect_image(ctr_p)
        if ctr_info["misaligned_blocks"] != 0:
            problems.append(f"centered stack falsely flagged as left-edge drift: {ctr_info!r}")

        # 16. Surface nesting depth: flat=0, 1 card=1, 3-level nested tonal & bordered cards=3
        if flat_info["surface_nesting_depth"] != 0:
            problems.append(f"flat UI reported surface nesting depth {flat_info['surface_nesting_depth']}")
        if li["surface_nesting_depth"] != 1 or di["surface_nesting_depth"] != 1:
            problems.append(f"single card nesting depth wrong: light={li['surface_nesting_depth']} dark={di['surface_nesting_depth']}")
        nested_tonal = _canvas(w, h, white, [
            (20, 20, 380, 280, (244, 244, 245)),   # depth 1
            (44, 44, 356, 256, (228, 228, 231)),   # depth 2
            (68, 68, 332, 232, (255, 255, 255)),   # depth 3
            (84, 88, 220, 96, ink),
        ])
        nt_p = tmp / "nested_tonal.png"
        write_png(nt_p, w, h, nested_tonal)
        nt_info = inspect_image(nt_p)
        if nt_info["surface_nesting_depth"] != 3 or not nt_info["deepest_surface"]:
            problems.append(f"3-level tonal surface nesting wrong: {nt_info!r}")
        # 3-level bordered cards (same interior fill as page bg, 1px hairline borders)
        b_rows = [bytearray(bytes(white) * w) for _ in range(h)]
        bcol = bytes((212, 212, 216))
        for bx0, by0, bx1, by1 in ((20, 20, 380, 280), (44, 44, 356, 256), (68, 68, 332, 232)):
            b_rows[by0][bx0 * 3:bx1 * 3] = bcol * (bx1 - bx0)
            b_rows[by1 - 1][bx0 * 3:bx1 * 3] = bcol * (bx1 - bx0)
            for by in range(by0, by1):
                b_rows[by][bx0 * 3:(bx0 + 1) * 3] = bcol
                b_rows[by][(bx1 - 1) * 3:bx1 * 3] = bcol
        nb_p = tmp / "nested_bordered.png"
        write_png(nb_p, w, h, [bytes(r) for r in b_rows])
        nb_info = inspect_image(nb_p)
        if nb_info["surface_nesting_depth"] != 3:
            problems.append(f"3-level bordered surface nesting wrong: {nb_info!r}")

        # 17. Per-band vertical reflow alignment: removing a 60px banner slides lower body up without diffing it
        ref_body = [
            (20, 0, 180, 14, ink),
            (20, 22, 340, 28, (100, 100, 110)),
            (20, 44, 140, 80, (37, 99, 235)),
            (180, 50, 360, 74, (24, 24, 27)),
            (20, 100, 380, 108, ink),
            (20, 124, 260, 134, (219, 39, 119)),
            (20, 152, 360, 160, ink),
            (20, 176, 220, 212, (22, 163, 74)),
        ]
        ref_before_shapes = (
            [(0, 0, w, 40, (24, 24, 27)), (20, 12, 120, 26, white)]
            + [(20, 50, 380, 100, (124, 58, 237)), (40, 66, 240, 84, white)]  # 60px banner to delete
            + [(x0, y0 + 120, x1, y1 + 120, c) for x0, y0, x1, y1, c in ref_body]
        )
        ref_after_shapes = (
            [(0, 0, w, 40, (24, 24, 27)), (20, 12, 120, 26, white)]
            + [(x0, y0 + 60, x1, y1 + 60, c) for x0, y0, x1, y1, c in ref_body]
        )
        rb_p, ra_p = tmp / "reflow_before.png", tmp / "reflow_after.png"
        write_png(rb_p, w, 360, _canvas(w, 360, white, ref_before_shapes))
        write_png(ra_p, w, 300, _canvas(w, 300, white, ref_after_shapes))
        cmp_ref = compare_images(rb_p, ra_p)
        if (not cmp_ref["ok"] or not any(s["dy"] == -60 for s in cmp_ref["band_shifts"])
                or cmp_ref["changed_pct"] > 25.0 or any(r["y"] >= 120 for r in cmp_ref["regions"])):
            problems.append(f"per-band reflow alignment failed: {format_compare(cmp_ref)} {cmp_ref['band_shifts']!r}")

        # 18. Long full-page capture produces viewport-band tiles isolating each fold
        fp_w, fp_h = 640, 1200  # nominal 16:10 viewport = 400px -> 3 folds
        fp_rows = [bytearray(bytes(white) * fp_w) for _ in range(fp_h)]
        # Fold 1 (y=0..400): purple gradient hero
        for y in range(20, 360):
            col = bytes((int(60 + 140 * y / 360), int(40 + 60 * y / 360), int(180 + 60 * y / 360))) * (fp_w - 40)
            fp_rows[y][20 * 3:(fp_w - 20) * 3] = col
        # Fold 2 (y=400..800): 3 competing buttons + dense lines
        for x0, x1, col in ((40, 160, (37, 99, 235)), (180, 300, (124, 58, 237)), (320, 440, (219, 39, 119))):
            seg = bytes(col) * (x1 - x0)
            for y in range(440, 476):
                fp_rows[y][x0 * 3:x1 * 3] = seg
        for ly in range(520, 760, 16):
            seg = bytes(ink) * 560
            for y in range(ly, ly + 4):
                fp_rows[y][40 * 3:600 * 3] = seg
        # Fold 3 (y=800..1200): nearly empty dead void with one small footer line
        for y in range(840, 846):
            fp_rows[y][40 * 3:140 * 3] = bytes(ink) * 100
        fp_p = tmp / "fullpage.png"
        write_png(fp_p, fp_w, fp_h, [bytes(r) for r in fp_rows])
        fp_info = inspect_image(fp_p)
        vb = fp_info["viewport_bands"]
        if (len(vb) != 3 or vb[0]["gradient_area_pct"] < 40
                or vb[1]["solid_controls_row"] != 3 or vb[1]["accent_islands"] < 3
                or vb[2]["empty_quarters"] < 2):
            problems.append(f"long full-page viewport bands wrong: {vb!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for p in problems:
        print(f"FAIL    {p}")
    print(f"visual_check selftest: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-image", help="inspect a screenshot and print visual telemetry")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="compare two screenshots")
    parser.add_argument("--dpr", type=float, default=1.0, help="device pixel ratio for pixel thresholds (default 1.0)")
    parser.add_argument("--viewport-height", type=int, default=None, help="viewport fold height in capture px")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.check_image:
        info = inspect_image(args.check_image, dpr=args.dpr, viewport_h=args.viewport_height)
        print(format_inspect(args.check_image, info))
        return 0 if info["ok"] else 1
    if args.compare:
        res = compare_images(args.compare[0], args.compare[1], dpr=args.dpr, viewport_h=args.viewport_height)
        print(format_compare(res))
        return 0 if res["ok"] else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
