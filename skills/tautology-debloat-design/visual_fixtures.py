"""Synthetic UI screenshot generator for calibrating and verifying visual_check.py telemetry.

Generates light and dark 1440x900 (plus 1440x2700 full-page and reflow pairs) PNG fixtures
without external dependencies so binary screenshots never bloat the repository.

Usage:
    python visual_fixtures.py --calibrate
    python visual_fixtures.py --out ./tmp-fixtures
    python visual_fixtures.py --out ./tmp-fixtures --only clean gradient_hero
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visual_check  # noqa: E402

W, H = 1440, 900

THEMES = {
    "light": {
        "bg": (255, 255, 255),
        "surface": (244, 244, 245),
        "surface2": (228, 228, 231),
        "surface3": (212, 212, 216),
        "border": (212, 212, 216),
        "divider": (228, 228, 231),
        "ink": (24, 24, 27),
        "muted": (113, 113, 122),
        "faded": (200, 200, 206),
        "btn_neutral": (24, 24, 27),
        "btn_text": (255, 255, 255),
        "accent": (37, 99, 235),
        "accent2": (124, 58, 237),
        "accent3": (219, 39, 119),
        "green": (22, 163, 74),
        "amber": (217, 119, 6),
        "cyan": (8, 145, 178),
        "grad_a": (99, 102, 241),
        "grad_b": (168, 85, 247),
        "grad_c": (6, 182, 212),
    },
    "dark": {
        "bg": (9, 9, 11),
        "surface": (24, 24, 27),
        "surface2": (39, 39, 42),
        "surface3": (56, 56, 62),
        "border": (48, 48, 54),
        "divider": (39, 39, 42),
        "ink": (244, 244, 245),
        "muted": (161, 161, 170),
        "faded": (44, 44, 50),
        "btn_neutral": (244, 244, 245),
        "btn_text": (9, 9, 11),
        "accent": (59, 130, 246),
        "accent2": (139, 92, 246),
        "accent3": (236, 72, 153),
        "green": (34, 197, 94),
        "amber": (245, 158, 11),
        "cyan": (6, 182, 212),
        "grad_a": (49, 46, 129),
        "grad_b": (107, 33, 168),
        "grad_c": (14, 116, 144),
    },
}


class Canvas:
    """Fast pure-Python RGB canvas backed by mutable row bytearrays."""

    def __init__(self, width, height, bg):
        self.w = width
        self.h = height
        row_tpl = bytes(bg) * width
        self.rows = [bytearray(row_tpl) for _ in range(height)]

    def fill(self, x0, y0, x1, y1, col):
        x0, x1 = max(0, int(x0)), min(self.w, int(x1))
        y0, y1 = max(0, int(y0)), min(self.h, int(y1))
        if x1 <= x0 or y1 <= y0:
            return
        seg = bytes(col) * (x1 - x0)
        s0, s1 = x0 * 3, x1 * 3
        for y in range(y0, y1):
            self.rows[y][s0:s1] = seg

    def stroke(self, x0, y0, x1, y1, col, thickness=1):
        t = max(1, int(thickness))
        self.fill(x0, y0, x1, y0 + t, col)
        self.fill(x0, y1 - t, x1, y1, col)
        self.fill(x0, y0, x0 + t, y1, col)
        self.fill(x1 - t, y0, x1, y1, col)

    def hline(self, x0, x1, y, col, thickness=1):
        self.fill(x0, y, x1, y + thickness, col)

    def gradient(self, x0, y0, x1, y1, col_a, col_b):
        x0, x1 = max(0, int(x0)), min(self.w, int(x1))
        y0, y1 = max(0, int(y0)), min(self.h, int(y1))
        span_x = max(1, x1 - x0 - 1)
        span_y = max(1, y1 - y0 - 1)
        for y in range(y0, y1):
            ty = (y - y0) / span_y
            buf = bytearray((x1 - x0) * 3)
            for idx, x in enumerate(range(x0, x1)):
                t = 0.65 * ((x - x0) / span_x) + 0.35 * ty
                buf[idx * 3] = int(col_a[0] + (col_b[0] - col_a[0]) * t)
                buf[idx * 3 + 1] = int(col_a[1] + (col_b[1] - col_a[1]) * t)
                buf[idx * 3 + 2] = int(col_a[2] + (col_b[2] - col_a[2]) * t)
            self.rows[y][x0 * 3:x1 * 3] = buf

    def text_block(self, x, y, width, col, height=8, word_gaps=True):
        """Draws synthetic word-segmented glyph bars so edge density mirrors real typography."""
        if not word_gaps:
            self.fill(x, y, x + width, y + height, col)
            return
        cur = x
        end = x + width
        step = 0
        while cur + 12 < end:
            word_w = min(end - cur, 34 + (step * 17) % 44)
            # Draw broken vertical glyph strokes inside each word to mimic character edges.
            gx = cur
            while gx + 4 <= cur + word_w:
                self.fill(gx, y, gx + 3, y + height, col)
                gx += 5
            cur += word_w + 10
            step += 1

    def button(self, x, y, w, h, fill_col, label_col):
        self.fill(x, y, x + w, y + h, fill_col)
        lw = max(24, w - 36)
        ly = y + (h - 6) // 2
        self.text_block(x + (w - lw) // 2, ly, lw, label_col, height=6)

    def save(self, path):
        visual_check.write_png(path, self.w, self.h, [bytes(r) for r in self.rows])


def _draw_header(cv, t, full_bleed=True):
    if full_bleed:
        cv.hline(0, cv.w, 60, t["divider"], thickness=1)
    cv.text_block(80, 22, 180, t["ink"], height=12)
    cv.text_block(1080, 24, 160, t["muted"], height=8)


def _draw_clean_body(cv, t, top=100, text_col=None, with_cta=True):
    ink = text_col or t["ink"]
    muted = text_col or t["muted"]
    cv.text_block(80, top, 340, ink, height=16)
    cv.text_block(80, top + 28, 520, muted, height=8)
    if with_cta:
        cv.button(1220, top, 140, 38, t["btn_neutral"], t["btn_text"])
    y = top + 76
    for idx in range(7):
        cv.text_block(80, y, 280 + (idx % 3) * 80, ink, height=10)
        cv.text_block(80, y + 18, 620 + (idx % 2) * 140, muted, height=8)
        cv.text_block(1160, y + 4, 180, muted, height=8)
        y += 88


def _draw_cta_row(cv, x, y, w, h, gap, colors, label_col):
    for i, col in enumerate(colors):
        cv.button(x + i * (w + gap), y, w, h, col, label_col)


def _draw_kpi_strip(cv, t, top, height=122, badge_cols=None):
    for i in range(4):
        cx = 80 + i * 324
        cv.fill(cx, top, cx + 304, top + height, t["surface"])
        cv.stroke(cx, top, cx + 304, top + height, t["border"], thickness=1)
        cv.text_block(cx + 20, top + 20, 120, t["muted"], height=8)
        cv.text_block(cx + 20, top + 46, 150, t["ink"], height=16)
        if badge_cols:
            cv.fill(cx + 200, top + 14, cx + 284, top + 36, badge_cols[i])
        else:
            cv.text_block(cx + 20, top + 82, 100, t["muted"], height=8)


def gen_clean(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    _draw_clean_body(cv, t)
    return cv


def gen_competing_ctas_hued(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    _draw_clean_body(cv, t, with_cta=False)
    _draw_cta_row(cv, 900, 100, 140, 38, 20, [t["accent"], t["accent2"], t["accent3"]], (255, 255, 255))
    return cv


def gen_competing_ctas_neutral(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    _draw_clean_body(cv, t, with_cta=False)
    _draw_cta_row(cv, 900, 100, 140, 38, 20, [t["btn_neutral"]] * 3, t["btn_text"])
    return cv


def gen_outline_badge_soup(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    _draw_clean_body(cv, t, top=160)
    cols = [t["accent"], t["accent2"], t["accent3"], t["green"], t["amber"], t["cyan"], t["accent"]]
    x = 80
    for c in cols:
        cv.stroke(x, 96, x + 110, 122, c, thickness=2)
        cv.text_block(x + 14, 105, 80, c, height=6)
        x += 124
    return cv


def gen_solid_badge_soup(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    _draw_clean_body(cv, t, top=160)
    cols = [t["accent"], t["green"], t["amber"], t["accent"], t["green"], t["amber"], t["accent"]]
    x = 80
    for c in cols:
        cv.fill(x, 96, x + 110, 118, c)
        cv.text_block(x + 14, 104, 80, (255, 255, 255), height=6)
        x += 126
    return cv


def gen_gradient_hero(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    cv.gradient(80, 84, 1360, 350, t["grad_a"], t["grad_b"])
    cv.text_block(120, 130, 420, (255, 255, 255), height=18)
    cv.text_block(120, 168, 560, (230, 230, 255), height=10)
    cv.button(120, 224, 150, 40, t["accent3"], (255, 255, 255))
    _draw_clean_body(cv, t, top=400, with_cta=False)
    return cv


def gen_kpi_stat_strip(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    _draw_kpi_strip(cv, t, top=88, height=122)
    _draw_clean_body(cv, t, top=250)
    return cv


def gen_ai_slop_dashboard(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    cv.gradient(80, 76, 1360, 220, t["grad_a"], t["grad_c"])
    cv.text_block(110, 104, 380, (255, 255, 255), height=16)
    _draw_cta_row(cv, 900, 104, 130, 38, 16, [t["accent"], t["accent2"], t["accent3"]], (255, 255, 255))
    _draw_kpi_strip(cv, t, top=240, height=104, badge_cols=[t["green"], t["amber"], t["cyan"], t["accent3"]])
    # Nested card + badge soup + divider spam
    cv.fill(80, 368, 1360, 860, t["surface"])
    cv.stroke(80, 368, 1360, 860, t["border"], thickness=1)
    cv.fill(104, 392, 1336, 836, t["surface2"])
    cv.stroke(104, 392, 1336, 836, t["border"], thickness=1)
    cv.fill(128, 420, 1312, 812, t["bg"])
    cv.stroke(128, 420, 1312, 812, t["border"], thickness=1)
    y = 442
    cols = [t["accent"], t["accent2"], t["accent3"], t["green"], t["amber"], t["cyan"]]
    for row in range(6):
        cv.text_block(152, y, 420, t["ink"], height=10)
        cv.fill(620, y - 4, 720, y + 18, cols[row % len(cols)])
        cv.text_block(760, y, 360, t["muted"], height=8)
        cv.hline(148, 1292, y + 34, t["divider"], thickness=1)
        y += 58
    return cv


def gen_divider_spam_nested_cards(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    # 3-level nested bordered cards
    for pad, bg_k in ((0, "bg"), (24, "bg"), (48, "bg")):
        cv.fill(80 + pad, 96 + pad, 1360 - pad, 840 - pad, t[bg_k])
        cv.stroke(80 + pad, 96 + pad, 1360 - pad, 840 - pad, t["border"], thickness=1)
    cv.button(1140, 168, 136, 36, t["btn_neutral"], t["btn_text"])
    y = 226
    for _ in range(11):
        cv.text_block(156, y, 520, t["ink"], height=8)
        cv.text_block(760, y, 320, t["muted"], height=8)
        cv.hline(148, 1292, y + 26, t["border"], thickness=1)
        y += 48
    return cv


def gen_tonal_nesting(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t, full_bleed=False)
    # 3 borderless tonal surfaces stacked inside one another
    cv.fill(80, 96, 1360, 840, t["surface"])
    cv.fill(120, 140, 1320, 800, t["surface2"])
    cv.fill(160, 184, 1280, 760, t["surface3"])
    cv.text_block(200, 224, 380, t["ink"], height=14)
    cv.text_block(200, 260, 620, t["muted"], height=8)
    cv.button(200, 310, 140, 38, t["btn_neutral"], t["btn_text"])
    return cv


def gen_faded_low_contrast_text(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t, full_bleed=False)
    _draw_clean_body(cv, t, text_col=t["faded"], with_cta=False)
    cv.button(1220, 100, 140, 38, t["faded"], t["bg"])
    return cv


def gen_dead_void(t):
    cv = Canvas(W, H, t["bg"])
    cv.text_block(40, 40, 240, t["ink"], height=14)
    cv.text_block(40, 72, 260, t["muted"], height=8)
    cv.text_block(40, 98, 220, t["muted"], height=8)
    cv.button(40, 132, 130, 36, t["btn_neutral"], t["btn_text"])
    return cv


def gen_cramped_hit_targets(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    _draw_clean_body(cv, t, top=180, with_cta=False)
    # Row 1: two sub-32px buttons (24px tall) separated by 3px (<6px)
    cv.button(80, 100, 110, 24, t["accent"], (255, 255, 255))
    cv.button(193, 100, 110, 24, t["btn_neutral"], t["btn_text"])
    # Row 2: two 36px buttons crowded with 4px gap
    cv.button(420, 100, 130, 36, t["accent"], (255, 255, 255))
    cv.button(554, 100, 130, 36, t["btn_neutral"], t["btn_text"])
    return cv


def gen_misaligned_left_edges(t):
    cv = Canvas(W, H, t["bg"])
    _draw_header(cv, t)
    offsets = [0, 8, 0, -6, 10, 0, 0]
    y = 110
    for dx in offsets:
        cv.text_block(80 + dx, y, 420, t["ink"], height=12)
        cv.text_block(80 + dx, y + 20, 560, t["muted"], height=8)
        y += 104
    cv.button(1220, 110, 140, 38, t["btn_neutral"], t["btn_text"])
    return cv


def gen_fullpage_long(t):
    """1440x2700 3-fold capture: fold 1 gradient hero, fold 2 slop strip, fold 3 sparse dead void."""
    cv = Canvas(W, 2700, t["bg"])
    _draw_header(cv, t)
    # Fold 1 (y=0..900): gradient hero
    cv.gradient(80, 96, 1360, 760, t["grad_a"], t["grad_b"])
    cv.text_block(130, 220, 480, (255, 255, 255), height=20)
    cv.button(130, 310, 150, 40, t["accent3"], (255, 255, 255))
    # Fold 2 (y=900..1800): competing CTAs + dense rows
    _draw_cta_row(cv, 80, 960, 140, 38, 20, [t["accent"], t["accent2"], t["accent3"]], (255, 255, 255))
    y = 1040
    for _ in range(9):
        cv.text_block(80, y, 1120, t["ink"], height=10)
        cv.text_block(80, y + 20, 960, t["muted"], height=8)
        cv.hline(80, 1360, y + 48, t["divider"], thickness=1)
        y += 76
    # Fold 3 (y=1800..2700): mostly empty void with tiny footer text
    cv.text_block(80, 1860, 200, t["muted"], height=8)
    return cv


def gen_reflow_pair(t):
    """Returns (before 1440x900 with 140px KPI banner, after 1440x760 with banner removed)."""
    def draw_table(cv, top):
        cv.text_block(80, top, 360, t["ink"], height=14)
        cv.button(1220, top - 4, 140, 38, t["accent"], (255, 255, 255))
        y = top + 64
        for idx in range(7):
            cv.text_block(80, y, 220 + (idx * 53) % 240, t["ink"], height=10)
            cv.text_block(480, y, 360 + (idx * 37) % 180, t["muted"], height=8)
            cv.text_block(1120, y, 160, t["muted"], height=8)
            y += 74

    before = Canvas(W, H, t["bg"])
    _draw_header(before, t)
    _draw_kpi_strip(before, t, top=84, height=112)
    draw_table(before, 240)

    after = Canvas(W, H - 140, t["bg"])
    _draw_header(after, t)
    draw_table(after, 100)
    return before, after


SCENARIOS = {
    "clean": gen_clean,
    "competing_ctas_hued": gen_competing_ctas_hued,
    "competing_ctas_neutral": gen_competing_ctas_neutral,
    "outline_badge_soup": gen_outline_badge_soup,
    "solid_badge_soup": gen_solid_badge_soup,
    "gradient_hero": gen_gradient_hero,
    "kpi_stat_strip": gen_kpi_stat_strip,
    "ai_slop_dashboard": gen_ai_slop_dashboard,
    "divider_spam_nested_cards": gen_divider_spam_nested_cards,
    "tonal_nesting": gen_tonal_nesting,
    "faded_low_contrast_text": gen_faded_low_contrast_text,
    "dead_void": gen_dead_void,
    "cramped_hit_targets": gen_cramped_hit_targets,
    "misaligned_left_edges": gen_misaligned_left_edges,
    "fullpage_long": gen_fullpage_long,
}


def generate_all(out_dir, only=None):
    """Writes requested scenarios (light + dark) plus reflow_before/after pairs into out_dir."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = [n for n in SCENARIOS if not only or n in only]
    written = []
    for name in names:
        fn = SCENARIOS[name]
        for theme_name, pal in THEMES.items():
            p = out / f"{name}_{theme_name}.png"
            fn(pal).save(p)
            written.append((name, theme_name, p))
    if not only or "reflow" in only:
        for theme_name, pal in THEMES.items():
            before, after = gen_reflow_pair(pal)
            pb = out / f"reflow_before_{theme_name}.png"
            pa = out / f"reflow_after_{theme_name}.png"
            before.save(pb)
            after.save(pa)
            written.append(("reflow_pair", theme_name, (pb, pa)))
    return written


def _rng(a, b, fmt="{:.1f}"):
    lo, hi = min(a, b), max(a, b)
    if abs(hi - lo) < 0.05:
        return fmt.format(lo)
    return f"{fmt.format(lo)}-{fmt.format(hi)}"


def calibrate(only=None):
    """Generates all fixtures in a temp dir, runs visual_check, prints calibration table, and verifies invariants."""
    tmp = Path(tempfile.mkdtemp(prefix="visual-fixtures-"))
    problems = []
    try:
        items = generate_all(tmp, only=only)
        results = {}
        reflow_results = {}
        for name, theme_name, target in items:
            if name == "reflow_pair":
                reflow_results[theme_name] = visual_check.compare_images(target[0], target[1])
            else:
                results.setdefault(name, {})[theme_name] = visual_check.inspect_image(target)

        print("| capture | edge % | isl/hues | ctrl row | grad % | <3:1 % | fg % | nest | drift | small/crowd |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for name, by_theme in results.items():
            li, di = by_theme["light"], by_theme["dark"]
            if not li["ok"] or not di["ok"]:
                problems.append(f"{name}: inspect failed (light={li['ok']}, dark={di['ok']})")
                continue
            print(
                f"| {name} "
                f"| {_rng(li['edge_density_pct'], di['edge_density_pct'])} "
                f"| {_rng(li['accent_islands'], di['accent_islands'], '{:.0f}')}/{_rng(li['accent_hues'], di['accent_hues'], '{:.0f}')} "
                f"| {_rng(li['solid_controls_row'], di['solid_controls_row'], '{:.0f}')} "
                f"| {_rng(li['gradient_area_pct'], di['gradient_area_pct'])} "
                f"| {_rng(li['contrast_lt3_pct'], di['contrast_lt3_pct'])} "
                f"| {_rng(li['foreground_pct'], di['foreground_pct'])} "
                f"| {_rng(li['surface_nesting_depth'], di['surface_nesting_depth'], '{:.0f}')} "
                f"| {_rng(li['misaligned_blocks'], di['misaligned_blocks'], '{:.0f}')} ({_rng(li['max_left_drift_px'], di['max_left_drift_px'])}px) "
                f"| {_rng(li['small_targets'], di['small_targets'], '{:.0f}')}/{_rng(li['crowded_controls'], di['crowded_controls'], '{:.0f}')} |"
            )

        # Invariant assertions across light & dark themes.
        def check(name, pred, msg):
            if name not in results:
                return
            for th in ("light", "dark"):
                info = results[name][th]
                if not pred(info):
                    problems.append(f"{name} ({th}): {msg} (got {info!r})")

        check("clean", lambda d: d["accent_islands"] == 0 and d["solid_controls_row"] == 1
              and d["surface_nesting_depth"] == 0 and d["misaligned_blocks"] == 0
              and d["small_targets"] == 0 and d["crowded_controls"] == 0,
              "clean baseline should have 0 islands, 1 CTA, 0 nesting, 0 drift, 0 cramped")
        check("competing_ctas_hued", lambda d: d["solid_controls_row"] == 3 and d["accent_islands"] >= 3 and d["accent_hues"] >= 3,
              "expected 3 competing hued CTAs across 3 hues")
        check("competing_ctas_neutral", lambda d: d["solid_controls_row"] == 3 and d["accent_islands"] == 0,
              "expected 3 neutral CTAs and 0 accent islands")
        check("solid_badge_soup", lambda d: d["accent_islands"] >= 5 and d["accent_hues"] >= 3 and d["solid_controls_row"] == 1,
              "expected >=5 badge islands across 3 hues without inflating solid_controls_row")
        check("gradient_hero", lambda d: d["gradient_area_pct"] >= 10,
              "expected gradient_area_pct >= 10%")
        check("kpi_stat_strip", lambda d: d["surface_nesting_depth"] == 1,
              "expected 1-level KPI card surfaces")
        check("ai_slop_dashboard", lambda d: d["solid_controls_row"] >= 3 and d["surface_nesting_depth"] >= 3 and d["accent_islands"] >= 5,
              "expected slop dashboard to trigger CTAs, nesting >=3, and islands >=5")
        check("divider_spam_nested_cards", lambda d: d["surface_nesting_depth"] >= 3,
              "expected 3-level bordered card nesting")
        check("tonal_nesting", lambda d: d["surface_nesting_depth"] >= 3,
              "expected 3-level borderless tonal nesting")
        check("faded_low_contrast_text", lambda d: d["contrast_lt3_pct"] >= 80,
              "expected >=80% low-contrast edges on faded text")
        check("dead_void", lambda d: d["empty_tiles"] >= 8 and d["spatial_edge_std"] >= 1.5,
              "expected >=8 empty tiles and elevated spatial std on dead void")
        check("cramped_hit_targets", lambda d: d["small_targets"] >= 2 and d["crowded_controls"] >= 2,
              "expected >=2 small (<32px) targets and >=2 crowded (<6px) pairs")
        check("misaligned_left_edges", lambda d: d["misaligned_blocks"] >= 3 and d["max_left_drift_px"] >= 8.0,
              "expected >=3 misaligned blocks with >=8px drift")
        check("fullpage_long", lambda d: len(d["viewport_bands"]) == 3
              and d["viewport_bands"][0]["gradient_area_pct"] >= 30
              and d["viewport_bands"][1]["solid_controls_row"] == 3
              and d["viewport_bands"][2]["empty_quarters"] >= 2,
              "expected 3 distinct viewport bands (gradient fold 1, 3 CTAs fold 2, sparse fold 3)")

        for th, cmp_res in reflow_results.items():
            print(f"\nReflow ({th}): {visual_check.format_compare(cmp_res)}")
            if (not cmp_res["ok"] or not any(s["dy"] == -140 for s in cmp_res["band_shifts"])
                    or cmp_res["changed_pct"] > 22.0 or any(r["y"] >= 240 for r in cmp_res["regions"])):
                problems.append(f"reflow ({th}): failed to align dy=-140 without flagging body: {cmp_res!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if problems:
        print("\n".join(f"FAIL    {p}" for p in problems))
        print(f"\nvisual_fixtures calibrate: {len(problems)} problem(s)")
        return 1
    print("\nvisual_fixtures calibrate: ok")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="directory where PNG calibration fixtures will be written")
    parser.add_argument("--only", nargs="+", help="restrict generation/calibration to named scenarios (or 'reflow')")
    parser.add_argument("--calibrate", action="store_true", help="generate to temp dir, print telemetry table, verify invariants")
    parser.add_argument("--list", action="store_true", help="list available scenario names")
    args = parser.parse_args()
    if args.list:
        for name in SCENARIOS:
            print(name)
        print("reflow")
        return 0
    if args.calibrate:
        return calibrate(only=args.only)
    if args.out:
        written = generate_all(args.out, only=args.only)
        print(f"Wrote {len(written)} fixture entries to {Path(args.out).resolve()}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
