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


def _decode_pillow(path):
    """Fast decoder via Pillow (handles RGB/RGBA/indexed/grayscale/16-bit PNG, JPEG, WebP, BMP)."""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    try:
        with Image.open(path) as img:
            has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            conv = img.convert("RGBA" if has_alpha else "RGB")
            w, h = conv.size
            ch = 4 if has_alpha else 3
            raw = conv.tobytes()
            stride = w * ch
            return w, h, ch, [raw[y * stride:(y + 1) * stride] for y in range(h)]
    except Exception:
        return None


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
    if decoded is not None:
        return decoded
    decoded = read_png(src)
    if decoded is not None:
        return decoded
    return _decode_os(src)


def _blend_pixel(r, g, b, a, matte=ALPHA_MATTE):
    if a >= 255:
        return r, g, b
    if a <= 0:
        return matte
    inv = 255 - a
    return (
        (r * a + matte[0] * inv) // 255,
        (g * a + matte[1] * inv) // 255,
        (b * a + matte[2] * inv) // 255,
    )


def _composite_rows(width, height, channels, rows, matte=ALPHA_MATTE):
    """Returns 3-channel RGB rows with any RGBA alpha composited over an opaque neutral matte."""
    if channels == 3:
        return rows
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


def _downsample_rgb(width, height, channels, rows, max_side=MAX_METRIC_SIDE):
    """Downsamples to <= max_side on longest edge with RGBA composited over ALPHA_MATTE.
    Returns (dw, dh, grid_rows, opaque_samples) where grid_rows are 3-byte-per-pixel bytes."""
    step = max(1, math.ceil(max(width, height) / max_side))
    xs = list(range(0, width, step))
    ys = list(range(0, height, step))
    dw, dh = len(xs), len(ys)
    grid = []
    opaque = []
    mr, mg, mb = ALPHA_MATTE
    for y in ys:
        row = rows[y]
        buf = bytearray(dw * 3)
        dst = 0
        if channels == 3:
            for x in xs:
                off = x * 3
                r, g, b = row[off], row[off + 1], row[off + 2]
                buf[dst] = r
                buf[dst + 1] = g
                buf[dst + 2] = b
                opaque.append((r, g, b))
                dst += 3
        else:
            for x in xs:
                off = x * 4
                r, g, b, a = row[off], row[off + 1], row[off + 2], row[off + 3]
                if a >= 16:
                    opaque.append((r, g, b))
                if a == 255:
                    buf[dst], buf[dst + 1], buf[dst + 2] = r, g, b
                elif a == 0:
                    buf[dst], buf[dst + 1], buf[dst + 2] = mr, mg, mb
                else:
                    inv = 255 - a
                    buf[dst] = (r * a + mr * inv) // 255
                    buf[dst + 1] = (g * a + mg * inv) // 255
                    buf[dst + 2] = (b * a + mb * inv) // 255
                dst += 3
        grid.append(bytes(buf))
    return dw, dh, grid, opaque


def _sample_stats(width, height, channels, rows):
    """Computes fast downsampled (<=480px) telemetry:
    1) edge_density_pct: |dx|+|dy| local edge energy (immune to smooth gradients, catches low-contrast borders)
    2) spatial_edge_std & empty_tiles: 4x4 grid edge-density balance variance
    3) accent_islands: spatially separated high-saturation chromatic blobs
    Plus opaque bg_hex, colors, luma_buckets, and foreground_pct.
    """
    dw, dh, grid, opaque = _downsample_rgb(width, height, channels, rows)

    # Background & color diversity from opaque pixels (falling back to composited grid if all transparent).
    sample_pool = opaque if opaque else [
        (row[o], row[o + 1], row[o + 2]) for row in grid for o in range(0, dw * 3, 3)
    ]
    buckets = Counter()
    coarse_colors = set()
    luma_buckets = set()
    for idx, (r, g, b) in enumerate(sample_pool):
        buckets[(r & 0xf8, g & 0xf8, b & 0xf8)] += 1
        if idx % 4 == 0:
            coarse_colors.add((r, g, b))
        luma = (77 * r + 150 * g + 29 * b) >> 8
        luma_buckets.add(luma >> 4)
    # Include composited luma buckets too so a solid white modal on transparent canvas has 2 buckets.
    if channels == 4 and len(opaque) < dw * dh:
        matte_luma = (77 * ALPHA_MATTE[0] + 150 * ALPHA_MATTE[1] + 29 * ALPHA_MATTE[2]) >> 8
        luma_buckets.add(matte_luma >> 4)

    bg = buckets.most_common(1)[0][0] if buckets else ALPHA_MATTE
    fg = sum(1 for r, g, b in sample_pool if max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2])) > 16)
    fg_pct = (100.0 * fg / len(sample_pool)) if sample_pool else 0.0

    # 1) Edge-energy density + 2) 4x4 spatial edge balance + 3) chromatic accent cells.
    tile_edges = [0] * 16
    tile_totals = [0] * 16
    edge_px = 0
    total_inner = max(1, (dw - 1) * (dh - 1))
    accent_cells = Counter()  # (cx, cy) on 4x4 downsampled cells

    for y in range(dh):
        row = grid[y]
        prev = grid[y - 1] if y > 0 else None
        ty = min(3, (y * 4) // max(1, dh))
        cy = y // 4
        for x in range(dw):
            off = x * 3
            r, g, b = row[off], row[off + 1], row[off + 2]
            # High-saturation chromatic pixel check
            mx = r if r >= g and r >= b else (g if g >= b else b)
            mn = r if r <= g and r <= b else (g if g <= b else b)
            chroma = mx - mn
            if mx >= 48 and chroma >= 45 and chroma * 100 >= mx * 45:
                accent_cells[(x // 4, cy)] += 1
            if x == 0 or prev is None:
                continue
            tx = min(3, (x * 4) // max(1, dw))
            tidx = ty * 4 + tx
            tile_totals[tidx] += 1
            dl = abs(r - row[off - 3]) + abs(g - row[off - 2]) + abs(b - row[off - 1])
            du = abs(r - prev[off]) + abs(g - prev[off + 1]) + abs(b - prev[off + 2])
            if dl >= 24 or du >= 24:
                edge_px += 1
                tile_edges[tidx] += 1

    edge_density_pct = 100.0 * edge_px / total_inner
    tile_pcts = [
        (100.0 * tile_edges[i] / tile_totals[i]) if tile_totals[i] else 0.0
        for i in range(16)
    ]
    mean_tile = sum(tile_pcts) / 16.0
    spatial_edge_std = math.sqrt(sum((p - mean_tile) ** 2 for p in tile_pcts) / 16.0)
    empty_tiles = sum(1 for p in tile_pcts if p < 0.2)

    # Connected components of chromatic accent cells (each island >= 4 accent pixels).
    hot_accent = {cell for cell, cnt in accent_cells.items() if cnt >= 2}
    seen = set()
    accent_islands = 0
    for cell in hot_accent:
        if cell in seen:
            continue
        stack = [cell]
        seen.add(cell)
        px_sum = 0
        while stack:
            cx, cy = stack.pop()
            px_sum += accent_cells[(cx, cy)]
            for ny in range(cy - 1, cy + 2):
                for nx in range(cx - 1, cx + 2):
                    if (nx, ny) in hot_accent and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
        if px_sum >= 4:
            accent_islands += 1

    # Smooth gradient blank screen: zero edge energy despite many luma buckets -> foreground_pct should
    # not falsely claim 81% UI density; keep raw_fg_pct as foreground_pct only when edges exist.
    effective_fg = fg_pct if edge_density_pct >= 0.05 else 0.0
    return {
        "colors": len(coarse_colors),
        "luma_buckets": len(luma_buckets),
        "bg": bg,
        "bg_hex": "#%02x%02x%02x" % bg,
        "foreground_pct": round(effective_fg, 1),
        "edge_density_pct": round(edge_density_pct, 2),
        "spatial_edge_std": round(spatial_edge_std, 2),
        "empty_tiles": empty_tiles,
        "accent_islands": accent_islands,
        "has_alpha": channels == 4,
    }


def _inspect_decoded(decoded):
    if decoded is None:
        return {"ok": False, "reason": "unreadable or unsupported image format (expected PNG, JPEG, BMP, or WebP)"}
    width, height, channels, rows = decoded
    if width < MIN_SIDE or height < MIN_SIDE:
        return {"ok": False, "width": width, "height": height, "reason": f"image too small ({width}x{height})"}
    stats = _sample_stats(width, height, channels, rows)
    # Valid non-blank image: either has visible edges (catches clean 2-3 color flat UIs and subtle
    # dark-mode grids) OR has >= 2 distinct luminance buckets (e.g. soft ambient capture).
    ok = stats["edge_density_pct"] >= 0.05 or stats["luma_buckets"] >= 2
    reason = "ok" if ok else "blank or uniform image (no edges and single luminance band)"
    return {"ok": ok, "width": width, "height": height, "reason": reason, **stats}


def inspect_image(path):
    """Verifies that a UI capture image is readable, sized >= 200px, and not blank, and returns telemetry."""
    return _inspect_decoded(read_image(path))


def _likely_offset(w, h, rows_a, rows_b):
    """Finds small global pixel shift (-4..4) in a central patch to isolate alignment shifts."""
    x0, x1 = w // 6, w - w // 6
    y0, y1 = h // 6, h - h // 6
    step = max(2, min(6, (x1 - x0) // 60))
    best = None
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            total = 0
            for y in range(y0 + 4, y1 - 4, step):
                ra, rb = rows_a[y], rows_b[y - dy]
                for x in range(x0 + 4, x1 - 4, step):
                    oa, ob = x * 3, (x - dx) * 3
                    total += abs(ra[oa] - rb[ob]) + abs(ra[oa + 1] - rb[ob + 1]) + abs(ra[oa + 2] - rb[ob + 2])
            if best is None or total < best[0]:
                best = (total, dx, dy)
    return (best[1], best[2]) if best else (0, 0)


def compare_images(before_path, after_path, tolerance=8, max_regions=12):
    """Compares before/after images: returns size delta, global shift, telemetry deltas, and changed boxes."""
    dec_a = read_image(before_path)
    dec_b = read_image(after_path)
    info_a = _inspect_decoded(dec_a)
    info_b = _inspect_decoded(dec_b)
    if not info_a["ok"] or not info_b["ok"]:
        return {"ok": False, "before": info_a, "after": info_b, "regions": []}
    w1, h1, ch_a, raw_a = dec_a
    w2, h2, ch_b, raw_b = dec_b
    rows_a = _composite_rows(w1, h1, ch_a, raw_a)
    rows_b = _composite_rows(w2, h2, ch_b, raw_b)
    w, h = min(w1, w2), min(h1, h2)
    dx, dy = _likely_offset(w, h, rows_a, rows_b) if (w1, h1) == (w2, h2) else (0, 0)

    hot_cells = {}  # (cx, cy) -> [min_x, min_y, max_x, max_y, count, max_diff]
    changed_px = 0
    union_px = w1 * h1 + w2 * h2 - w * h
    for y in range(h):
        sy = y - dy
        if not (0 <= sy < h2):
            continue
        ra, rb = rows_a[y], rows_b[sy]
        if dx == 0 and w1 == w2 and ra == rb:
            continue
        cy = y // CELL
        for x in range(w):
            sx = x - dx
            if not (0 <= sx < w2):
                continue
            oa, ob = x * 3, sx * 3
            diff = max(abs(ra[oa] - rb[ob]), abs(ra[oa + 1] - rb[ob + 1]), abs(ra[oa + 2] - rb[ob + 2]))
            if diff > tolerance:
                changed_px += 1
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

    seen = set()
    regions = []
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

    # Account for cropped/added area when dimensions change (e.g. vertical debloat shrinking modal height).
    size_changed = (w1, h1) != (w2, h2)
    if h1 != h2:
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

    regions.sort(reverse=True)
    return {
        "ok": True,
        "width": w,
        "height": h,
        "before_size": (w1, h1),
        "after_size": (w2, h2),
        "size_changed": size_changed,
        "offset": (dx, dy),
        "changed_pct": round(100.0 * changed_px / max(1, union_px), 2),
        "density_before": info_a["foreground_pct"],
        "density_after": info_b["foreground_pct"],
        "density_delta": round(info_b["foreground_pct"] - info_a["foreground_pct"], 1),
        "edge_density_before": info_a["edge_density_pct"],
        "edge_density_after": info_b["edge_density_pct"],
        "edge_density_delta": round(info_b["edge_density_pct"] - info_a["edge_density_pct"], 2),
        "before": info_a,
        "after": info_b,
        "regions": [{"x": x, "y": y, "w": rw, "h": rh, "pixels": cnt, "max_diff": pk}
                    for cnt, x, y, rw, rh, pk in regions[:max_regions]],
    }


def format_inspect(path, info):
    if not info["ok"]:
        return f"FAIL {path}: {info['reason']}"
    return (
        f"OK   {path}: {info['width']}x{info['height']}, bg {info['bg_hex']}, "
        f"edge density {info['edge_density_pct']}%, spatial std {info['spatial_edge_std']} "
        f"({info['empty_tiles']}/16 empty tiles), accent islands {info['accent_islands']}, "
        f"fg {info['foreground_pct']}%"
    )


def format_compare(res):
    if not res["ok"]:
        bad = [res["before"], res["after"]]
        reasons = "; ".join(r["reason"] for r in bad if not r["ok"])
        return f"FAIL image compare: {reasons}"
    bw, bh = res["before_size"]
    aw, ah = res["after_size"]
    size_desc = f"{bw}x{bh} -> {aw}x{ah} (size changed)" if res["size_changed"] else f"{bw}x{bh}"
    lines = [
        f"Compare {size_desc} (offset dx={res['offset'][0]}, dy={res['offset'][1]}): "
        f"{res['changed_pct']}% changed, edge density {res['edge_density_before']}% -> {res['edge_density_after']}% "
        f"({res['edge_density_delta']:+.2f} pp), fg {res['density_before']}% -> {res['density_after']}% "
        f"({res['density_delta']:+.1f} pp), {len(res['regions'])} region(s)"
    ]
    for idx, reg in enumerate(res["regions"], 1):
        lines.append(
            f"  region {idx}: x={reg['x']}, y={reg['y']}, {reg['w']}x{reg['h']} "
            f"({reg['pixels']} px, max diff {reg['max_diff']})"
        )
    return "\n".join(lines)


def selftest():
    """Verifies decoder, RGBA compositing, blank/flat/gradient telemetry, and size-change diffing."""
    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="visual-check-"))
    try:
        w, h = 400, 300
        # 1. Uniform blank image fails
        blank_p = tmp / "blank.png"
        write_png(blank_p, w, h, [bytes([18, 18, 22] * w) for _ in range(h)])
        if inspect_image(blank_p)["ok"]:
            problems.append("uniform blank PNG was accepted")

        # 2. Clean 3-color flat UI passes with 1 accent island
        flat_rows = []
        for y in range(h):
            row = bytearray()
            for x in range(w):
                if 40 <= x < 360 and 50 <= y < 66:
                    row.extend((17, 17, 17))
                elif 40 <= x < 160 and 200 <= y < 236:
                    row.extend((37, 99, 235))
                else:
                    row.extend((255, 255, 255))
            flat_rows.append(bytes(row))
        flat_p = tmp / "flat.png"
        write_png(flat_p, w, h, flat_rows)
        flat_info = inspect_image(flat_p)
        if not flat_info["ok"] or flat_info["accent_islands"] != 1 or flat_info["edge_density_pct"] <= 0:
            problems.append(f"clean 3-color flat UI telemetry wrong: {flat_info!r}")

        # 3. Blank purple gradient does not fake 81% visual density
        grad_rows = [
            bytes([int(20 + 180 * y / h), int(15 + 60 * y / h), int(60 + 160 * y / h)] * w)
            for y in range(h)
        ]
        grad_p = tmp / "grad.png"
        write_png(grad_p, w, h, grad_rows)
        grad_info = inspect_image(grad_p)
        if grad_info["edge_density_pct"] > 0.05 or grad_info["foreground_pct"] > 0.0:
            problems.append(f"blank gradient faked high density: {grad_info!r}")

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

        # 5. Transparent RGBA modal does not turn transparent pixels into #000000 bg
        rgba_rows = []
        for y in range(h):
            row = bytearray()
            for x in range(w):
                if 100 <= x < 300 and 50 <= y < 250:
                    row.extend((255, 255, 255, 255))
                else:
                    row.extend((0, 0, 0, 0))
            rgba_rows.append(bytes(row))
        rgba_p = tmp / "modal_rgba.png"
        write_png(rgba_p, w, h, rgba_rows, channels=4)
        rgba_info = inspect_image(rgba_p)
        if not rgba_info["ok"] or rgba_info["bg_hex"] == "#000000":
            problems.append(f"RGBA transparent modal misreported bg/ok: {rgba_info!r}")

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
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.check_image:
        info = inspect_image(args.check_image)
        print(format_inspect(args.check_image, info))
        return 0 if info["ok"] else 1
    if args.compare:
        res = compare_images(args.compare[0], args.compare[1])
        print(format_compare(res))
        return 0 if res["ok"] else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
