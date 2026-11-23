"""Zero-dependency PNG (plus JPEG/WebP/BMP via Pillow or OS converter) verification and before/after region diffing."""
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
MIN_COLORS = 8
CELL = 8


def read_png(path):
    """Decodes an 8-bit non-interlaced RGB or RGBA PNG into (width, height, channels, rows)."""
    data = Path(path).read_bytes()
    if data[:8] != SIG:
        return None
    pos, chunks, header = 8, [], None
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + size
    if not header:
        return None
    width, height, depth, color, _, _, interlace = header
    channels = {2: 3, 6: 4}.get(color)
    if depth != 8 or channels is None or interlace:
        return None
    raw = zlib.decompress(b"".join(chunks))
    stride = width * channels
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filt = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if filt == 1:
                line[i] = (line[i] + left) & 255
            elif filt == 2:
                line[i] = (line[i] + up) & 255
            elif filt == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 255
            elif filt == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        rows.append(bytes(line))
        previous = line
    return width, height, channels, rows


def write_png(path, width, height, rows, channels=3):
    """Writes an 8-bit non-interlaced RGB/RGBA PNG without external libraries."""
    color_type = 2 if channels == 3 else 6
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    raw = b"".join(b"\x00" + row[:width * channels] for row in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)

    blob = SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(blob)


def _sample_stats(width, height, channels, rows):
    """Computes sampled unique colors, dominant quantized background, and foreground fill ratio."""
    coarse_colors = set()
    for y in range(0, height, 16):
        row = rows[y]
        for x in range(0, width, 16):
            off = x * channels
            coarse_colors.add(row[off:off + 3])

    step = max(2, min(8, width // 160))
    buckets = Counter()
    samples = []
    for y in range(0, height, step):
        row = rows[y]
        for x in range(0, width, step):
            off = x * channels
            rgb = (row[off], row[off + 1], row[off + 2])
            samples.append(rgb)
            buckets[(rgb[0] & 0xf8, rgb[1] & 0xf8, rgb[2] & 0xf8)] += 1
    bg = buckets.most_common(1)[0][0] if buckets else (0, 0, 0)
    fg = sum(1 for r, g, b in samples if max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2])) > 16)
    fg_pct = (100.0 * fg / len(samples)) if samples else 0.0
    return {
        "colors": len(coarse_colors),
        "bg": bg,
        "bg_hex": "#%02x%02x%02x" % bg,
        "foreground_pct": round(fg_pct, 1),
    }


def _decode_non_png(path):
    """Decodes JPEG/WebP/BMP via Pillow when installed, or converts to a temp PNG using built-in OS tools."""
    src = Path(path)
    if not src.is_file():
        return None
    try:
        from PIL import Image  # type: ignore
        with Image.open(src) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            raw = rgb.tobytes()
            stride = w * 3
            return w, h, 3, [raw[y * stride:(y + 1) * stride] for y in range(h)]
    except Exception:
        pass
    tmp = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="debloat-img-", suffix=".png")
        os.close(fd)
        tmp = Path(tmp_name)
        env = {**os.environ, "DEBLOAT_IMG_SRC": str(src), "DEBLOAT_IMG_DST": str(tmp)}
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
    """Decodes an 8-bit RGB/RGBA PNG directly, or JPEG/BMP/WebP via Pillow/OS converter."""
    decoded = read_png(path)
    if decoded is not None:
        return decoded
    return _decode_non_png(path)


def inspect_image(path):
    """Verifies that a UI capture image is readable, sized >= 200px, and not blank."""
    decoded = read_image(path)
    if decoded is None:
        return {"ok": False, "reason": "unreadable or unsupported image format (expected 8-bit RGB/RGBA PNG or JPEG/BMP/WebP)"}
    width, height, channels, rows = decoded
    if width < MIN_SIDE or height < MIN_SIDE:
        return {"ok": False, "width": width, "height": height, "reason": f"image too small ({width}x{height})"}
    stats = _sample_stats(width, height, channels, rows)
    ok = stats["colors"] >= MIN_COLORS
    reason = "ok" if ok else f"blank or uniform image (only {stats['colors']} sampled colors)"
    return {"ok": ok, "width": width, "height": height, "reason": reason, **stats}


def _likely_offset(w, h, ch_a, rows_a, ch_b, rows_b):
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
                    oa, ob = x * ch_a, (x - dx) * ch_b
                    total += abs(ra[oa] - rb[ob]) + abs(ra[oa + 1] - rb[ob + 1]) + abs(ra[oa + 2] - rb[ob + 2])
            if best is None or total < best[0]:
                best = (total, dx, dy)
    return (best[1], best[2]) if best else (0, 0)


def compare_images(before_path, after_path, tolerance=8, max_regions=12):
    """Compares before/after PNGs: returns global shift, visual density delta, and changed bounding boxes."""
    info_a = inspect_image(before_path)
    info_b = inspect_image(after_path)
    if not info_a["ok"] or not info_b["ok"]:
        return {"ok": False, "before": info_a, "after": info_b, "regions": []}
    w1, h1, ch_a, rows_a = read_image(before_path)
    w2, h2, ch_b, rows_b = read_image(after_path)
    w, h = min(w1, w2), min(h1, h2)
    dx, dy = _likely_offset(w, h, ch_a, rows_a, ch_b, rows_b) if (w1, h1) == (w2, h2) else (0, 0)

    cw, ch = (w + CELL - 1) // CELL, (h + CELL - 1) // CELL
    hot_cells = {}  # (cx, cy) -> [min_x, min_y, max_x, max_y, count, max_diff]
    changed_px = 0
    total_px = w * h
    for y in range(h):
        sy = y - dy
        if not (0 <= sy < h2):
            continue
        ra, rb = rows_a[y], rows_b[sy]
        if dx == 0 and ch_a == ch_b and w1 == w2 and ra == rb:
            continue
        cy = y // CELL
        for x in range(w):
            sx = x - dx
            if not (0 <= sx < w2):
                continue
            oa, ob = x * ch_a, sx * ch_b
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
    regions.sort(reverse=True)
    return {
        "ok": True,
        "width": w,
        "height": h,
        "size_changed": (w1, h1) != (w2, h2),
        "offset": (dx, dy),
        "changed_pct": round(100.0 * changed_px / max(1, total_px), 2),
        "density_before": info_a["foreground_pct"],
        "density_after": info_b["foreground_pct"],
        "density_delta": round(info_b["foreground_pct"] - info_a["foreground_pct"], 1),
        "before": info_a,
        "after": info_b,
        "regions": [{"x": x, "y": y, "w": rw, "h": rh, "pixels": cnt, "max_diff": pk}
                    for cnt, x, y, rw, rh, pk in regions[:max_regions]],
    }


def format_inspect(path, info):
    if not info["ok"]:
        return f"FAIL {path}: {info['reason']}"
    return (
        f"OK   {path}: {info['width']}x{info['height']}, {info['colors']} colors, "
        f"bg {info['bg_hex']}, visual density {info['foreground_pct']}%"
    )


def format_compare(res):
    if not res["ok"]:
        bad = [res["before"], res["after"]]
        reasons = "; ".join(r["reason"] for r in bad if not r["ok"])
        return f"FAIL image compare: {reasons}"
    lines = [
        f"Compare {res['width']}x{res['height']} (offset dx={res['offset'][0]}, dy={res['offset'][1]}): "
        f"{res['changed_pct']}% changed, visual density {res['density_before']}% -> {res['density_after']}% "
        f"({res['density_delta']:+.1f} pp), {len(res['regions'])} region(s)"
    ]
    for idx, reg in enumerate(res["regions"], 1):
        lines.append(f"  region {idx}: x={reg['x']}, y={reg['y']}, {reg['w']}x{reg['h']} ({reg['pixels']} px, max diff {reg['max_diff']})")
    return "\n".join(lines)
