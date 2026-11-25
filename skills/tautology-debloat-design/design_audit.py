"""Static visual-design auditor for screens: overloaded hierarchy, visual repetition, excessive
padding/clutter, spacing off the project's rhythm, hardcoded styles outside theme files, UI state
inventory, and PNG visual checks.

    python design_audit.py --find settings                  # locate screen files by name/symbol/title
    python design_audit.py --states path/to/screen          # list interactive UI states, modals, and hooks
    python design_audit.py --all path/to/screen             # full audit of the target screen
    python design_audit.py --diff                           # changed UI lines against HEAD
    python design_audit.py --only design,style ...          # checks: design, style, spacing, bloat
    python design_audit.py --check-image shot.png           # verify screenshot is non-blank and measure density
    python design_audit.py --compare before.png after.png   # diff regions & visual density between captures
    python design_audit.py --accept "<line>" --reason "<why>"
    python design_audit.py --selftest
"""
import argparse
import bisect
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAUTOLOGY_DIR = HERE.parent / "tautology"
MODULAR_DIR = HERE.parent / "modular"
CASES = HERE / "cases"
for extra in (HERE, TAUTOLOGY_DIR, MODULAR_DIR):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import core  # noqa: E402
import copy_rules  # noqa: E402
import hunt  # noqa: E402
import spacing  # noqa: E402
import visual_check  # noqa: E402

CHECKS = {"design", "style", "spacing", "bloat"}
STYLE_EXT = {".css", ".scss"}
MARKUP_EXT = {".vue", ".svelte"}
JSX_LIKE = core.JSX | MARKUP_EXT
WEB_EXT = core.JS | MARKUP_EXT
CODE_EXT = core.CODE | MARKUP_EXT
SCAN_EXT = CODE_EXT | STYLE_EXT
normalize_jsx_tags = core.normalize_jsx_tags
DEFAULT_THEME_RE = re.compile(r"(^|/)(?:theme|tokens|palette|colors)(?:/|\.)", re.I)
GENERIC_UI_DIRS = {"screens", "views", "pages", "components", "dialogs", "modals", "ui", "src"}
SCREEN_SUBDIRS = {"view", "views", "ui", "dialogs", "modals", "detail"}

# Patterns for structural UI cues across GPUI/egui (Rust), React/HTML/Vue/Svelte (JSX/TSX), and Qt (Python).
STYLED_BOX_RE = re.compile(
    r"\b(?:solid_card|surface_card|form_card|modal_card|panel|card|QFrame|QGroupBox)\s*\("
    r"|\b(?:egui::)?Frame::(?:group|window|popup|canvas|side_top_panel|central_panel)\s*\("
    r"|\bui\.group\s*\("
    r"|<(?:Card|Paper|Panel|Box|Modal|Dialog)\b"
    r"|\.(?:bg|border_[1-9]\w*|border_color|shadow_\w+)\("
    r"|<(?:div|section|aside|article)\b[^>]*(?:bg-(?!transparent\b)|shadow|card|rounded|\bborder(?!-(?:0|none|transparent)\b))"
)
CARD_CALL_RE = re.compile(
    r"\b(?:solid_card|surface_card|form_card|modal_card)\s*\("
    r"|\b(?:visual|kit|ui|theme)::card\s*\("
    r"|\b(?:egui::)?Frame::group\s*\(|\bui\.group\s*\("
    r"|<(?:Card|Paper|Panel)\b|\.rounded\([^)]*\)\s*\.bg\("
)
INNER_CARD_CALL_RE = re.compile(
    r"\b(?:solid_card|surface_card|form_card)\s*\(|\b(?:visual|kit|ui|theme)::card\s*\("
)
MODAL_BOX_RE = re.compile(
    r"\b(?:modal_card|modal_shell|modal_backdrop|(?:visual|kit|ui)::(?:modal|dialog))\s*\(|<(?:Modal|Dialog)\b"
)
HEADLINE_RE = re.compile(r"\b(?:visual|typography|theme)::HEADLINE\b|\bui\.heading\s*\(|<h1\b")
MICRO_RE = re.compile(
    r"\b(?:button|reveal_button|secondary_button|primary_button|close_button|icon|tag|badge|chip|switch|check_box)\s*\("
    r"|\bsize\(px\((?:[1-9]|1[0-9]|2[0-9]|3[0-6])\.?\)"
)
DIVIDER_RE = re.compile(
    r"\b(?:divider|separator|row_separator)\s*\(|\bui\.separator\s*\(|<hr\b|<Divider\b|<Separator\b|"
    r"<(?:v|el|n|a|q)-(?:divider|separator)\b|QFrame[\.:]+HLine|\.h\(px\([12]\.?\)\)"
)
BADGE_RE = re.compile(r"\b(?:visual::tag|tag|badge|chip|status_pill)\s*\(|<(?:Badge|Chip|Tag)\b|<(?:v|el|n|a|q|b)-(?:chip|tag|badge)\b")
ICON_RE = re.compile(r"\b(?:visual::icon|icon)\s*\(|<(?:[A-Z]\w*Icon|Icon)\b")
ICON_IMPORT_RE = re.compile(
    r"""import\s*\{([^}]+)\}\s*from\s*['"](?:lucide-react|@heroicons/[^'"]+|@radix-ui/react-icons|"""
    r"""react-icons(?:/[^'"]+)?|@?phosphor-icons(?:/[^'"]+)?|@tabler/icons-react)['"]""",
    re.S,
)
INFO_HINT_RE = re.compile(r"\binfo_hint\s*\(|<(?:InfoHint|HelpTip|HintIcon)\b")
PRIMARY_ACCENT_RE = re.compile(
    r"\bprimary_button\s*\("
    r"|\.bg\(\s*(?!Rgba\s*\{)[^;\n]*\b\w+\.primary\b"
    r"|\.pill\([^;\n]*,\s*\w+\.primary\b"
    r"|\bvariant\s*=\s*[\"']primary[\"']"
    r"|\bbg-primary\b(?!/)"
    r"|<PrimaryButton\b"
    r"|\.setProperty\(\s*[\"']primary[\"']\s*,\s*True\s*\)"
)
# Vue/Element/Naive/Ant kits: <v-btn color="primary">, <el-button type="primary">, <a-button type="primary">
VUE_KIT_PRIMARY_RE = re.compile(r"""<(?:v-btn|el-button|n-button|a-button|q-btn)\b[^>]*\b(?:color|type)\s*=\s*["']primary["']""")
PRIMARY_FILL_BODY_RE = re.compile(PRIMARY_ACCENT_RE.pattern + r"|\b\w+\.on_primary\s*\(")
KIT_HELPER_FILE_RE = re.compile(r"(?i)(?:^|/)(?:kit|visual|components|widgets|controls|theme)(?:/|\.)")
QUALIFIED_SEGMENTED_RE = re.compile(r"\b(?:kit|visual|ui)::(segmented\w*)\s*\(")
PRIMARY_ARG_CALL_RE = re.compile(r"(?:\.pill|\bsegmented\w*)\s*\(")
FILTER_TRIGGER_RE = re.compile(
    r"\b(?:menu_trigger|menu_trigger_with_state)\s*\("
    r"|<(?:Select|select|Dropdown|FilterSelect|SortSelect)\b"
    r"|\bQComboBox\s*\("
)
FILTER_CHIP_RE = re.compile(r"\bfilter_chip\s*\(|<(?:FilterChip|FilterPill|ToggleButton)\b")
MODAL_CALLEE_RE = re.compile(
    r"(?i:(?:^|_)(?:modal|dialog|overlay|popup|layer|drawer|sheet)(?:s|_|$))"
    r"|(?<=[a-z0-9])(?:Modal|Dialog|Overlay|Popup|Layer|Drawer|Sheet)(?:s|$|(?=[A-Z_]))"
)
FIXED_TRIGGER_WIDTH_RE = re.compile(
    r"\.w\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)"
    r"|\bw-\[(\d+(?:\.\d*)?)px\]"
    r"|\.setFixedWidth\(\s*(\d+)\s*\)"
)
LOOP_START_RE = re.compile(
    r"^\s*(?:for\s+\w|\.for_each\s*\(|\.map\(\s*\|)"
    r"|\.\s*(?:map|flatMap)\(\s*(?:\||(?:\(?[A-Za-z_]\w*(?:\s*:\s*[^,)=]+)?(?:\s*,\s*[A-Za-z_]\w*(?:\s*:\s*[^,)=]+)?)*\)?\s*=>))"
    r"|\bv-for\s*=|\{#each\b"
)
SPACER_RE = re.compile(
    r"\bdiv\(\)\s*\.(?:h|w)\(px\((?P<px>\d+(?:\.\d*)?)\)\)\s*(?:[,;)]|$)"
    r"|\bui\.add_space\(\s*(?P<egui_px>\d+(?:\.\d*)?)\s*\)"
    r"|<Spacer\b|\.addSpacing\("
)
TEXT_STACK_CALL_RE = re.compile(
    r"\.(?:child)\(\s*(?:div\(\)[^;{}]*\.text_size|\"|format!)|<(?:p|span|small|Caption|Text)\b"
)
TEXT_OPEN_TAG_RE = re.compile(r"<(p|span|small|Caption|Text)\b[^>]*>")
TEXT_CLOSE_ONLY_RE = re.compile(r"^\s*</(?:p|span|small|Caption|Text)>\s*$")
HEADER_NAME_RE = re.compile(r"(?i)header|title|heading|subtitle|intro|banner|card_head")
FN_DECL_RE = re.compile(
    r"^\s*(?:(?:pub(?:\([^)]*\))?|export(?:\s+default)?)\s+)?(?:async\s+)?(?:unsafe\s+)?(?:fn|def|function)\s+"
)

# Composite AI-slop cues & intra-screen consistency patterns.
SLOP_GRADIENT_RE = re.compile(r"\bbg-gradient-to-|\b(?:linear|radial|conic)-gradient\(")
SLOP_GLASS_CUES_RE = re.compile(r"\bbackdrop-blur(?:-\w+)?\b|\bshadow-(?:xl|2xl)\b")
SLOP_FROST_BG_RE = re.compile(r"\bbg-white/(?:5|10|15|20)\b|\bring-1\s+ring-white/")
SLOP_MODAL_CONTEXT_RE = re.compile(
    r"(?i)\b(?:modal|dialog|overlay|popover|tooltip|drawer|sheet|modal_backdrop)\b|fixed\s+inset-0"
)
SLOP_GRAD_CLIP_RE = re.compile(r"\bbg-clip-text\b")
SLOP_GRAD_TRANS_RE = re.compile(r"\btext-transparent\b")
TW_PILL_TEXT_RE = re.compile(r"\btext-(?:xs|\[1[012]px\])\b")
TW_PILL_BG_RE = re.compile(r"\bbg-(?!transparent\b)[\w/\[\]#-]+")
TW_DOT_H_RE = re.compile(r"\bh-(?:1\.5|2|2\.5)\b")
TW_DOT_W_RE = re.compile(r"\bw-(?:1\.5|2|2\.5)\b")
TW_DOT_SIZE_RE = re.compile(r"\bsize-(?:1\.5|2|2\.5)\b")
TW_DOT_ANIM_RE = re.compile(r"\banimate-(?:pulse|ping)\b")
TW_DOT_LOADING_RE = re.compile(
    r"(?i)\b(?:loading|isLoading|spinner|skeleton|progress|fetching|syncing|busy|pending|typing|isTyping|recording|"
    r"isRecording|rec|streaming|connecting|uploading|transcrib\w*|listening|capturing)\b"
    r"|\brole=[\"']status[\"']|\baria-live=|animation-delay|\bdelay-\d+"
)

# Intra-screen consistency extractors (full audit only).
CONSIST_TW_RADIUS_RE = re.compile(
    r"(?<![\w-])rounded(?:-(?:none|sm|md|lg|xl|2xl|3xl)|-\[(\d+(?:\.\d+)?)(px|rem)\])?(?![\w-])"
)
CONSIST_RS_RADIUS_RE = re.compile(r"\.rounded\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)|\.rounded_(sm|md|lg|xl|2xl)\(\)")
CONSIST_TW_FONT_RE = re.compile(r"(?<![\w-])text-(xs|sm|base|lg|xl|[2-6]xl|\[(?:\d+(?:\.\d+)?)(?:px|rem)\])(?![\w-])")
CONSIST_RS_FONT_RE = re.compile(r"\.text_size\(\s*px\(\s*([^)]+)\s*\)\s*\)|\.text_(xs|sm|base|lg|xl|2xl)\(\)")
CONSIST_CARD_TW_PAD_RE = re.compile(r"(?<![\w-])p(?:[xy])?-(?:(\d+(?:\.5)?)|\[(\d+(?:\.\d+)?)(px|rem)\])(?![\w.\]-])")
# Tailwind card-like surface: rounded corners plus a border, fill or shadow (shadcn-style `rounded-lg border p-4`).
TW_CARD_LIKE_RE = re.compile(
    r"<(?:div|section|article|li|aside)\b[^>]*\brounded(?:-[\w\[\].]+)?\b[^>]*"
    r"(?:\bborder(?!-(?:0|none|transparent)\b)|\bbg-(?!transparent\b)|\bshadow\b|\bshadow-(?!none\b))"
    r"|<(?:div|section|article|li|aside)\b[^>]*(?:\bborder(?!-(?:0|none|transparent)\b)|\bbg-(?!transparent\b)|\bshadow\b|\bshadow-(?!none\b))"
    r"[^>]*\brounded(?:-[\w\[\].]+)?\b"
)
# KPI stat value: an element in a large display size whose only content is a number or a single {expression}.
KPI_VALUE_RE = re.compile(
    r"<(\w+)\b[^>]*\btext-(?:2xl|3xl|4xl|5xl|6xl)\b[^>]*>\s*"
    r"(?:[$€£¥]|R\$)?\s*(?:\d[\d.,]*\s*[%kKmMbB]?|\{\s*[\"'`]?[$€£¥]?\d[\d.,]*\s*[%kKmMbB]?[\"'`]?\s*\}"
    r"|\{(?!\s*[\"'`])[^{}<>]*(?:toLocaleString|toFixed|format\w*|Intl\.NumberFormat|"
    r"(?i:count|total|value|amount|sum|avg|average|rate|pct|percent|revenue|users|qty|num|kpi|stat|delta|metric|mrr|arr|unread|views|clicks|visits|orders|sales|signups|score|balance|spent|budget|growth|churn)\w*)"
    r"[^{}<>]*\})\s*</\1>"
)
KPI_NON_METRIC_TAG_RE = re.compile(r"^<(?:a|h[1-6]|Link)\b")
KPI_PRICE_PERIOD_RE = re.compile(r"(?i)/\s*(?:mo|month|yr|year|user|seat)\b|\bper\s+(?:month|year|user|seat)\b")
KPI_LABEL_RE = re.compile(r"\btext-(?:xs|sm|\[1[0-4]px\])\b|\b(?:uppercase|tracking-wide|text-muted)")
CONSIST_CARD_RS_PAD_RE =re.compile(r"\.(?:p|px|py)\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)|\.(?:p|px|py)_(\d+)(p5)?\(\)")

# Patterns for hardcoded styling literals.
CSS_FIX = "use CSS custom property or theme token"
QSS_FIX = "use theme palette/QSS variables"
RS_HEX_COLOR_RE = re.compile(r"\b(?:gpui::)?(rgb|rgba|hsla)\(\s*(0x[0-9a-fA-F_]{6,12}|[^)]+)\)")
EGUI_COLOR_RE = re.compile(r"\b(?:egui::)?Color32::from_(?:rgb|rgba_unmultiplied|rgba_premultiplied|hex)\s*\([^)]+\)")
EGUI_FONT_SIZE_RE = re.compile(r"\bRichText::new\([^)]*\)\s*\.size\(\s*(\d+(?:\.\d*)?)\s*\)")
RS_RGBA_LIT_RE = re.compile(r"\bRgba\s*\{\s*(?![^}]*\.\.)[^}]*\}")
CSS_COLOR_RE = re.compile(r"(#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(\s*\d[^)]*\))")
RS_FONT_SIZE_RE = re.compile(r"\.text_size\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)")
RS_RADIUS_RE = re.compile(r"\.rounded\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)")
RS_CIRCLE_SIZE_RE = re.compile(r"\b(?:size|h|w)\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)")
JSX_INLINE_STYLE_RE = re.compile(r"""\bstyle=\{\{([^}]+)\}\}""")
QSS_INLINE_RE = re.compile(r"""\.setStyleSheet\(\s*["']([^"']+)["']""")
JSX_FONT_PX_RE = re.compile(r"\bfontSize\s*:\s*['\"]?(\d+)")
JSX_RADIUS_PX_RE = re.compile(r"\bborderRadius\s*:\s*['\"]?(\d+)")
CSS_FONT_PX_RE = re.compile(r"\bfont-size\s*:\s*(\d+(?:\.\d*)?)\s*px")
CSS_RADIUS_PX_RE = re.compile(r"\bborder-radius\s*:\s*(\d+(?:\.\d*)?)\s*px")
CSS_VAR_DECL_RE = re.compile(r"^\s*--[\w-]+\s*:")
CSS_ANY_DECL_RE = re.compile(r"^\s*[\w-]+\s*:")
CSS_SCOPE_RE = re.compile(r"^\s*(?::root\b|@theme\b|\[data-theme\b)")
TW_COLOR_RE = re.compile(
    r"\b(?:bg|text|border|ring|fill|stroke|from|via|to|outline|shadow|accent|decoration)-\[(#[0-9a-fA-F]{3,8}|(?:rgba?|hsla?)\([^\]\n]+\))\]"
)
TW_RAW_COLOR_RE = re.compile(
    r"\b(?:bg|text|border|ring|from|via|to|fill|stroke|outline|shadow)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|"
    r"yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b"
)
TW_SEMANTIC_COLOR_RE = re.compile(
    r"\b(?:bg|text|border)-(?:background|foreground|card|card-foreground|surface|primary|primary-foreground|"
    r"secondary|muted|muted-foreground|accent|destructive|border|input|ring|base|action-primary|on-base)\b"
)
TW_FONT_RE = re.compile(r"\btext-\[(\d+(?:\.\d+)?)(px|rem|em)\]")
TW_RADIUS_RE = re.compile(r"\brounded(?:-[trbl]{1,2})?-\[(\d+(?:\.\d+)?)(px|rem|em)\]")
THEME_STYLE_HINT_RE = re.compile(r"\b(?:visual::\w+|theme::\w+|\w+_radius|var\(--|tokens\.)")
URL_HASH_RE = re.compile(r"https?://\S*#")


def _is_css_token_only_file(source):
    """True when >50% of non-comment CSS declarations are `--*` custom properties."""
    clean = re.sub(r"/\*[\s\S]*?\*/", "", source)
    total = 0
    vars_cnt = 0
    for chunk in re.split(r"[;{}]", clean):
        line = chunk.strip()
        if not line or line.startswith("@") or "{" in line:
            continue
        if CSS_VAR_DECL_RE.match(line):
            vars_cnt += 1
            total += 1
        elif CSS_ANY_DECL_RE.match(line) and not re.match(r"^[a-z-]+:\s*(?:hover|focus|active|before|after|root|not\()", line):
            total += 1
    return total > 0 and vars_cnt * 2 > total


def is_theme_file(rel, source=None):
    """Theme/token definition files hold color and metric literals on purpose.
    Does not blindly exempt `styles/` folders; only explicit theme/token paths or CSS files
    composed mostly (>50%) of `--*` custom property declarations."""
    if core.C.theme_paths and any(rel.startswith(p) for p in core.C.theme_paths):
        return True
    if DEFAULT_THEME_RE.search(rel) is not None:
        return True
    if source is not None and Path(rel).suffix.lower() in STYLE_EXT:
        return _is_css_token_only_file(source)
    return False


def _own_debloat_case(path, rel):
    return not core.SELFTEST and (path.resolve().is_relative_to(CASES) or core.own_cases(path, rel))


def _tracked_candidates(root=None, exts=SCAN_EXT):
    base = Path(root or core.REPO).resolve()
    import patterns as mod_patterns
    for rel in mod_patterns.tracked(base):
        path = (base / rel).resolve()
        suffix = path.suffix.lower()
        if suffix not in exts or _own_debloat_case(path, rel):
            continue
        if core.C.skip_dirs & set(Path(rel).parts) or core.is_test(rel):
            continue
        if not path.is_file() or path.stat().st_size > core.MAX_BYTES:
            continue
        yield rel, path, suffix


def _is_target_file(rel, suffix, source):
    if suffix in STYLE_EXT:
        return not is_theme_file(rel, source)
    if suffix in MARKUP_EXT:
        return True
    return core.is_ui(rel, source)


def repo_ui_files(root=None):
    """All non-test, non-theme UI and stylesheet files under root."""
    out = []
    for rel, path, suffix in _tracked_candidates(root):
        source = path.read_text(encoding="utf-8", errors="replace")
        if _is_target_file(rel, suffix, source):
            out.append((rel, path, source))
    return out


VARIANT_PARAM_RE = re.compile(r"\b(?:is_)?(?:primary|active|selected|highlighted)\s*:\s*bool\b|\bvariant\s*:")


def _scan_kit_primary_helpers(source, primary_helpers, non_primary_helpers):
    """Detects exported Rust UI kit helpers whose implementation unconditionally paints a solid p.primary fill."""
    lines = source.splitlines()
    test_from = core.rust_test_start(lines)
    spans = _function_spans(lines, ".rs", test_from)
    bodies, pub_names, direct = {}, set(), set()
    for start, name, end in spans:
        if re.match(r"^(?:render|build|view|main$|new$)", name):
            continue
        decl = lines[start - 1]
        if re.match(r"^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?fn\b", decl):
            pub_names.add(name)
        sig = " ".join(lines[start - 1:min(end, start + 3)])
        body = "\n".join(lines[start:end])
        bodies[name] = body
        is_variant_toggle = bool(VARIANT_PARAM_RE.search(sig)) and bool(
            re.search(r"\b\w+\.(?:surface|selected|card|bg|muted)\b", body)
        )
        if not is_variant_toggle and PRIMARY_FILL_BODY_RE.search(body):
            direct.add(name)
    propagated = set(direct)
    if direct:
        call_re = re.compile(r"(?<![.\w])(" + "|".join(re.escape(n) for n in direct) + r")\s*\(")
        for name, body in bodies.items():
            if name not in propagated and call_re.search(body):
                propagated.add(name)
    for name in pub_names:
        if name in propagated:
            primary_helpers.add(name)
            non_primary_helpers.discard(name)
        elif name not in primary_helpers:
            non_primary_helpers.add(name)


WEB_BUTTON_KIT_RE = re.compile(r"(?i)(?:^|/)(?:components/)?ui/button\.(?:tsx|jsx|ts|js|vue)$")
CVA_VARIANT_BLOCK_RE = re.compile(r"\bvariant\s*:\s*\{")
CVA_VARIANT_ENTRY_RE = re.compile(r"""(?m)^\s*["']?([A-Za-z][\w-]*)["']?\s*:\s*(["'`])([^"'`]*)\2""")
CVA_DEFAULT_RE = re.compile(r"""defaultVariants\s*:\s*\{[^}]*\bvariant\s*:\s*["']([\w-]+)["']""")
JSX_BUTTON_RE = re.compile(r"<Button\b([^>]*)>")


def _scan_web_button_variants(source):
    """shadcn-style `components/ui/button`: the cva() variants painted with a solid bg-primary, as
    `<Button:variant>` sentinels, plus `<Button>` when defaultVariants makes a bare <Button> primary."""
    out = set()
    m = CVA_VARIANT_BLOCK_RE.search(source)
    if not m:
        return out
    block_end = source.find("}", m.end())
    for em in CVA_VARIANT_ENTRY_RE.finditer(source[m.end():block_end]):
        if re.search(r"(?<![\w:-])bg-primary(?![\w/-])", em.group(3)):
            out.add(f"<Button:{em.group(1)}>")
    d = CVA_DEFAULT_RE.search(source)
    if d and f"<Button:{d.group(1)}>" in out:
        out.add("<Button>")
    return out


def _is_kit_primary_button(code, primary_helpers):
    """A <Button> whose variant (or the cva default when none is given) the button kit paints primary."""
    for m in JSX_BUTTON_RE.finditer(code):
        v = re.search(r"""\bvariant\s*=\s*\{?\s*["']([\w-]+)["']""", m.group(1))
        if (f"<Button:{v.group(1)}>" if v else "<Button>") in primary_helpers:
            return True
    return False


def _discover_kit_helpers(root=None):
    """Lightweight pass over Rust kit/component files (and a web ui/button kit) so lean hooks match the CLI."""
    primary_helpers, non_primary_helpers = set(), set()
    for rel, path, suffix in _tracked_candidates(root, exts={".rs"} | set(WEB_EXT)):
        if suffix == ".rs" and KIT_HELPER_FILE_RE.search(rel):
            _scan_kit_primary_helpers(
                path.read_text(encoding="utf-8", errors="replace"),
                primary_helpers,
                non_primary_helpers,
            )
        elif suffix in WEB_EXT and WEB_BUTTON_KIT_RE.search(rel):
            primary_helpers.update(_scan_web_button_variants(path.read_text(encoding="utf-8", errors="replace")))
    return primary_helpers, non_primary_helpers


def discover_project_tokens(root=None):
    """Detects palette, semantic Tailwind tokens, typography, radius tokens, tooltips, and kit primary-fill helpers."""
    tokens = {
        "has_palette": bool(core.C.design.get("style_tokens")),
        "has_tw_semantic_colors": bool(core.C.design.get("style_tokens")),
        "has_font_tokens": bool(core.C.design.get("style_tokens")),
        "has_radius_tokens": False,
        "uses_tooltips": bool(core.C.raw.get("tooltip_calls")),
        "primary_helpers": set(),
        "non_primary_helpers": set(),
    }
    candidates = list(_tracked_candidates(root))
    theme_first = [c for c in candidates if is_theme_file(c[0]) or c[2] in STYLE_EXT or "tailwind.config" in Path(c[0]).name.lower()]
    kit_rs = [c for c in candidates if c[2] == ".rs" and KIT_HELPER_FILE_RE.search(c[0])]
    prioritized = list(dict.fromkeys(theme_first + kit_rs))
    prioritized_set = set(prioritized)
    general = [c for c in candidates if c not in prioritized_set]

    def inspect(rel, suffix, source):
        is_tw_cfg = "tailwind.config" in Path(rel).name.lower()
        is_theme = is_theme_file(rel, source) or suffix in STYLE_EXT or is_tw_cfg
        if (re.search(r"\bPalette\b|\bcreateTheme\b|--bg\b|--surface\b|--[\w-]*(?:color|bg|surface|accent|card)\s*:|\bp\.(surface|card|text|primary)\b|\b(?:bg|c|b--)-(?:base|action-primary|muted-[1-5]|on-base|danger|warning|success)\b", source)
                or (is_theme and re.search(r"\bcolors\s*:", source))):
            tokens["has_palette"] = True
        if ((is_tw_cfg and re.search(r"\bcolors\s*:", source))
                or (suffix in STYLE_EXT and re.search(r"@theme\b[\s\S]*--color-", source))
                or (suffix in WEB_EXT and TW_SEMANTIC_COLOR_RE.search(source))):
            tokens["has_tw_semantic_colors"] = True
        if (re.search(r"\b(?:visual|typography)::(?:BODY|FOOTNOTE|CAPTION|EMPHASIS|HEADLINE|TITLE)\b|--[\w-]*(?:font|text)[-_]?size\b|typography\.|\btext-(?:xs|sm|base|lg|xl|2xl)\b|\bt-(?:heading-[1-6]|body|small|mini|action)\b", source)
                or (is_theme and re.search(r"\b(?:BODY|FOOTNOTE|CAPTION|EMPHASIS|HEADLINE|TITLE|fontSize)\b", source))):
            tokens["has_font_tokens"] = True
        if re.search(r"\b\w+_radius\b|--[\w-]*radius\b|\brounded\w*:\s*[\d.]+|card_radius|control_radius|\brounded-(?:sm|md|lg|xl|2xl)\b|\bbr[0-4]\b", source):
            tokens["has_radius_tokens"] = True
        if core.C.tooltip_call.search(source) or re.search(r"<Tooltip\b|data-tooltip|\btitle=", source):
            tokens["uses_tooltips"] = True
        if suffix == ".rs" and KIT_HELPER_FILE_RE.search(rel):
            _scan_kit_primary_helpers(source, tokens["primary_helpers"], tokens["non_primary_helpers"])
        if suffix in WEB_EXT and WEB_BUTTON_KIT_RE.search(rel):
            tokens["primary_helpers"].update(_scan_web_button_variants(source))

    for rel, path, suffix in prioritized:
        inspect(rel, suffix, path.read_text(encoding="utf-8", errors="replace"))
    checked = 0
    for rel, path, suffix in general:
        source = path.read_text(encoding="utf-8", errors="replace")
        if not _is_target_file(rel, suffix, source):
            continue
        inspect(rel, suffix, source)
        checked += 1
        if checked >= 50 and all(tokens[k] for k in ("has_palette", "has_tw_semantic_colors", "has_font_tokens", "has_radius_tokens", "uses_tooltips")):
            break
    return tokens


def find_screens(query, root=None):
    """Matches screen files by path, symbol, or visible UI title and lists sibling screen files."""
    terms = [t for t in core.plain(query).split() if len(t) >= 2]
    if not terms:
        return []
    files = repo_ui_files(root)
    by_rel = {rel: path for rel, path, _ in files}
    scored = []
    for rel, path, source in files:
        norm_rel = core.plain(rel)
        score = sum(8 for t in terms if f" {t} " in f" {norm_rel} ") + sum(4 for t in terms if t in norm_rel)
        symbols = " ".join(re.findall(r"\b(?:struct|fn|class|function|const)\s+([A-Za-z_]\w*)", source))
        norm_sym = core.plain(symbols)
        score += sum(3 for t in terms if t in norm_sym)
        if score == 0:
            titles = " ".join(re.findall(r'"([^"\n]{3,80})"', source[:12000]))
            norm_titles = core.plain(titles)
            score += sum(2 for t in terms if t in norm_titles)
        if score > 0:
            if re.search(r"(^|/)(?:screens|views|pages|dialogs|modals)/", rel):
                score += 3
            elif re.search(r"(^|/)components/", rel):
                score += 2
            if "view" in Path(rel).stem:
                score += 1
            if re.search(r"<[A-Za-z]", source):
                score += 2
            if re.search(r"(^|/)(?:hooks|utils|typings|services|graphql)/", rel):
                score = max(1, score - 6)
            elif len(source.splitlines()) <= 15 and not re.search(r"<[A-Za-z]", source):
                score = max(1, score - 4)
            if path.suffix.lower() in STYLE_EXT:
                score = max(1, score - 2)
            siblings = sorted(r for r in by_rel if Path(r).parent == Path(rel).parent and r != rel)
            scored.append((score, rel, siblings[:8]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[:10]


def _container_indent(line):
    indent = len(line) - len(line.lstrip())
    stripped = line.lstrip()
    return max(0, indent - 4) if stripped.startswith(".") else indent


def _function_spans(lines, suffix, test_from, fallback_name="view"):
    """Splits UI file lines into (start_line, name, end_line) spans keyed by render_owner
    (and FN_HEAD_RE for export default / React.FC components, or a whole-file span for Vue/Svelte)."""
    limit = (test_from - 1) if (suffix == ".rs" and test_from) else len(lines)
    spans = []
    current = None
    for number in range(1, limit + 1):
        line = lines[number - 1]
        owner = copy_rules.render_owner(lines, number)
        if suffix in WEB_EXT:
            hm = FN_HEAD_RE.match(line)
            if hm:
                hname = hm.group(1) or hm.group(2)
                if hname:
                    owner = (number, hname)
        if owner[0] == 0:
            if current is not None and suffix in WEB_EXT:
                continue
            continue
        if current is None or owner != current[:2]:
            if current is not None:
                spans.append((current[0], current[1], number - 1))
            current = (owner[0], owner[1])
    if current is not None:
        spans.append((current[0], current[1], limit))
    if not spans and suffix in MARKUP_EXT and limit > 0:
        spans.append((1, fallback_name, limit))
    return spans


def _is_text_stack_line(lines, number, raw, suffix):
    lit_suffix = ".tsx" if suffix in MARKUP_EXT else suffix
    if TEXT_STACK_CALL_RE.search(raw) and any(len(lit.split()) >= 4 for lit in core.literals(raw, lit_suffix)):
        return True
    if raw.rstrip().endswith((".child(", ".child([")) and number < len(lines):
        nxt = lines[number]
        if ("div()" in nxt or "hint_text" in nxt or ".text_size(" in nxt) and any(
            len(lit.split()) >= 4 for lit in core.literals(nxt, lit_suffix)
        ):
            return True
    # Multi-line JSX/HTML text element: `<p>\n  Several words of prose.\n</p>`
    if suffix in WEB_EXT:
        om = TEXT_OPEN_TAG_RE.search(raw)
        if om and f"</{om.group(1)}>" not in raw:
            for offset in range(1, 4):
                if number - 1 + offset >= len(lines):
                    break
                nxt = lines[number - 1 + offset]
                if f"</{om.group(1)}>" in nxt:
                    if any(len(lit.split()) >= 4 for lit in core.literals(nxt, lit_suffix)):
                        return True
                    break
                if any(len(lit.split()) >= 4 for lit in core.literals(nxt, lit_suffix)):
                    return True
    return False


NONE_ARG_RE = re.compile(
    r"^(?:None|null|undefined)$|^(?:hint|help|tooltip)\s*[:=]\s*(?:None|null|undefined)$"
)
JSX_OPEN_TAG_RE = re.compile(
    r"<(?:[A-Z]\w*|(?:div|section|aside|article|main|header|footer|nav|ul|ol|li|form|table|p|span)\b)(?=[\s/>]|$)"
)
JSX_CLOSE_TAG_RE = re.compile(r"</[A-Za-z]\w*>|/>")


def _has_top_level_none(lines, number, open_col, suffix):
    """Checks across wrapped arguments whether this call directly passes None/null/undefined."""
    span_text, _ = copy_rules.call_span(lines, number, open_col, limit=25)
    if not re.search(r"\b(?:None|null|undefined)\b", span_text):
        return False
    clean_lines = []
    for ln in span_text.splitlines():
        if core.comment_of(ln, suffix) is not None:
            continue
        stripped_lit = core.strip_literals(ln)
        stripped_lit = re.sub(r"#.*$" if suffix == ".py" else r"//.*$", "", stripped_lit)
        clean_lines.append(stripped_lit)
    if not clean_lines:
        return False
    return any(NONE_ARG_RE.search(arg.strip()) for arg in core.split_top("\n".join(clean_lines)))


def _aggregate_branches(entries, widths=1):
    """Combines counts across mutually exclusive (group_id, arm) paths via max()."""
    base = [0] * widths if widths > 1 else 0
    by_group = defaultdict(lambda: defaultdict(list))
    for path, val in entries:
        if not path:
            if widths > 1:
                for i in range(widths):
                    base[i] += val[i]
            else:
                base += val
        else:
            gid, arm = path[0]
            by_group[gid][arm].append((path[1:], val))
    for arms in by_group.values():
        arm_totals = [_aggregate_branches(arm_entries, widths) for arm_entries in arms.values()]
        if widths > 1:
            for i in range(widths):
                base[i] += max((t[i] for t in arm_totals), default=0)
        else:
            base += max(arm_totals, default=0)
    return base


def _aggregate_branches_touched(entries, widths):
    """Combines (counts, touched_max, has_touched) across mutually exclusive branches so an edited clean
    branch cannot trigger a pre-existing finding from an untouched sibling branch."""
    base_all = [0] * widths
    base_t = [0] * widths
    base_has = [False] * widths
    by_group = defaultdict(lambda: defaultdict(list))
    for path, counts, t_max, has_t in entries:
        if not path:
            for i in range(widths):
                prev_all, prev_t, prev_has = base_all[i], base_t[i], base_has[i]
                base_all[i] = prev_all + counts[i]
                if prev_has and has_t[i]:
                    base_t[i] = prev_t + t_max[i]
                    base_has[i] = True
                elif prev_has:
                    base_t[i] = prev_t + counts[i]
                    base_has[i] = True
                elif has_t[i]:
                    base_t[i] = prev_all + t_max[i]
                    base_has[i] = True
        else:
            gid, arm = path[0]
            by_group[gid][arm].append((path[1:], counts, t_max, has_t))
    for arms in by_group.values():
        arm_res = [_aggregate_branches_touched(arm_entries, widths) for arm_entries in arms.values()]
        for i in range(widths):
            grp_all = max((a[i] for a, _, _ in arm_res), default=0)
            grp_has = any(h[i] for _, _, h in arm_res)
            grp_t = max((t[i] for _, t, h in arm_res if h[i]), default=0)
            prev_all, prev_t, prev_has = base_all[i], base_t[i], base_has[i]
            base_all[i] = prev_all + grp_all
            if prev_has:
                base_t[i] = prev_t + grp_all
            elif grp_has:
                base_t[i] = prev_all + grp_t
                base_has[i] = True
    return base_all, base_t, base_has


_BRANCH_KEYWORDS = {"let", "true", "false", "True", "False", "None", "null", "undefined"}


def _branch_cond(raw, suffix):
    """Extracts a canonical (group_key, arm_tag, is_binary) for simple single-subject if/elif headers."""
    clean = re.sub(r"#.*$" if suffix == ".py" else r"//.*$", "", raw).strip()
    if suffix == ".py":
        m = re.match(r"^(?:el)?if\b\s*(.+?)\s*:\s*$", clean)
    else:
        m = re.search(r"(?:^|[\s=}(])(?:else\s+)?if\b\s*(.+?)\s*\{\s*$", clean)
    if not m:
        return None
    cond = m.group(1).strip()
    while cond.startswith("(") and cond.endswith(")"):
        depth, balanced = 0, True
        for i, ch in enumerate(cond):
            depth += 1 if ch == "(" else (-1 if ch == ")" else 0)
            if depth == 0 and i < len(cond) - 1:
                balanced = False
                break
        if not balanced or depth != 0:
            break
        cond = cond[1:-1].strip()
    if not cond or re.search(r"&&|\|\||\band\b|\bor\b|[;?]", cond):
        return None
    opt_let = re.fullmatch(r"let\s+(Some\s*\([^)]*\)|None)\s*=\s*&?(?:mut\s+)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", cond)
    if opt_let:
        return (("opt", opt_let.group(2)), 0 if opt_let.group(1) == "None" else 1, True)
    opt_call = re.fullmatch(r"(!)?\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.(is_some|is_none)\(\)", cond)
    if opt_call:
        arm = 1 if opt_call.group(3) == "is_some" else 0
        if opt_call.group(1):
            arm = 1 - arm
        return (("opt", opt_call.group(2)), arm, True)
    py_none = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+is\s+(not\s+)?None", cond)
    if py_none:
        return (("opt", py_none.group(1)), 1 if py_none.group(2) else 0, True)
    eq_m = re.fullmatch(
        r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*===?\s*([A-Za-z0-9_:\.\-]+|\"[^\"\n]*\"|'[^'\n]*')",
        cond,
    )
    if eq_m:
        return (("eq", eq_m.group(1)), eq_m.group(2), False)
    bool_m = re.fullmatch(r"(?:(!)\s*|(not)\s+)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", cond)
    if bool_m and bool_m.group(3) not in _BRANCH_KEYWORDS:
        neg = bool(bool_m.group(1) or bool_m.group(2))
        return (("bool", bool_m.group(3)), 0 if neg else 1, True)
    return None


def _invalidate_cond_groups(cond_groups, code):
    """Drops correlated condition groups whose subject variable is assigned or mutated on this line."""
    if not cond_groups:
        return
    mutated = set()
    for am in re.finditer(r"(?<![=!<>])\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:=|\+=|-=|\*=|/=)(?!=|>)", code):
        mutated.add(am.group(1))
    for mm in re.finditer(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.(?:take|replace|insert|clear|push|pop|set_\w+)\s*\(", code):
        mutated.add(mm.group(1))
    if not mutated:
        return
    for key in list(cond_groups):
        expr = key[1]
        if any(expr == v or expr.startswith(v + ".") or v.startswith(expr + ".") for v in mutated):
            del cond_groups[key]


def _pop_closed_branches(branch_stack, confirmed, indent, stripped, suffix, raw="", cond_groups=None):
    """Pops/advances branch_stack frames ([indent, gid, arm, kind, has_else, has_return, cond_meta]) for current line."""
    for idx in range(len(branch_stack) - 1, -1, -1):
        frame = branch_stack[idx]
        if (frame[3] == "match" and indent <= frame[0] + 4 and "=>" in stripped
                and not stripped.startswith(".") and not re.match(r"^(?:\}\s*)?(?:if|else)\b", stripped)):
            del branch_stack[idx + 1:]
            frame[2] += 1
            confirmed.add(frame[1])
            return True
    while branch_stack and indent < branch_stack[-1][0]:
        branch_stack.pop()
    while branch_stack and indent == branch_stack[-1][0]:
        top = branch_stack[-1]
        if top[3] == "ret_rest":
            break
        if top[3] == "if" and (re.match(r"^(?:\}\s*)?(?:else\b|elif\b)", stripped)
                               or (suffix in core.JSX and re.match(r"^\)\s*:\s*\(", stripped))):
            meta = top[6]
            is_plain_else = bool(re.match(r"^(?:\}\s*)?else\s*[:{]?\s*$", stripped)) or (
                suffix in core.JSX and bool(re.match(r"^\)\s*:\s*\(", stripped))
            )
            if meta is not None and is_plain_else and meta[1]:
                # Plain else of a binary (opt/bool) correlated condition maps to its exact complement.
                top[2] = 1 - top[2]
                if cond_groups and meta[0] in cond_groups:
                    cond_groups[meta[0]][3].add(top[2])
            elif meta is not None and not is_plain_else and cond_groups and meta[0] in cond_groups:
                elif_cond = _branch_cond(raw, suffix)
                grp = cond_groups[meta[0]]
                if elif_cond and elif_cond[0] == meta[0]:
                    arm_tag = elif_cond[1]
                    if arm_tag not in grp[1]:
                        grp[1][arm_tag] = grp[2]
                        grp[2] += 1
                    top[2] = grp[1][arm_tag]
                else:
                    top[2] = grp[2]
                    grp[2] += 1
                grp[3].add(top[2])
            elif meta is not None and cond_groups and meta[0] in cond_groups:
                grp = cond_groups[meta[0]]
                top[2] = grp[2]
                grp[2] += 1
                grp[3].add(top[2])
            else:
                top[2] += 1
            top[4] = True
            confirmed.add(top[1])
            return True
        if stripped.startswith("}") or (suffix == ".py" and not re.match(r"^(?:elif|else|match)\b", stripped)) or (
            suffix in core.JSX and stripped.startswith(")}")
        ):
            popped = branch_stack.pop()
            if popped[3] == "if" and popped[5] and not popped[4]:
                confirmed.add(popped[1])
                meta = popped[6]
                if meta is not None and meta[1]:
                    rest_arm = 1 - popped[2]
                    if cond_groups and meta[0] in cond_groups:
                        cond_groups[meta[0]][3].add(rest_arm)
                elif meta is not None and cond_groups and meta[0] in cond_groups:
                    grp = cond_groups[meta[0]]
                    rest_arm = grp[2]
                    grp[2] += 1
                    grp[3].add(rest_arm)
                else:
                    rest_arm = popped[2] + 1
                branch_stack.append([popped[0], popped[1], rest_arm, "ret_rest", True, False, None])
            continue
        break
    return False


def _blank_literals(line):
    """Replaces string literal contents with same-length spaces to preserve column offsets."""
    line = re.sub(r'"(?:[^"\\\n]|\\.)*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', line)
    line = re.sub(r"(?<![\w&])'(?:[^'\\\n]|\\.){3,}'", lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", line)
    return re.sub(r"\x60[^\x60\n]*\x60", lambda m: "`" + " " * (len(m.group(0)) - 2) + "`", line)


def _extract_imported_icons(source):
    """Extracts component identifiers imported from common React/Vue/Svelte icon libraries."""
    names = set()
    for m in ICON_IMPORT_RE.finditer(source):
        for item in m.group(1).split(","):
            tok = item.strip()
            if not tok:
                continue
            if " as " in tok:
                tok = tok.split(" as ")[-1].strip()
            if re.match(r"^[A-Z]\w*$", tok):
                names.add(tok)
    return names


def _is_tw_pill(line):
    """Detects Tailwind inline pills (rounded-full + text-xs/10-12px + non-transparent bg- in any order),
    excluding interactive pill buttons/links."""
    if not ("rounded-full" in line and TW_PILL_TEXT_RE.search(line) and TW_PILL_BG_RE.search(line)):
        return False
    if re.search(r"<(?:button|a)\b", line, re.I) and not re.search(r"<(?:span|div|p|small|label|li)\b[^>]*rounded-full", line, re.I):
        return False
    return True


def _is_tw_animated_dot(line, context):
    """Detects Tailwind animated status dots (rounded-full + tiny 1.5/2/2.5 square + animate-pulse/ping)
    in any class order, excluding real loading/spinner/skeleton indicators."""
    if "rounded-full" not in line or not TW_DOT_ANIM_RE.search(line):
        return False
    has_size = bool(TW_DOT_SIZE_RE.search(line)) or (bool(TW_DOT_H_RE.search(line)) and bool(TW_DOT_W_RE.search(line)))
    if not has_size:
        return False
    return not TW_DOT_LOADING_RE.search(context)


def _slop_surface_cue(line, is_modal):
    """Composite (>=2 co-occurring cues) decorative gradient/glassmorphism detector on non-modal surfaces."""
    if is_modal or SLOP_MODAL_CONTEXT_RE.search(line):
        return None
    has_grad = bool(SLOP_GRADIENT_RE.search(line))
    has_glass = bool(SLOP_GLASS_CUES_RE.search(line))
    has_frost = bool(SLOP_FROST_BG_RE.search(line))
    if has_grad and has_glass:
        return "decorative gradient/glassmorphism surface"
    if has_frost and "backdrop-blur" in line and re.search(r"\bshadow-(?:xl|2xl)\b", line):
        return "decorative glassmorphism card (backdrop-blur + frost bg + heavy shadow)"
    return None


def _card_padding_px(raw, suffix):
    """Extracts representative padding (px) from a card element line, or None if not explicit."""
    vals = []
    if suffix in WEB_EXT:
        for unit, arb_v, arb_u in CONSIST_CARD_TW_PAD_RE.findall(raw):
            if unit:
                vals.append(float(unit) * 4.0)
            elif arb_v:
                vals.append(float(arb_v) * (16.0 if arb_u == "rem" else 1.0))
    elif suffix == ".rs":
        for px_v, unit_v, half in CONSIST_CARD_RS_PAD_RE.findall(raw):
            if px_v:
                vals.append(float(px_v))
            elif unit_v:
                vals.append(int(unit_v) * 4.0 + (2.0 if half else 0.0))
    return max(vals) if vals else None


def _collect_consistency(raw, suffix, number, radii, font_sizes):
    """Collects distinct non-pill corner radii and font sizes within a single view (full audit only)."""
    if suffix in WEB_EXT:
        for m in CONSIST_TW_RADIUS_RE.finditer(raw):
            tok = m.group(0)
            if tok in ("rounded-full",) or (m.group(1) and float(m.group(1)) >= 999):
                continue
            radii.setdefault(tok, number)
        for m in CONSIST_TW_FONT_RE.finditer(raw):
            font_sizes.setdefault(f"text-{m.group(1)}", number)
    elif suffix == ".rs":
        for px_v, named in CONSIST_RS_RADIUS_RE.findall(raw):
            if px_v:
                r = float(px_v)
                if 0 < r < 999:
                    radii.setdefault(f"{r:g}px", number)
            elif named:
                radii.setdefault(f"rounded_{named}", number)
        for arg, named in CONSIST_RS_FONT_RE.findall(raw):
            if arg:
                font_sizes.setdefault(arg.strip(), number)
            elif named:
                font_sizes.setdefault(f"text_{named}", number)


def _track_line_boxes(raw, code, suffix, delim_state, stack, in_loop, card_lines, raw_items, bp, number, max_depth, max_depth_line, is_decl=False, box_lines=None):
    """Tracks delimiter/JSX nesting alongside indent so single-line chains and wrappers count accurately."""
    stripped = raw.lstrip()
    c_indent = _container_indent(raw)
    indent = len(raw) - len(stripped)
    cur_n = delim_state[0] + delim_state[1]
    while stack and (
        cur_n < stack[-1][0][0]
        or (cur_n == stack[-1][0][0] and (indent < stack[-1][0][1] or (indent == stack[-1][0][1] and not stripped.startswith("."))))
    ):
        stack.pop()

    box_text = raw if suffix in JSX_LIKE else code
    is_micro = MICRO_RE.search(code) is not None
    box_matches = [] if (is_micro or is_decl) else list(STYLED_BOX_RE.finditer(box_text))
    tokens = []  # (pos, kind, match_obj)
    for m in box_matches:
        tokens.append((m.start(), "box", m))
    for pos, ch in enumerate(code):
        if ch in "([":
            tokens.append((pos, "open", None))
        elif ch in ")]":
            tokens.append((pos, "close", None))
    if suffix in JSX_LIKE:
        for m in JSX_OPEN_TAG_RE.finditer(code):
            tokens.append((m.start(), "jsx_open", None))
        for m in JSX_CLOSE_TAG_RE.finditer(code):
            tokens.append((m.start(), "jsx_close", None))
    order = {"box": 0, "jsx_open": 1, "open": 2, "close": 3, "jsx_close": 4}
    tokens.sort(key=lambda t: (t[0], order[t[1]]))

    for pos, kind, m in tokens:
        if kind == "open":
            delim_state[0] += 1
        elif kind == "close":
            delim_state[0] = max(0, delim_state[0] - 1)
            cur_n = delim_state[0] + delim_state[1]
            while stack and cur_n < stack[-1][0][0]:
                stack.pop()
        elif kind == "jsx_open":
            delim_state[1] += 1
        elif kind == "jsx_close":
            delim_state[1] = max(0, delim_state[1] - 1)
            cur_n = delim_state[0] + delim_state[1]
            while stack and (cur_n < stack[-1][0][0] or (stack[-1][4] and cur_n <= stack[-1][0][0])):
                stack.pop()
        elif kind == "box":
            if box_lines is not None:
                box_lines.add(number)
            cur_n = delim_state[0] + delim_state[1]
            box_key = (cur_n, c_indent)
            is_jsx = m.group(0).startswith("<")
            while stack and stack[-1][0] > box_key:
                stack.pop()
            if not stack or stack[-1][0] < box_key:
                stack.append([box_key, number, set(), number in card_lines, is_jsx])
                if len(stack) > max_depth:
                    max_depth, max_depth_line = len(stack), number
            if stack:
                tok = m.group(0)
                if ".bg(" in tok or re.search(r"\bbg-(?!transparent\b)", raw):
                    stack[-1][2].add("bg")
                if (re.search(r"\.(?:border_[1-9]\w*|border_color)\(|\bborder(?!-(?:0|none|transparent)\b)", tok)
                        or re.search(r"\bborder-(?!(?:0|none|transparent)\b)", box_text)):
                    stack[-1][2].add("border")
                if not in_loop and not stack[-1][3] and {"bg", "border"} <= stack[-1][2]:
                    raw_items["cards"].append((bp, stack[-1][1]))
                    card_lines.add(stack[-1][1])
                    stack[-1][3] = True
    return max_depth, max_depth_line


def _is_primary_accent_call(lines, number, raw, code, primary_helpers, non_primary_helpers):
    """Detects solid p.primary fills: direct primary_button/.bg/.pill, discovered kit helpers, or kit::segmented."""
    if PRIMARY_ACCENT_RE.search(raw) or VUE_KIT_PRIMARY_RE.search(raw):
        return True
    if primary_helpers and "<Button" in code and _is_kit_primary_button(raw, primary_helpers):
        return True
    for m in PRIMARY_ARG_CALL_RE.finditer(code):
        span, _ = copy_rules.call_span(lines, number, m.end(), limit=12)
        if re.search(r"(?<!\.\.)\b\w+\.primary\b", span):
            return True
    if primary_helpers:
        for m in re.finditer(r"(?<![.\w])(?:(?:kit|visual|ui)::)?([a-z_]\w*)\s*\(", code):
            if m.group(1) in primary_helpers:
                return True
    for m in QUALIFIED_SEGMENTED_RE.finditer(code):
        fn_name = m.group(1)
        if fn_name not in non_primary_helpers:
            span, _ = copy_rules.call_span(lines, number, m.end(), limit=12)
            if not re.search(r"\b\w+\.(?:selected|surface|muted|card|bg)\b", span):
                return True
    return False


def _analyze_fn(lines, suffix, start, name, end, look, skip, known_callees, primary_helpers=(), non_primary_helpers=(), imported_icons=(), is_modal_file=False):
    cfg = core.C.design
    stack = []  # [[(nesting, indent), line_number, flags, card_counted, is_jsx]]
    branch_stack = []  # [[indent, group_id, arm_idx, kind, has_else, has_return, cond_meta]]
    confirmed = set()
    cond_groups = {}  # cond_key -> [gid, arm_map, next_arm_id, seen_arms]
    delim_state = [0, 0]  # [paren_bracket_depth, jsx_depth]
    next_group = 1
    max_depth, max_depth_line = 0, start
    raw_items = defaultdict(list)  # field -> [(branch_path, line)]
    spacers, gap_calls = [], 0
    header_runs, current_run, raw_calls = [], [], []
    inline_chains = defaultdict(list)
    fixed_width_triggers = []
    slop_findings = []  # (line, problem, fix)
    radii, font_sizes = {}, {}
    card_paddings = []  # (depth, pad_px, line)
    kpi_values = []  # (line, in_loop)
    flat_filter_chips = []
    loop_indent = None
    loop_bp = ()
    loop_primary_seen = False
    loop_filter_chip_seen = False
    card_lines = set()
    box_lines = set()
    has_modal_box = False
    imported_icon_re = (
        re.compile(r"<(?:" + "|".join(re.escape(ic) for ic in sorted(imported_icons)) + r")\b")
        if imported_icons else None
    )
    fn_modal_hint = is_modal_file or bool(MODAL_CALLEE_RE.search(name))

    for number in range(start, end + 1):
        if number in skip:
            continue
        raw = lines[number - 1]
        if core.comment_of(raw, suffix) is not None or not raw.strip():
            continue
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)

        if loop_indent is not None and indent <= loop_indent and stripped not in ("{", "}", ");", ")", "})}", ")}", ")}"):
            loop_indent = None
            loop_primary_seen = False
            loop_filter_chip_seen = False
        started_loop = False
        if loop_indent is None and LOOP_START_RE.search(raw):
            loop_indent = indent
            loop_bp = tuple((f[1], f[2]) for f in branch_stack)
            loop_primary_seen = False
            loop_filter_chip_seen = False
            started_loop = True
        in_loop = (loop_indent is not None and indent > loop_indent) or (started_loop and bool(FILTER_CHIP_RE.search(raw)))

        continued = _pop_closed_branches(branch_stack, confirmed, indent, stripped, suffix, raw, cond_groups)
        code = _blank_literals(raw)
        _invalidate_cond_groups(cond_groups, code)
        if branch_stack and branch_stack[-1][3] == "if" and indent <= branch_stack[-1][0] + 4 and re.search(r"\breturn\b", code):
            branch_stack[-1][5] = True

        single_ret_if = (not continued and re.search(r"(?:^|\s)if\b.*\{.*\breturn\b.*\}", code)
                         and not re.search(r"\belse\b", code))
        opened_branch = None
        if single_ret_if:
            gid = next_group
            next_group += 1
            confirmed.add(gid)
            opened_branch = ("single_ret", gid, 0, None)
        elif re.search(r"\bmatch\b.*\{\s*$", code) or (suffix == ".py" and re.match(r"^match\b.*:\s*$", stripped)):
            opened_branch = ("match", next_group, 0, None)
            confirmed.add(next_group)
            next_group += 1
        elif not continued and (
            re.search(r"(?:^|[\s=(])if\b[^{};]*\{\s*$", code)
            or (suffix == ".py" and re.match(r"^if\b.*:\s*$", stripped))
            or (suffix in JSX_LIKE and re.search(r"\{\s*[^?{}]+\?\s*\(\s*$", code))
        ):
            cond_info = None if (suffix in JSX_LIKE and "?" in code) else _branch_cond(raw, suffix)
            active_gids = {f[1] for f in branch_stack}
            if cond_info is not None:
                ckey, arm_tag, is_binary = cond_info
                existing = cond_groups.get(ckey)
                if existing is not None and existing[0] not in active_gids:
                    gid, arm_map, next_arm, seen_arms = existing
                    if is_binary:
                        arm = arm_tag
                    else:
                        if arm_tag not in arm_map:
                            arm_map[arm_tag] = next_arm
                            existing[2] = next_arm + 1
                        arm = arm_map[arm_tag]
                    seen_arms.add(arm)
                    if len(seen_arms) >= 2:
                        confirmed.add(gid)
                    opened_branch = ("if", gid, arm, (ckey, is_binary))
                else:
                    gid = next_group
                    next_group += 1
                    if is_binary:
                        arm = arm_tag
                        cond_groups[ckey] = [gid, {0: 0, 1: 1}, 2, {arm}]
                    else:
                        arm = 0
                        cond_groups[ckey] = [gid, {arm_tag: 0}, 1, {0}]
                    opened_branch = ("if", gid, arm, (ckey, is_binary))
            else:
                gid = next_group
                next_group += 1
                if suffix in JSX_LIKE and "?" in code:
                    confirmed.add(gid)
                opened_branch = ("if", gid, 0, None)

        line_bp = tuple((f[1], f[2]) for f in branch_stack)
        if opened_branch and opened_branch[0] == "single_ret":
            eval_bp = line_bp + ((opened_branch[1], 0),)
        else:
            eval_bp = line_bp

        is_decl = number == start and FN_DECL_RE.match(raw) is not None
        if ".gap(" in raw or re.search(r"\bgap-\d+", raw):
            gap_calls += 1
        if not is_decl:
            _collect_consistency(raw, suffix, number, radii, font_sizes)
            slop_msg = _slop_surface_cue(raw, fn_modal_hint or has_modal_box)
            if slop_msg:
                slop_findings.append((
                    number,
                    f"{name}: {slop_msg}",
                    "use plain theme surface and border tokens instead of decorative gradients, blur, or heavy glow",
                ))
            if SLOP_GRAD_CLIP_RE.search(raw) and SLOP_GRAD_TRANS_RE.search(raw):
                slop_findings.append((
                    number,
                    f"{name}: decorative gradient text (bg-clip-text)",
                    "use a plain theme text token and show hierarchy via weight or size",
                ))
            kpi_m = KPI_VALUE_RE.search(raw) if suffix in WEB_EXT else None
            if kpi_m and not KPI_NON_METRIC_TAG_RE.match(kpi_m.group(0)):
                label_ctx = "\n".join(lines[max(0, number - 3):min(len(lines), number + 2)])
                if KPI_LABEL_RE.search(label_ctx) and not KPI_PRICE_PERIOD_RE.search(label_ctx):
                    kpi_values.append((number, in_loop))
            dot_ctx = "\n".join(lines[max(0, number - 3):min(len(lines), number + 2)])
            if suffix in WEB_EXT and _is_tw_animated_dot(raw, dot_ctx):
                slop_findings.append((
                    number,
                    f"{name}: decorative pulsing status dot",
                    "colour only the status word with a semantic token; keep pulse animations for real loading states",
                ))

        if in_loop:
            if not loop_primary_seen and _is_primary_accent_call(lines, number, raw, code, primary_helpers, non_primary_helpers):
                raw_items["primary_accents"].append((loop_bp, number))
                loop_primary_seen = True
            if not loop_filter_chip_seen and FILTER_CHIP_RE.search(raw):
                raw_items["filter_chip_strips"].append((loop_bp, number))
                loop_filter_chip_seen = True
        elif not is_decl:
            if MODAL_BOX_RE.search(raw):
                has_modal_box = True
            for _ in DIVIDER_RE.finditer(raw):
                raw_items["dividers"].append((eval_bp, number))
            badge_hits = len(list(BADGE_RE.finditer(raw)))
            if badge_hits:
                for _ in range(badge_hits):
                    raw_items["badges"].append((eval_bp, number))
            elif suffix in WEB_EXT and _is_tw_pill(raw):
                raw_items["badges"].append((eval_bp, number))
            icon_hits = len(list(ICON_RE.finditer(raw)))
            if imported_icon_re is not None:
                icon_hits += len(list(imported_icon_re.finditer(raw)))
            for _ in range(icon_hits):
                raw_items["icons"].append((eval_bp, number))
            if INFO_HINT_RE.search(raw):
                raw_items["info_hints"].append((eval_bp, number))
            if _is_primary_accent_call(lines, number, raw, code, primary_helpers, non_primary_helpers):
                raw_items["primary_accents"].append((eval_bp, number))
            if HEADLINE_RE.search(raw):
                raw_items["headlines"].append((eval_bp, number))
            if FILTER_CHIP_RE.search(raw):
                flat_filter_chips.append((eval_bp, number))
            if FILTER_TRIGGER_RE.search(raw):
                raw_items["filter_triggers"].append((eval_bp, number))
                window_lines = "\n".join(lines[number - 1:min(end, number + 8)])
                wm = FIXED_TRIGGER_WIDTH_RE.search(window_lines)
                if wm:
                    width = int(float(wm.group(1) or wm.group(2) or wm.group(3)))
                    if width >= 140:
                        fixed_width_triggers.append((number, width))
            spacer_m = SPACER_RE.search(raw)
            if spacer_m and float(spacer_m.group("px") or spacer_m.group("egui_px") or 20) >= 16:
                spacers.append(number)
            if INNER_CARD_CALL_RE.search(raw):
                raw_items["inner_cards"].append((eval_bp, number))
            if CARD_CALL_RE.search(raw):
                raw_items["cards"].append((eval_bp, number))
                card_lines.add(number)

        # Track calls to other UI helpers (local or sibling module) with nesting depth & branch path.
        call_depth = len(stack)
        for cm in re.finditer(r"(?:\b([A-Za-z_]\w*)::|\bself\.)?\b([A-Za-z_]\w*)\s*\(|<([A-Z]\w*)\b", code):
            qual = cm.group(1) or ""
            target = cm.group(2) or cm.group(3)
            if target in known_callees and target != name:
                if cm.group(2):
                    none_hint = _has_top_level_none(lines, number, cm.end(), suffix)
                else:
                    none_hint = re.search(r"\b(?:null|undefined|None)\b|\bhint=\{?(?:null|undefined|false)\b", code) is not None
                raw_calls.append((qual, target, call_depth, number, eval_bp, none_hint))

        chain_m = re.findall(r"\.(rounded\w*|bg|border\w*|shadow\w*|px?\d?|py?\d?)\(", raw)
        if len(chain_m) >= 3:
            inline_chains[tuple(chain_m[:4])].append(number)

        prev_cards = len(card_lines)
        max_depth, max_depth_line = _track_line_boxes(
            raw, code, suffix, delim_state, stack, in_loop, card_lines, raw_items, eval_bp,
            number, max_depth, max_depth_line, is_decl=is_decl, box_lines=box_lines,
        )
        if not in_loop and not is_decl:
            pad = _card_padding_px(raw, suffix)
            if pad is not None:
                if len(card_lines) > prev_cards or number in card_lines:
                    card_paddings.append((len(stack), pad, number))
                elif suffix in WEB_EXT and TW_CARD_LIKE_RE.search(raw):
                    card_paddings.append((len(stack), pad, number))
                elif suffix == ".rs" and stack and stack[-1][1] in card_lines and raw.lstrip().startswith(".") and ".child" not in raw:
                    card_paddings.append((len(stack), pad, stack[-1][1]))

        if opened_branch:
            kind, gid, arm, cond_meta = opened_branch
            if kind == "single_ret":
                branch_stack.append([indent, gid, 1, "ret_rest", True, False, None])
            else:
                branch_stack.append([indent, gid, arm, kind, kind != "if" or (suffix in JSX_LIKE and "?" in code), False, cond_meta])

        if _is_text_stack_line(lines, number, raw, suffix):
            current_run.append(number)
        elif stripped not in ("{", "}", ");", ")") and not (
            current_run and suffix in WEB_EXT and (TEXT_CLOSE_ONLY_RE.match(stripped) or stripped.startswith('{"'))
        ):
            if len(current_run) > cfg["max_header_stack"]:
                header_runs.append(list(current_run))
            current_run.clear()
    if len(current_run) > cfg["max_header_stack"]:
        header_runs.append(current_run)
    if len(flat_filter_chips) >= 3:
        raw_items["filter_chip_strips"].append(flat_filter_chips[0])

    clean_bp = lambda bp: tuple(pair for pair in bp if pair[0] in confirmed)
    count_keys = (
        "cards", "inner_cards", "dividers", "badges", "icons", "info_hints",
        "primary_accents", "headlines", "filter_triggers", "filter_chip_strips",
    )
    items = {k: [n for _, n in raw_items[k]] for k in count_keys}
    counts = {k: _aggregate_branches([(clean_bp(bp), 1) for bp, _ in raw_items[k]]) for k in count_keys}
    touched_counts = {k: any(look(n) for n in items[k]) for k in count_keys}
    local_entries = []
    for idx, k in enumerate(count_keys):
        for bp, ln in raw_items[k]:
            vec = [0] * len(count_keys)
            vec[idx] = 1
            hit = look(ln)
            t_vec = list(vec) if hit else [0] * len(count_keys)
            h_vec = [False] * len(count_keys)
            h_vec[idx] = hit
            local_entries.append((clean_bp(bp), vec, t_vec, h_vec))
    calls = [(qual, target, d, look(ln), clean_bp(bp), nh) for qual, target, d, ln, bp, nh in raw_calls]

    return {
        "start": start, "end": end, "name": name,
        "max_depth": max_depth, "max_depth_line": max_depth_line,
        "touched_depth": any(look(n) for n in box_lines),
        "has_modal_box": has_modal_box,
        **items, "counts": counts, "touched_counts": touched_counts,
        "local_entries": local_entries, "spacers": spacers,
        "fixed_width_triggers": fixed_width_triggers,
        "slop_findings": slop_findings,
        "radii": radii, "font_sizes": font_sizes, "card_paddings": card_paddings,
        "kpi_values": kpi_values,
        "gap_calls": gap_calls, "header_runs": header_runs,
        "inline_chains": inline_chains, "calls": calls,
        "touched": any(look(n) for n in range(start, end + 1)),
    }


def _emit_fn_findings(rel, stats, uses_tooltips, look, out, is_modal_file=False, include_consistency=True):
    cfg = core.C.design
    name = stats["name"]
    if not stats["touched"]:
        return
    if stats["max_depth"] > cfg["max_container_depth"] and stats["touched_depth"]:
        out.append(core.Finding(
            rel, stats["max_depth_line"], "design",
            f"{name}: overloaded hierarchy with {stats['max_depth']} nested styled boxes (limit {cfg['max_container_depth']})",
            "flatten wrappers or remove inner card borders and backgrounds",
        ))
    count_specs = [
        ("cards", "max_cards", "boxed cards/panels in one view",
         "group sections with whitespace and typography instead of wrapping every block in a box"),
        ("dividers", "max_dividers", "dividers/separators in one view",
         "replace horizontal rules with spacing between groups"),
        ("badges", "max_badges", "badges/chips cluttering one view",
         "keep only status chips that drive user action"),
        ("icons", "max_icons", "icons in one view",
         "drop decorative icons where labels already explain the element"),
        ("info_hints", "max_info_hints", "info_hint badges in one view",
         "make labels self-explanatory and keep help icons only for non-obvious behavior"),
        ("primary_accents", "max_primary_accents", "competing primary accents in one view",
         "keep solid primary accent for the main CTA only; use neutral/surface fill for selected filters and toggles"),
        ("headlines", "max_headlines", "competing HEADLINE headings in one view",
         "keep one primary headline for the view/modal and step down inner section titles"),
        ("filter_triggers", "max_exposed_filters", "exposed filter/sort dropdowns in one view",
         "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set"),
    ]
    for field, limit_key, desc, fix in count_specs:
        items = stats[field]
        eff = stats["counts"][field]
        limit = cfg[limit_key]
        if eff > limit and any(look(n) for n in items):
            out.append(core.Finding(rel, items[0], "design", f"{name}: {eff} {desc} (limit {limit})", fix))
    fn_is_modal = is_modal_file or bool(MODAL_CALLEE_RE.search(name)) or stats["has_modal_box"]
    if fn_is_modal:
        inner_hit = stats["counts"]["inner_cards"] >= 1 and any(look(n) for n in stats["inner_cards"])
        nested_modal_cards = (
            stats["counts"]["cards"] >= 2
            and (stats["has_modal_box"] or stats["max_depth"] >= 2)
            and any(look(n) for n in stats["cards"])
        )
        if inner_hit or nested_modal_cards:
            line_no = stats["inner_cards"][0] if inner_hit else stats["cards"][-1]
            out.append(core.Finding(
                rel, line_no, "design",
                f"{name}: inner boxed card inside a modal (nested surface)",
                "flatten the inner card using whitespace and typography; the modal itself is already a card",
            ))
    if stats["counts"]["filter_triggers"] >= 1 and stats["counts"]["filter_chip_strips"] >= 1:
        hit_lines = stats["filter_triggers"] + stats["filter_chip_strips"]
        if any(look(n) for n in hit_lines):
            out.append(core.Finding(
                rel, min(hit_lines), "design",
                f"{name}: stacked filter controls ({stats['counts']['filter_triggers']} trigger(s) + exposed filter-chip strip)",
                "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set",
            ))
    for line_no, width in stats["fixed_width_triggers"]:
        if look(line_no):
            out.append(core.Finding(
                rel, line_no, "design",
                f"{name}: fixed-width filter/sort trigger ({width}px) crowds toolbar",
                "size trigger to content or consolidate secondary filters into a single Filters menu",
            ))
    seen_slop = set()
    for line_no, prob, fix in stats["slop_findings"]:
        if look(line_no) and prob not in seen_slop:
            seen_slop.add(prob)
            out.append(core.Finding(rel, line_no, "design", prob, fix))
    kpis = stats.get("kpi_values", [])
    kpi_lines = [ln for ln, _ in kpis]
    if (len(kpis) >= 3 or any(in_loop for _, in_loop in kpis)) and any(look(ln) for ln in kpi_lines):
        n_desc = f"{len(kpis)} metric cards" if not any(in_loop for _, in_loop in kpis) else "a mapped row of metric cards"
        out.append(core.Finding(
            rel, kpi_lines[0], "design",
            f"{name}: KPI stat strip ({n_desc}: small label + large number)",
            "keep a metric strip only on a dashboard the user asked for; otherwise show the one number the workflow needs inline, or drop the strip",
        ))
    if stats["gap_calls"] > 0 and len(stats["spacers"]) >= 2 and any(look(n) for n in stats["spacers"]):
        out.append(core.Finding(
            rel, stats["spacers"][0], "design",
            f"{name}: {len(stats['spacers'])} explicit spacer boxes inside gap-spaced layout",
            "adjust container gap/padding tokens instead of inserting blank spacer blocks",
        ))
    tip_hint = "or move secondary notes into a tooltip" if uses_tooltips else "or shorten to one line"
    for run in stats["header_runs"]:
        if any(look(n) for n in run):
            out.append(core.Finding(
                rel, run[0], "design",
                f"{name}: {len(run)} stacked text blocks competing in sequence (lines {', '.join(map(str, run[:4]))})",
                f"keep 1 title + essential context {tip_hint}",
            ))
    for sig, locs in stats["inline_chains"].items():
        if len(locs) >= 3 and any(look(n) for n in locs):
            out.append(core.Finding(
                rel, locs[1], "design",
                f"{name}: repeated inline style pattern ({'.'.join(sig)}) {len(locs)}x on screen",
                "extract to a shared theme-backed helper or simplify repeated cards",
            ))
    if include_consistency:
        radii = stats["radii"]
        if len(radii) > 3 and any(look(ln) for ln in radii.values()):
            first_ln = min(radii.values())
            sample = ", ".join(sorted(radii)[:6])
            out.append(core.Finding(
                rel, first_ln, "design",
                f"{name}: inconsistent corner radii ({len(radii)} distinct non-pill values: {sample})",
                "standardize on 1-2 project radius tokens (control radius and card radius)",
            ))
        font_sizes = stats["font_sizes"]
        if len(font_sizes) > 5 and any(look(ln) for ln in font_sizes.values()):
            first_ln = min(font_sizes.values())
            sample = ", ".join(sorted(font_sizes)[:7])
            out.append(core.Finding(
                rel, first_ln, "design",
                f"{name}: inconsistent typography scale ({len(font_sizes)} distinct font sizes: {sample})",
                "limit a single view to 3-4 typography scale steps from the theme",
            ))
        by_depth = defaultdict(dict)
        for depth, pad, ln in stats["card_paddings"]:
            by_depth[depth].setdefault(ln, pad)
        for card_map in by_depth.values():
            pads = sorted(set(card_map.values()))
            if len(card_map) >= 2 and len(pads) >= 2 and pads[-1] - pads[0] >= 8.0 and any(look(ln) for ln in card_map):
                first_ln = min(card_map)
                pad_desc = ", ".join(f"{p:g}px" for p in pads[:4])
                out.append(core.Finding(
                    rel, first_ln, "design",
                    f"{name}: inconsistent padding across sibling cards ({pad_desc})",
                    "use one consistent padding token for sibling cards at the same hierarchy level",
                ))
                break


def _file_symbols(source):
    impls = set(re.findall(r"\bimpl(?:<[^>{]+>)?\s+([A-Z]\w*)", source))
    imports = set(re.findall(r"(?:\b(?:use|mod)\s+[\w:]+::(\w+)|\bmod\s+(\w+)\s*;|import\s+\{([^}]+)\}|import\s+(\w+)\s+from)", source))
    names = set()
    for grp in imports:
        for part in grp:
            for item in part.split(","):
                tok = item.strip().split(" as ")[0].strip()
                if tok:
                    names.add(tok)
    return impls, names


def _peer_record(ppath, file_cache):
    """Reads and caches UI metadata for peer discovery without re-reading across siblings."""
    key = str(ppath.resolve())
    if key in file_cache:
        return file_cache[key]
    if not ppath.is_file() or ppath.suffix.lower() not in CODE_EXT:
        file_cache[key] = None
        return None
    rel = core.relative(ppath)
    if _own_debloat_case(ppath, rel) or core.C.skip_dirs & set(Path(rel).parts) or core.is_test(rel) or is_theme_file(rel):
        file_cache[key] = None
        return None
    if ppath.stat().st_size > core.MAX_BYTES:
        file_cache[key] = None
        return None
    psource = ppath.read_text(encoding="utf-8", errors="replace")
    psuf = ppath.suffix.lower()
    if not _is_target_file(rel, psuf, psource):
        file_cache[key] = None
        return None
    praw = psource.splitlines()
    pnorm = normalize_jsx_tags(praw) if psuf in WEB_EXT else praw
    plines = copy_rules.jsx_lines(pnorm) if psuf in JSX_LIKE else pnorm
    ptest = core.rust_test_start(praw) if psuf == ".rs" else None
    pskip = core.ignored_lines(praw)
    fn_names = {name for _, name, _ in _function_spans(plines, psuf, ptest, fallback_name=ppath.stem)}
    rec = (rel, ppath, plines, psource, psuf, ptest, pskip, fn_names)
    file_cache[key] = rec
    return rec


def _screen_peer_paths(path, file_cache):
    """UI sibling files belonging to the same screen module folder (up/down 1 UI subdir level)."""
    parent = path.parent
    dirs = [parent]
    if parent.name.lower() in SCREEN_SUBDIRS and parent.parent.is_dir():
        dirs.append(parent.parent)
        dirs += [d for d in sorted(parent.parent.iterdir()) if d.is_dir() and d.name.lower() in SCREEN_SUBDIRS]
    elif parent.name.lower() not in GENERIC_UI_DIRS:
        dirs += [d for d in sorted(parent.iterdir()) if d.is_dir() and d.name.lower() in SCREEN_SUBDIRS]
    seen, out = {path.resolve()}, []
    for folder in dict.fromkeys(dirs):
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.iterdir()):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rec = _peer_record(candidate, file_cache)
            if rec is not None:
                out.append(rec)
            if len(out) >= 20:
                return out
    return out


def _resolve_callee(cur_rel, cur_meta, qual, target, metas_by_rel):
    """Resolves a callee within current file or linked screen-module peers."""
    if target in cur_meta["by_name"]:
        return (cur_rel, target)
    if qual:
        for rel, meta in metas_by_rel.items():
            if rel != cur_rel and (Path(rel).stem == qual or Path(rel).parent.name == qual) and target in meta["by_name"]:
                return (rel, target)
    candidates = [(rel, meta) for rel, meta in metas_by_rel.items() if rel != cur_rel and target in meta["by_name"]]
    if not candidates:
        return None
    shared_impl = [(rel, m) for rel, m in candidates if cur_meta["impls"] & m["impls"]]
    if len(shared_impl) == 1:
        return (shared_impl[0][0], target)
    imported = [(rel, m) for rel, m in candidates if target in cur_meta["imports"] or Path(rel).stem in cur_meta["imports"]]
    if len(imported) == 1:
        return (imported[0][0], target)
    if len(candidates) == 1 and Path(cur_rel).parent.name.lower() not in GENERIC_UI_DIRS:
        return (candidates[0][0], target)
    return None


def _analyze_unit(rel, path, lines, source, suffix, test_from, look, skip, peer_metas=None, primary_helpers=(), non_primary_helpers=()):
    """Parses spans and analyses every function in one UI file."""
    spans = _function_spans(lines, suffix, test_from, fallback_name=path.stem)
    local_names = {name for _, name, _ in spans}
    known_callees = set(local_names)
    if peer_metas:
        for meta in peer_metas.values():
            known_callees |= set(meta["fn_names"])
    imported_icons = _extract_imported_icons(source) if suffix in WEB_EXT else set()
    impls, imports = _file_symbols(source)
    is_modal_file = bool(MODAL_CALLEE_RE.search(rel)) or any(MODAL_CALLEE_RE.search(imp) for imp in impls)
    by_name = {}
    for start, name, end in spans:
        by_name[name] = _analyze_fn(
            lines, suffix, start, name, end, look, skip, known_callees,
            primary_helpers=primary_helpers, non_primary_helpers=non_primary_helpers,
            imported_icons=imported_icons, is_modal_file=is_modal_file,
        )
    return {"rel": rel, "path": path, "by_name": by_name, "fn_names": local_names, "impls": impls, "imports": imports}


def _audit_file_design(rel, path, lines, source, suffix, test_from, look, skip, tokens, out, cache=None, include_consistency=True):
    cfg = core.C.design
    uses_tooltips = tokens.get("uses_tooltips", False)
    primary_helpers = tokens.get("primary_helpers", ())
    non_primary_helpers = tokens.get("non_primary_helpers", ())
    store = {} if cache is None else cache
    metas = store.setdefault("units", {})
    file_cache = store.setdefault("files", {})
    peers = _screen_peer_paths(path, file_cache)
    all_fn_names = {name for _, name, _ in _function_spans(lines, suffix, test_from, fallback_name=path.stem)}
    for _, _, _, _, _, _, _, p_names in peers:
        all_fn_names |= p_names

    peer_metas = {}
    for prel, ppath, plines, psource, psuf, ptest, pskip, _ in peers:
        pkey = str(ppath.resolve())
        unit = metas.get(pkey)
        if unit is None or not (all_fn_names <= unit["known_callees"]):
            unit = _analyze_unit(
                prel, ppath, plines, psource, psuf, ptest, lambda n: False, pskip,
                peer_metas={"_": {"fn_names": all_fn_names}},
                primary_helpers=primary_helpers, non_primary_helpers=non_primary_helpers,
            )
            unit["known_callees"] = set(all_fn_names)
            metas[pkey] = unit
        peer_metas[prel] = unit

    cur_meta = _analyze_unit(
        rel, path, lines, source, suffix, test_from, look, skip,
        peer_metas={"_": {"fn_names": all_fn_names}},
        primary_helpers=primary_helpers, non_primary_helpers=non_primary_helpers,
    )
    is_modal_file = (
        bool(MODAL_CALLEE_RE.search(rel))
        or any(MODAL_CALLEE_RE.search(imp) for imp in cur_meta["impls"])
    )
    metas_by_rel = {rel: cur_meta, **peer_metas}
    local_modal_card_fns = set()
    for stats in cur_meta["by_name"].values():
        before_len = len(out)
        _emit_fn_findings(
            rel, stats, uses_tooltips, look, out,
            is_modal_file=is_modal_file, include_consistency=include_consistency,
        )
        if any("inner boxed card inside a modal" in f.problem for f in out[before_len:]):
            local_modal_card_fns.add(stats["name"])
    # Include lean nested-cards, status-dot, and decorative-badge rules (deduped by key) so design_audit is the single [design] owner.
    seen_keys = {f.key() for f in out}
    lean_extra = []
    if suffix in JSX_LIKE:
        _lean_nested_cards(rel, lines, look, skip, lean_extra)
    _lean_slop_badges(rel, lines, suffix, look, skip, test_from, lean_extra)
    _lean_gpui_status_dots(rel, lines, suffix, look, skip, test_from, lean_extra)
    for f in lean_extra:
        if f.key() not in seen_keys:
            seen_keys.add(f.key())
            out.append(f)

    called_anywhere = set()
    for m_rel, m_data in metas_by_rel.items():
        for s in m_data["by_name"].values():
            for qual, target, *_ in s["calls"]:
                resolved = _resolve_callee(m_rel, m_data, qual, target, metas_by_rel)
                if resolved:
                    called_anywhere.add(resolved)

    count_keys = (
        "cards", "inner_cards", "dividers", "badges", "icons", "info_hints",
        "primary_accents", "headlines", "filter_triggers", "filter_chip_strips",
    )
    cards_idx = count_keys.index("cards")
    inner_card_idx = count_keys.index("inner_cards")
    hint_idx = count_keys.index("info_hints")
    primary_idx = count_keys.index("primary_accents")
    headline_idx = count_keys.index("headlines")
    trigger_idx = count_keys.index("filter_triggers")
    strip_idx = count_keys.index("filter_chip_strips")
    memo = {}

    def compose(node_key, stack_path=()):
        if not stack_path and node_key in memo:
            return memo[node_key]
        node_rel, fn_name = node_key
        m_data = metas_by_rel.get(node_rel)
        s = m_data["by_name"].get(fn_name) if m_data else None
        zeros = [0] * len(count_keys)
        falses = [False] * len(count_keys)
        if not s or node_key in stack_path or len(stack_path) >= 4:
            return 0, [fn_name], zeros, zeros, falses, False, False, False
        max_d = s["max_depth"]
        best_chain = [fn_name]
        entries = list(s["local_entries"])
        best_depth_touched = s["touched_depth"]
        local_deep = s["max_depth"] > cfg["max_container_depth"]
        child_deep = False
        child_modal_card = False
        for qual, target, depth_at_call, call_touched, bp, none_hint in s["calls"]:
            callee_key = _resolve_callee(node_rel, m_data, qual, target, metas_by_rel)
            if not callee_key:
                continue
            cd, cchain, ctotals, ct_max, chas_t, cdepth_touched, _, c_modal_card = compose(callee_key, stack_path + (node_key,))
            if c_modal_card or (callee_key[0] == rel and callee_key[1] in local_modal_card_fns):
                child_modal_card = True
            c_stats = metas_by_rel[callee_key[0]]["by_name"].get(callee_key[1], {})
            if c_stats.get("max_depth", 0) > cfg["max_container_depth"]:
                child_deep = True
            cand = depth_at_call + cd
            chain_touched = s["touched_depth"] or cdepth_touched or (call_touched and cd > 0)
            if cd > 0 and (cand > max_d or (cand == max_d and not best_depth_touched and chain_touched)):
                label = callee_key[1] if callee_key[0] == node_rel else f"{Path(callee_key[0]).stem}::{callee_key[1]}"
                max_d, best_chain = cand, [fn_name] + ([label] + cchain[1:] if cchain else [])
                best_depth_touched = chain_touched
            adj = list(ctotals)
            t_adj = list(ct_max)
            h_adj = list(chas_t)
            if none_hint:
                adj[hint_idx] = 0
                t_adj[hint_idx] = 0
                h_adj[hint_idx] = False
            if MODAL_CALLEE_RE.search(callee_key[1]):
                for idx in (primary_idx, headline_idx, inner_card_idx, trigger_idx, strip_idx):
                    adj[idx] = 0
                    t_adj[idx] = 0
                    h_adj[idx] = False
            if call_touched:
                for idx in range(len(count_keys)):
                    if adj[idx] > 0:
                        t_adj[idx] = adj[idx]
                        h_adj[idx] = True
            entries.append((bp, adj, t_adj, h_adj))
        seq_totals, touched_totals, has_touched = _aggregate_branches_touched(entries, len(count_keys))
        res = (
            max_d, best_chain, seq_totals, touched_totals, has_touched, best_depth_touched,
            (best_depth_touched and not local_deep and not child_deep), child_modal_card,
        )
        if not stack_path:
            memo[node_key] = res
        return res

    composed_specs = [
        ("cards", "max_cards", 0, "boxed cards/panels across composed view",
         "group sections with whitespace and typography instead of wrapping every block in a card"),
        ("info_hints", "max_info_hints", 0, "info_hint badges across composed view",
         "make labels self-explanatory and cut redundant help icons"),
        ("primary_accents", "max_primary_accents", 1, "competing primary accents across composed view",
         "keep solid primary accent for the main CTA only; use neutral/surface fill for selected filters and toggles"),
        ("headlines", "max_headlines", 0, "competing HEADLINE headings across composed view",
         "keep one primary headline for the view/modal and step down inner section titles"),
        ("filter_triggers", "max_exposed_filters", 0, "exposed filter/sort dropdowns across composed view",
         "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set"),
    ]
    for root_name, root in cur_meta["by_name"].items():
        if not root["calls"]:
            continue
        is_entry = (rel, root_name) not in called_anywhere or re.match(
            r"^(render\w*|build\w*|view\w*|[A-Z]\w*)$", root_name
        )
        if not is_entry:
            continue
        max_d, best_chain, totals, touched_totals, has_touched, _, depth_clean, child_modal_card = compose((rel, root_name))
        if not any(has_touched) and not depth_clean:
            continue
        root_is_modal = is_modal_file or bool(MODAL_CALLEE_RE.search(root_name)) or root["has_modal_box"]
        if len(best_chain) >= 2 and depth_clean and max_d > cfg["max_container_depth"]:
            out.append(core.Finding(
                rel, root["start"], "design",
                f"{' -> '.join(best_chain)}: composed hierarchy of {max_d} nested styled boxes (limit {cfg['max_container_depth']})",
                "flatten helper wrappers or remove inner card borders",
            ))
        for key, limit_key, extra_allowance, desc, fix in composed_specs:
            pos = count_keys.index(key)
            extra = 0 if (key == "primary_accents" and root_is_modal) else extra_allowance
            limit = cfg[limit_key] + extra
            if touched_totals[pos] > limit and root["counts"][key] <= limit:
                out.append(core.Finding(
                    rel, root["start"], "design",
                    f"{root_name} (composed): {touched_totals[pos]} {desc} (limit {limit})",
                    fix,
                ))
        if (root_is_modal and root_name not in local_modal_card_fns and not child_modal_card
                and (has_touched[inner_card_idx] or has_touched[cards_idx])
                and (root["counts"]["cards"] >= 1 or root["has_modal_box"] or touched_totals[inner_card_idx] >= 1)
                and (touched_totals[inner_card_idx] >= 1 or touched_totals[cards_idx] >= 2)):
            out.append(core.Finding(
                rel, root["start"], "design",
                f"{root_name} (composed): inner boxed card inside a modal (nested surface)",
                "flatten the inner card using whitespace and typography; the modal itself is already a card",
            ))
        local_stacked = root["counts"]["filter_triggers"] >= 1 and root["counts"]["filter_chip_strips"] >= 1
        if (totals[trigger_idx] >= 1 and totals[strip_idx] >= 1 and not local_stacked
                and ((has_touched[trigger_idx] and touched_totals[trigger_idx] >= 1)
                     or (has_touched[strip_idx] and touched_totals[strip_idx] >= 1))):
            out.append(core.Finding(
                rel, root["start"], "design",
                f"{root_name} (composed): stacked filter controls across composed view ({totals[trigger_idx]} trigger(s) + exposed filter-chip strip)",
                "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set",
            ))


def _check_css_line(rel, number, raw, tokens, out):
    """Checks CSS/QSS rule declarations for literal colors, font-size, and border-radius."""
    for decl in raw.split(";"):
        if "var(--" in decl:
            continue
        cm = CSS_COLOR_RE.search(URL_HASH_RE.sub("", decl))
        if cm:
            val = cm.group(1).lower()
            is_clear = val in ("#0000", "#00000000") or re.fullmatch(
                r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0(?:\.0+)?\s*\)", val
            ) is not None
            if not is_clear:
                out.append(core.Finding(rel, number, "style", f'hardcoded CSS color literal "{cm.group(1)[:32]}"', CSS_FIX))
        if tokens["has_font_tokens"]:
            fm = CSS_FONT_PX_RE.search(decl)
            if fm and float(fm.group(1)) >= 10:
                out.append(core.Finding(rel, number, "style", f"hardcoded CSS font-size {fm.group(1)}px", CSS_FIX))
        if tokens["has_radius_tokens"]:
            rm = CSS_RADIUS_PX_RE.search(decl)
            if rm and 3. < float(rm.group(1)) < 999.:
                out.append(core.Finding(rel, number, "style", f"hardcoded CSS border-radius {rm.group(1)}px", CSS_FIX))


def _style_checks(rel, lines, suffix, look, skip, test_from, tokens, out, source=None):
    if is_theme_file(rel, source):
        return
    work_lines = normalize_jsx_tags(lines) if suffix in WEB_EXT else lines
    eff_look = spacing._absorbed_look(lines, work_lines, look) if suffix in WEB_EXT else look
    sfc_style_lines = spacing._sfc_style_lines(lines) if suffix in MARKUP_EXT else None
    limit = (test_from - 1) if (suffix == ".rs" and test_from) else len(work_lines)
    in_block_comment = False
    theme_scope_depth = 0
    for number in range(1, limit + 1):
        raw = work_lines[number - 1]
        if suffix in STYLE_EXT:
            stripped = raw.strip()
            if in_block_comment:
                if "*/" in stripped:
                    in_block_comment = False
                continue
            if stripped.startswith("/*"):
                if "*/" not in stripped[2:]:
                    in_block_comment = True
                continue
            if CSS_SCOPE_RE.match(stripped) and "{" in stripped:
                theme_scope_depth += stripped.count("{") - stripped.count("}")
                continue
            if theme_scope_depth > 0:
                theme_scope_depth = max(0, theme_scope_depth + stripped.count("{") - stripped.count("}"))
                continue
        if number in skip or not eff_look(number):
            continue
        if core.comment_of(raw, suffix) is not None or core.LOG_CONTEXT.search(raw):
            continue

        if suffix == ".rs":
            m = RS_HEX_COLOR_RE.search(raw)
            if m:
                val = m.group(2).strip().replace("_", "")
                is_clear = bool(re.fullmatch(r"0x(?:[0-9a-fA-F]{6}00|0{6,8})", val))
                if not is_clear:
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded color literal "{m.group(0)[:36]}"',
                        "use project Palette / theme color variable",
                    ))
            elif EGUI_COLOR_RE.search(raw):
                em = EGUI_COLOR_RE.search(raw)
                out.append(core.Finding(
                    rel, number, "style",
                    f'hardcoded color literal "{em.group(0)[:36]}"',
                    "use project Palette / theme color variable",
                ))
            elif RS_RGBA_LIT_RE.search(raw):
                out.append(core.Finding(
                    rel, number, "style",
                    "hardcoded Rgba struct literal without theme palette spread",
                    "derive from theme Palette (e.g. Rgba { a: ..., ..p.surface })",
                ))
            if tokens["has_font_tokens"]:
                fm = RS_FONT_SIZE_RE.search(raw) or EGUI_FONT_SIZE_RE.search(raw)
                if fm and float(fm.group(1)) >= 10:
                    out.append(core.Finding(
                        rel, number, "style",
                        f"hardcoded text_size px({fm.group(1)})",
                        "use project typography token (e.g. visual::BODY / FOOTNOTE / HEADLINE)",
                    ))
            if tokens["has_radius_tokens"]:
                rm = RS_RADIUS_RE.search(raw)
                if rm:
                    radius = float(rm.group(1))
                    nearby = "\n".join(work_lines[max(0, number - 3):min(limit, number + 2)])
                    circular = any(abs(float(sz) - 2. * radius) <= 1. for sz in RS_CIRCLE_SIZE_RE.findall(nearby))
                    if radius > 3. and not circular:
                        out.append(core.Finding(
                            rel, number, "style",
                            f"hardcoded corner radius px({rm.group(1)})",
                            "use project radius token (e.g. p.control_radius / p.card_radius)",
                        ))
        elif suffix in WEB_EXT:
            if sfc_style_lines is not None and sfc_style_lines[number - 1].strip():
                continue
            tw_c = TW_COLOR_RE.search(raw)
            if tw_c and "var(--" not in tw_c.group(0):
                out.append(core.Finding(
                    rel, number, "style",
                    f'hardcoded Tailwind arbitrary color "{tw_c.group(0)[:36]}"',
                    CSS_FIX,
                ))
            tw_raw = TW_RAW_COLOR_RE.search(raw) if tokens.get("has_tw_semantic_colors") else None
            if tw_raw:  # project defines semantic Tailwind colors; stock palette shade bypasses them
                out.append(core.Finding(
                    rel, number, "style",
                    f'stock Tailwind color "{tw_raw.group(0)}" in a themed project',
                    CSS_FIX,
                ))
            tw_f = TW_FONT_RE.search(raw) if tokens["has_font_tokens"] else None
            if tw_f and (tw_f.group(2) != "px" or float(tw_f.group(1)) >= 10):
                out.append(core.Finding(
                    rel, number, "style",
                    f'hardcoded Tailwind font-size "{tw_f.group(0)}"',
                    CSS_FIX,
                ))
            tw_r = TW_RADIUS_RE.search(raw) if tokens["has_radius_tokens"] else None
            if tw_r and (tw_r.group(2) != "px" or 3. < float(tw_r.group(1)) < 999.):
                out.append(core.Finding(
                    rel, number, "style",
                    f'hardcoded Tailwind radius "{tw_r.group(0)}"',
                    CSS_FIX,
                ))
            sm = JSX_INLINE_STYLE_RE.search(raw)
            if sm:
                body = sm.group(1)
                cm = CSS_COLOR_RE.search(URL_HASH_RE.sub("", body))
                if cm and "var(--" not in body:
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded inline CSS color "{cm.group(1)[:32]}"',
                        CSS_FIX,
                    ))
                if tokens["has_font_tokens"] and JSX_FONT_PX_RE.search(body) and "var(--" not in body:
                    out.append(core.Finding(
                        rel, number, "style",
                        "hardcoded inline fontSize in JSX style",
                        CSS_FIX,
                    ))
                if tokens["has_radius_tokens"] and JSX_RADIUS_PX_RE.search(body) and "var(--" not in body:
                    out.append(core.Finding(
                        rel, number, "style",
                        "hardcoded inline borderRadius in JSX style",
                        CSS_FIX,
                    ))
            elif not THEME_STYLE_HINT_RE.search(raw) and "var(--" not in raw:
                cm = CSS_COLOR_RE.search(URL_HASH_RE.sub("", raw))
                if cm and ("style" in raw or "color" in raw.lower() or "background" in raw.lower()):
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded color literal "{cm.group(1)[:32]}"',
                        CSS_FIX,
                    ))
        elif suffix in STYLE_EXT:
            if not CSS_VAR_DECL_RE.match(raw):
                _check_css_line(rel, number, raw, tokens, out)
        elif suffix == ".py":
            qm = QSS_INLINE_RE.search(raw)
            if qm:
                body = qm.group(1)
                cm = CSS_COLOR_RE.search(body)
                if cm:
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded inline QSS color "{cm.group(1)[:32]}"',
                        QSS_FIX,
                    ))
                if CSS_FONT_PX_RE.search(body) or CSS_RADIUS_PX_RE.search(body):
                    out.append(core.Finding(
                        rel, number, "style",
                        "hardcoded inline QSS size/radius",
                        QSS_FIX,
                    ))
    if sfc_style_lines is not None:
        sfc_comment = False
        sfc_theme_depth = 0
        for number, raw in enumerate(sfc_style_lines, 1):
            stripped = raw.strip()
            if not stripped:
                continue
            if sfc_comment:
                if "*/" in stripped:
                    sfc_comment = False
                continue
            if stripped.startswith("/*"):
                if "*/" not in stripped[2:]:
                    sfc_comment = True
                continue
            if CSS_SCOPE_RE.match(stripped) and "{" in stripped:
                sfc_theme_depth += stripped.count("{") - stripped.count("}")
                continue
            if sfc_theme_depth > 0:
                sfc_theme_depth = max(0, sfc_theme_depth + stripped.count("{") - stripped.count("}"))
                continue
            if number not in skip and look(number) and not CSS_VAR_DECL_RE.match(raw):
                _check_css_line(rel, number, raw, tokens, out)


_SPACING_SCALES = {}
FN_HEAD_RE = re.compile(
    r"\s*(?:pub(?:\([^)]*\))?\s+|export\s+(?:default\s+)?)?(?:async\s+)?(?:fn|def|function)\s+(\w+)"
    r"|\s*(?:export\s+)?(?:const|let)\s+([A-Z]\w*)(?:\s*:\s*[^=]+)?\s*=\s*"
    r"(?:(?:React\.)?(?:memo|forwardRef)\s*\(\s*)?(?:async\s+)?(?:function\b|\([^)]*\)(?:\s*:\s*[^=]+)?\s*=>|\w+\s*=>)"
)


def spacing_scale(root=None):
    """The project's spacing steps and named spacing constants, learned once per repository.
    Oversized files (bundles, generated stylesheets) are left out, as in the audit itself: their
    thousands of values would set the scale the hand-written screens are judged by."""
    key = str(Path(root or core.REPO).resolve())
    if key not in _SPACING_SCALES:
        _SPACING_SCALES[key] = spacing.learn_scale(
            (path.read_text(encoding="utf-8", errors="replace"), suffix)
            for _, path, suffix in _tracked_candidates(root)
            if path.is_file() and path.stat().st_size <= core.MAX_BYTES
        )
    return _SPACING_SCALES[key]


def _spacing_checks(rel, raw_lines, suffix, look, test_from, out, source=None):
    if is_theme_file(rel, source):
        return
    limit = (test_from - 1) if (suffix == ".rs" and test_from) else len(raw_lines)
    lines = raw_lines[:limit]
    # The enclosing function by its header line: `render_owner` per line costs ~60 ms a file.
    heads = [(number, m.group(1) or m.group(2)) for number, line in enumerate(lines, 1)
             for m in [FN_HEAD_RE.match(line)] if m and (m.group(1) or m.group(2))] if suffix in CODE_EXT else []
    starts = [number for number, _ in heads]

    def fn_of(line):
        at = bisect.bisect_right(starts, line) - 1
        return heads[at][1] if at >= 0 else Path(rel).stem

    steps, tokens = spacing_scale()
    out += spacing.check(rel, lines, suffix, fn_of, look, steps, tokens)


# ---------------------------------------------------------------- lean [design] single-owner entry point

_LEAN_BOX_TAG = re.compile(
    r"<(/?)(Card|Paper|Panel|div|section|article|aside)\b([^>]*?)(/?)>|"
    r"<(Card|Paper|Panel|div|section|article|aside)\b([^>]*)$"
)
_LEAN_LIST_ITEM = re.compile(r"\.(?:map|flatMap)\(|\bv-for=|\{#each\b")
_LEAN_CARD_CLASS = re.compile(r"\b(?:border(?!-(?:0|none|transparent)\b)|shadow(?:-\w+)?|card|ring)\b")
_LEAN_BADGE_OPEN = re.compile(
    r"<(?:Badge|Chip|Tag|Pill)\b|<(?:v|el|n|a|q|b)-(?:chip|tag|badge)\b|\b(?:badge|chip|tag|pill|status_pill)\s*\("
)
_LEAN_HYPE = re.compile(
    r"(?i)^\W*(?:new|novo|nova|beta|alpha|pro|ai|ia|hot|popular|recommended|recomendad[oa]|smart|"
    r"premium|trending|exclusive|exclusivo|live|ao vivo|ai[\s-]powered|powered by \w+)\W*$"
)
_LEAN_SLOP_LOOK = re.compile(
    r"bg-gradient|linear-gradient|gradient\(|animate-(?:pulse|ping)|uppercase[^\"']*tracking-|"
    r"tracking-\w+[^\"']*uppercase|^\s*[•●◉]|rounded-full\s+(?:h|w|size)-(?:1|1\.5|2)\b"
)
_LEAN_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_GPUI_DOT_SIZE = re.compile(r"\.(?:size|w)\(px\(([5-9](?:\.\d*)?)\)\)")
_GPUI_DOT_BG = re.compile(r"\.bg\(([^)]+)\)")
_DOT_NON_STATUS = re.compile(
    r"(?i)(?<![a-z])(?:store_dot|series|legend|plot|chart|bullet|item_row|markdown|timeline|milestone|step|avatar|thumb|badge_dot)(?![a-z])"
    r"|\.when\(\s*(?:checked|selected|active)\b"
)
_DOT_STATUS_OWNER = re.compile(r"(?i)(?:status|badge|pill|chip|monitor_bar|banner|state)")
_DOT_STATUS_COLOR = re.compile(r"(?i)\b(?:dot|ink|status\w*|success|danger|warning|accent|focus_ring)\b")


def _lean_owner(lines, number, fallback):
    owner = copy_rules.render_owner(lines, number)
    if owner[0] != 0:
        return owner
    for back in range(number, 0, -1):
        hm = FN_HEAD_RE.match(lines[back - 1])
        if hm and (hm.group(1) or hm.group(2)):
            return (back, hm.group(1) or hm.group(2))
    return (1, fallback)


def _lean_nested_cards(rel, lines, look, skip, out):
    """Multi-line-safe nested card detector (JSX/Vue/Svelte). Self-closing tags and border-none never stack."""
    limit = core.C.design.get("max_card_nesting", 2)
    stack, deepest, where, touched = [], 0, 0, False
    for number, line in enumerate(lines, 1):
        if number in skip or not line:
            continue
        for m in _LEAN_BOX_TAG.finditer(line):
            closing = m.group(1)
            name = m.group(2) or m.group(5)
            attrs = m.group(3) or m.group(6) or ""
            selfclose = m.group(4)
            if closing:
                if stack:
                    stack.pop()
                continue
            card = name in ("Card", "Paper", "Panel") or bool(_LEAN_CARD_CLASS.search(attrs))
            if card and any(_LEAN_LIST_ITEM.search(lines[i]) for i in range(max(0, number - 3), number)):
                card = False
            if selfclose:
                continue
            stack.append(card)
            depth = sum(stack)
            if card and depth > deepest:
                deepest, where = depth, number
            if card and depth > limit and look(number):
                touched = True
    if deepest > limit and touched:
        out.append(core.Finding(
            rel, where, "design",
            f"{deepest} cards nested inside each other (limit {limit})",
            "keep one box; separate the inner groups with spacing and a heading, not another border",
        ))


def _lean_slop_badges(rel, lines, suffix, look, skip, test_from, out):
    """Flags decorative/hype badges and Tailwind inline pill soup."""
    limit = (test_from - 1) if (suffix == ".rs" and test_from) else len(lines)
    lit_suffix = ".tsx" if suffix in MARKUP_EXT else suffix
    fallback = Path(rel).stem
    loud, styled, pills = defaultdict(list), defaultdict(list), defaultdict(list)
    for number in range(1, limit + 1):
        if number in skip:
            continue
        line = lines[number - 1]
        if not line.strip() or core.comment_of(line, suffix) is not None:
            continue
        is_badge = bool(_LEAN_BADGE_OPEN.search(line))
        is_pill = suffix in WEB_EXT and _is_tw_pill(line)
        if not is_badge and not is_pill:
            continue
        owner = _lean_owner(lines, number, fallback)
        if is_pill:
            pills[owner].append(number)
        near = lines[number - 1:min(limit, number + 2)]
        texts = [t for l in near for t in core.literals(l, lit_suffix)]
        if any(_LEAN_HYPE.match(t) or _LEAN_EMOJI_RE.search(t) for t in texts):
            loud[owner].append(number)
        elif any(_LEAN_SLOP_LOOK.search(l) for l in near) or any(_LEAN_SLOP_LOOK.search(t) for t in texts):
            styled[owner].append(number)
    for owner in set(loud) | set(styled):
        found = sorted(set(loud[owner] + styled[owner]))
        if (loud[owner] or len(styled[owner]) >= 2) and any(look(n) for n in found):
            out.append(core.Finding(
                rel, found[0], "design",
                f"{owner[1]}: {len(found)} decorative badges (lines {', '.join(map(str, found[:5]))})",
                "keep a badge only for a state the user acts on; no dot, gradient, pulse or hype word",
            ))
    max_badges = core.C.design.get("max_badges", 6)
    for owner, found in pills.items():
        if len(found) > max_badges and owner not in loud and owner not in styled and any(look(n) for n in found):
            out.append(core.Finding(
                rel, found[0], "design",
                f"{owner[1]}: {len(found)} badges/chips cluttering one view (limit {max_badges})",
                "keep only status chips that drive user action",
            ))


def _lean_gpui_status_dots(rel, lines, suffix, look, skip, test_from, out):
    """Detects decorative GPUI status dots beside status text."""
    if suffix != ".rs":
        return
    limit = (test_from - 1) if test_from else len(lines)
    fallback = Path(rel).stem
    for number in range(1, limit + 1):
        if number in skip or not look(number):
            continue
        line = lines[number - 1]
        if core.comment_of(line, suffix) is not None:
            continue
        line_s = line.strip()
        size_match = _GPUI_DOT_SIZE.search(line_s)
        if not size_match:
            continue
        head = " ".join(l.strip() for l in lines[max(0, number - 6):number - 1])
        tail = " ".join(l.strip() for l in lines[number - 1:min(limit, number + 4)])
        cut_head = max(head.rfind(".child("), head.rfind(";"))
        prefix = head[cut_head + 1:] if cut_head >= 0 else head
        if "div()" in prefix:
            prefix = prefix[prefix.rfind("div()"):]
        after_size = tail[size_match.start():]
        cut = re.search(r"\.child\(|;", after_size)
        element = (prefix + " " + tail[:size_match.start()] + (after_size[:cut.start()] if cut else after_size)).strip()
        if "div()" in element:
            element = element[element.rfind("div()"):]
        if ".rounded_full()" not in element or ".border_2()" in element:
            continue
        bg_match = _GPUI_DOT_BG.search(element)
        if not bg_match:
            continue
        if ".w(px(" in element and ".h(px(" not in element:
            continue
        owner = _lean_owner(lines, number, fallback)
        context = "\n".join(lines[max(0, number - 4):min(limit, number + 6)])
        if _DOT_NON_STATUS.search(owner[1]) or _DOT_NON_STATUS.search(context):
            continue
        bg_arg = bg_match.group(1)
        if not (
            _DOT_STATUS_OWNER.search(owner[1])
            or _DOT_STATUS_COLOR.search(bg_arg)
            or ".text_color(" in context
        ):
            continue
        out.append(core.Finding(
            rel, number, "design",
            f"{owner[1]}: decorative status dot",
            "colour only the status word with the semantic token; drop the dot",
        ))


# --- composed-class, CSS rule-block, GPUI chain, StatCard and per-row badge pass ---------------
CLASS_CALL_START_RE = re.compile(r"\b(?:cn|clsx|cva|cx|tv|twMerge|classNames|classnames)\s*\(")
CLASS_CONST_RE = re.compile(r"""^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(["'`])([^"'`]*)\1""")
STR_LIT_RE = re.compile(r"""(["'`])((?:(?!\1)[^\\]|\\.)*)\1""")
SFC_STYLE_OPEN_RE = re.compile(r"<style\b[^>]*>", re.I)
SFC_STYLE_CLOSE_RE = re.compile(r"</style>", re.I)
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
CSS_GRADIENT_RE = re.compile(r"\b(?:linear|radial|conic)-gradient\(")
CSS_BACKDROP_BLUR_RE = re.compile(r"(?:^|[\s;])(?:-webkit-)?backdrop-filter\s*:\s*[^;]*\bblur\(")
CSS_BOX_SHADOW_RE = re.compile(r"\bbox-shadow\s*:\s*([^;]+)")
CSS_LENGTH_RE = re.compile(r"(?<![\w-])(-?\d*\.?\d+)(px|rem|em)?(?![\w%])")
CSS_CLIP_TEXT_RE = re.compile(r"(?:^|[\s;])(?:-webkit-)?background-clip\s*:\s*text\b")
CSS_TRANSPARENT_TEXT_RE = re.compile(r"(?:-webkit-text-fill-color|(?:^|[\s;])color)\s*:\s*transparent\b")
CSS_BG_ALPHA_RE = re.compile(r"(?:^|[\s;])background(?:-color)?\s*:\s*(?:rgba|hsla)\([^)]*,\s*(0?\.\d+)\s*\)")
CSS_PULSE_ANIM_RE = re.compile(r"(?i)\banimation(?:-name)?\s*:\s*[^;]*\b(?:pulse|ping|blink|glow)\w*")
CSS_ROUND_RE = re.compile(r"\bborder-radius\s*:\s*(?:50%|100%|9{3,}px|var\(--radius-(?:full|round)\))")
CSS_DOT_SEL_RE = re.compile(r"(?i)(?:^|[.\-_])(?:dot|indicator|pulse|blip)(?:$|[\-_])")
CSS_BG_DECL_RE = re.compile(r"(?:^|[\s;])background(?:-color)?\s*:")
CSS_BORDER_DECL_RE = re.compile(r"(?:^|[\s;])border\s*:\s*(?!0\b|none\b)")
CSS_RADIUS_DECL_RE = re.compile(r"(?:^|[\s;])border-radius\s*:")
GPUI_GRADIENT_RE = re.compile(r"\b(?:linear_gradient|radial_gradient)\s*\(")
GPUI_HEAVY_SHADOW_RE = re.compile(r"\.shadow_(?:xl|2xl)\s*\(\s*\)")
STAT_CARD_RE = re.compile(r"<((?:[A-Z]\w*)?(?:Stat|Metric|Kpi|KPI)\w*)\b[^>]*\bvalue=")
SLOP_SURFACE_FIX = "use plain theme surface and border tokens instead of decorative gradients, blur, or heavy glow"
SLOP_GRAD_TEXT_FIX = "use a plain theme text token and show hierarchy via weight or size"
SLOP_DOT_FIX = "colour only the status word with a semantic token; keep pulse animations for real loading states"


def _class_bag_spans(lines):
    """Yields (start_line, end_line, bag) for class strings composed across a statement:
    multi-line cn()/clsx()/cva()/twMerge() calls and `const x = "..."` class constants
    (in a function or at module scope)."""
    number, limit = 1, len(lines)
    while number <= limit:
        line = lines[number - 1]
        cm = CLASS_CALL_START_RE.search(line)
        if cm:
            depth, end, parts = 0, number, []
            for k in range(number, min(limit, number + 40) + 1):
                seg = lines[k - 1][cm.start():] if k == number else lines[k - 1]
                parts.append(seg)
                depth += seg.count("(") - seg.count(")")
                end = k
                if depth <= 0:
                    break
            text = "\n".join(parts)
            bag = " ".join(m.group(2) for m in STR_LIT_RE.finditer(text))
            yield number, end, bag
            number = end + 1
            continue
        km = CLASS_CONST_RE.match(line)
        if km:
            yield number, number, km.group(2)
        number += 1


def _css_rule_blocks(raw_lines, suffix):
    """Yields (line, selector, body) for plain CSS rule blocks of a stylesheet or of SFC <style> blocks."""
    chunks = []
    if suffix in STYLE_EXT:
        chunks.append((1, "\n".join(raw_lines)))
    elif suffix in MARKUP_EXT:
        start = None
        for number, line in enumerate(raw_lines, 1):
            if start is None:
                om = SFC_STYLE_OPEN_RE.search(line)
                if om:
                    start = number
                    buf = [line[om.end():]]
                    if SFC_STYLE_CLOSE_RE.search(buf[0]):
                        chunks.append((number, SFC_STYLE_CLOSE_RE.split(buf[0])[0]))
                        start = None
            elif SFC_STYLE_CLOSE_RE.search(line):
                buf.append(SFC_STYLE_CLOSE_RE.split(line)[0])
                chunks.append((start, "\n".join(buf)))
                start = None
            else:
                buf.append(line)
    for base, text in chunks:
        text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
        for m in CSS_RULE_RE.finditer(text):
            selector = m.group(1).strip()
            if not selector or selector.startswith("@"):
                continue
            sel_off = m.start(1) + (len(m.group(1)) - len(m.group(1).lstrip()))
            yield base + text.count("\n", 0, sel_off), selector, m.group(2)


def _css_max_shadow_blur(body):
    best = 0.0
    for sm in CSS_BOX_SHADOW_RE.finditer(body):
        value = re.sub(r"\([^()]*\)", " ", sm.group(1))
        for layer in value.split(","):
            if "inset" in layer:
                continue
            nums = CSS_LENGTH_RE.findall(layer)
            if len(nums) >= 3:
                size, unit = float(nums[2][0]), nums[2][1]
                best = max(best, size * 16 if unit in ("rem", "em") else size)
    return best


def _css_bem_block(selector):
    first = re.search(r"\.([A-Za-z][\w-]*)", selector)
    if not first:
        return None
    return re.split(r"__|--", first.group(1))[0]


def _composed_slop_pass(rel, raw_lines, suffix, look, skip, test_from, is_modal_file, out, full=False):
    """Slop cues that the per-line detectors cannot see: classes composed across a cn()/cva()
    statement or hoisted into a module-scope constant, CSS/SFC <style> rule blocks, GPUI element
    chains, and (full audits only) <StatCard value=...> strips and badge soup inside one list row."""
    fallback = Path(rel).stem.replace(".module", "")
    seen = {(f.line, f.problem.split(": ", 1)[-1].split(" (")[0]) for f in out if f.path == rel}
    seen_probs = {f.problem for f in out if f.path == rel}

    def emit(number, owner, msg, fix):
        key = (number, msg.split(" (")[0])
        prob = f"{owner}: {msg}"
        if key in seen or prob in seen_probs or not look(number):
            return
        seen.add(key)
        seen_probs.add(prob)
        out.append(core.Finding(rel, number, "design", prob, fix))

    if suffix in WEB_EXT:
        norm = normalize_jsx_tags(raw_lines)
        for start, end, bag in _class_bag_spans(norm):
            if start in skip or not bag.strip():
                continue
            owner = _lean_owner(norm, start, fallback)[1]
            fn_modal = is_modal_file or bool(MODAL_CALLEE_RE.search(owner))
            msg = _slop_surface_cue(bag, fn_modal)
            if msg:
                emit(start, owner, msg, SLOP_SURFACE_FIX)
            if SLOP_GRAD_CLIP_RE.search(bag) and SLOP_GRAD_TRANS_RE.search(bag):
                emit(start, owner, "decorative gradient text (bg-clip-text)", SLOP_GRAD_TEXT_FIX)
            ctx = "\n".join(norm[max(0, start - 3):min(len(norm), end + 2)])
            if _is_tw_animated_dot(bag, ctx):
                emit(start, owner, "decorative pulsing status dot", SLOP_DOT_FIX)

    if suffix in STYLE_EXT or suffix in MARKUP_EXT:
        owner = fallback
        groups = defaultdict(list)
        surfaces = defaultdict(list)
        for number, selector, body in _css_rule_blocks(raw_lines, suffix):
            if number in skip:
                continue
            grad = bool(CSS_GRADIENT_RE.search(body))
            blur = bool(CSS_BACKDROP_BLUR_RE.search(body))
            glow = _css_max_shadow_blur(body) >= 24
            alpha = CSS_BG_ALPHA_RE.search(body)
            frost = bool(alpha) and float(alpha.group(1)) <= 0.3
            modal = is_modal_file or bool(SLOP_MODAL_CONTEXT_RE.search(selector)) or bool(
                re.search(r"(?i)modal|dialog|overlay|backdrop|sheet|drawer|popover", selector))
            if not modal and ((grad and (blur or glow)) or (blur and (frost or glow))):
                emit(number, owner, f"decorative gradient/glassmorphism surface (CSS {selector})", SLOP_SURFACE_FIX)
            if CSS_CLIP_TEXT_RE.search(body) and CSS_TRANSPARENT_TEXT_RE.search(body):
                emit(number, owner, f"decorative gradient text (CSS {selector})", SLOP_GRAD_TEXT_FIX)
            block = _css_bem_block(selector)
            if block:
                groups[block].append((number, selector, body))
                if CSS_BG_DECL_RE.search(body) and CSS_BORDER_DECL_RE.search(body) and CSS_RADIUS_DECL_RE.search(body):
                    surfaces[block].append(number)
        for block, rules in groups.items():
            sels = " ".join(r[1] for r in rules)
            body = " ".join(r[2] for r in rules)
            anim = next((r for r in rules if CSS_PULSE_ANIM_RE.search(r[2])), None)
            if anim and (CSS_ROUND_RE.search(body) or CSS_DOT_SEL_RE.search(block)) and not TW_DOT_LOADING_RE.search(sels):
                emit(anim[0], owner, f"decorative pulsing status dot (CSS .{block})", SLOP_DOT_FIX)
        for block, found in surfaces.items():
            if len(found) >= 3:
                emit(found[0], owner,
                     f"{len(found)} boxed surfaces nested in one component (CSS .{block}: background + border + radius each)",
                     "keep one box; separate the inner groups with spacing and a heading, not another border")

    if suffix == ".rs":
        limit = (test_from - 1) if test_from else len(raw_lines)
        for number in range(1, limit + 1):
            line = raw_lines[number - 1]
            if number in skip or not GPUI_GRADIENT_RE.search(line) or core.comment_of(line, suffix) is not None:
                continue
            indent = len(line) - len(line.lstrip())
            chain = [line]
            for step in (-1, 1):
                k = number + step
                while 1 <= k <= limit:
                    other = raw_lines[k - 1]
                    o_indent = len(other) - len(other.lstrip())
                    if other.strip() and o_indent < indent:
                        break
                    if other.strip() and o_indent == indent and not other.lstrip().startswith((".", ")")):
                        break
                    if o_indent == indent:
                        chain.append(other)
                    k += step
            if any(GPUI_HEAVY_SHADOW_RE.search(c) for c in chain):
                owner = _lean_owner(raw_lines, number, fallback)[1]
                emit(number, owner, "decorative gradient/glassmorphism surface (gradient fill + heavy shadow)", SLOP_SURFACE_FIX)

    if not full or suffix not in WEB_EXT:
        return
    norm = normalize_jsx_tags(raw_lines)
    lines = core.jsx_lines(norm, normalized=True) if suffix in JSX_LIKE else norm
    stat_cards = defaultdict(list)
    covered = set()
    for number, line in enumerate(lines, 1):
        if number in skip:
            continue
        if STAT_CARD_RE.search(line):
            owner = _lean_owner(lines, number, fallback)
            in_loop = any(LOOP_START_RE.search(lines[k - 1]) for k in range(max(1, number - 3), number))
            stat_cards[owner].append((number, in_loop))
        if number in covered or not LOOP_START_RE.search(line):
            continue
        indent = len(line) - len(line.lstrip())
        badges = []
        for k in range(number, min(len(lines), number + 60) + 1):
            row = lines[k - 1]
            if k > number and row.strip() and len(row) - len(row.lstrip()) <= indent:
                break
            covered.add(k)
            badges += [k] * len(_LEAN_BADGE_OPEN.findall(row))
        if len(badges) >= 3:
            owner = _lean_owner(lines, number, fallback)[1]
            uniq = sorted(set(badges))
            emit(badges[0], owner,
                 f"{len(badges)} badges per list row (lines {', '.join(map(str, uniq[:5]))})",
                 "keep one status badge per row; show the other fields as plain muted text or in the detail view")
    for owner, cards in stat_cards.items():
        if len(cards) >= 3 or any(loop for _, loop in cards):
            if any(f.problem.startswith(f"{owner[1]}: KPI stat strip") for f in out if f.path == rel):
                continue
            desc = "a mapped row of metric cards" if any(loop for _, loop in cards) else f"{len(cards)} metric cards"
            emit(cards[0][0], owner[1], f"KPI stat strip ({desc} <{STAT_CARD_RE.search(lines[cards[0][0] - 1]).group(1)} value=...>)",
                 "keep a metric strip only on a dashboard the user asked for; otherwise show the one number the workflow needs inline, or drop the strip")


def scan_lean_design(path, rel, text, repo, only=None):
    """Single canonical owner of lean [design] layout/chrome/AI-slop rules for hunt.py / stop_gate.py:
    nested cards/styled boxes, decorative/slop badges & pills, GPUI/Tailwind status dots, and
    composite AI-slop cues. Excludes heavier per-screen consistency checks (radii, font scale,
    sibling card padding) which stay scoped to full /tautology-debloat-design audits."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in CODE_EXT or is_theme_file(rel, text) or core.is_test(rel):
        return []
    if not _is_target_file(rel, suffix, text):
        return []
    raw_lines = text.splitlines()
    skip = core.ignored_lines(raw_lines)
    base_look = (lambda n: n not in skip) if only is None else (lambda n: n in only and n not in skip)
    test_from = core.rust_test_start(raw_lines) if suffix == ".rs" else None
    norm_lines = normalize_jsx_tags(raw_lines) if suffix in WEB_EXT else raw_lines
    eff_look = spacing._absorbed_look(raw_lines, norm_lines, base_look) if suffix in WEB_EXT else base_look
    lines = core.jsx_lines(norm_lines, normalized=True) if suffix in JSX_LIKE else norm_lines
    fallback = path.stem
    is_modal_file = bool(MODAL_CALLEE_RE.search(rel))

    out = []
    if suffix in JSX_LIKE:
        _lean_nested_cards(rel, norm_lines, eff_look, skip, out)
    _lean_slop_badges(rel, lines, suffix, eff_look, skip, test_from, out)
    _lean_gpui_status_dots(rel, raw_lines, suffix, base_look, skip, test_from, out)

    if suffix in WEB_EXT:
        seen_slop = set()
        limit = len(norm_lines)
        for number in range(1, limit + 1):
            if number in skip or not eff_look(number):
                continue
            line = norm_lines[number - 1]
            if not line.strip() or core.comment_of(line, suffix) is not None:
                continue
            owner = _lean_owner(norm_lines, number, fallback)
            fn_modal = is_modal_file or bool(MODAL_CALLEE_RE.search(owner[1]))
            slop_msg = _slop_surface_cue(line, fn_modal)
            if slop_msg:
                prob = f"{owner[1]}: {slop_msg}"
                if prob not in seen_slop:
                    seen_slop.add(prob)
                    out.append(core.Finding(
                        rel, number, "design", prob,
                        "use plain theme surface and border tokens instead of decorative gradients, blur, or heavy glow",
                    ))
            if SLOP_GRAD_CLIP_RE.search(line) and SLOP_GRAD_TRANS_RE.search(line):
                prob = f"{owner[1]}: decorative gradient text (bg-clip-text)"
                if prob not in seen_slop:
                    seen_slop.add(prob)
                    out.append(core.Finding(
                        rel, number, "design", prob,
                        "use a plain theme text token and show hierarchy via weight or size",
                    ))
            dot_ctx = "\n".join(norm_lines[max(0, number - 3):min(limit, number + 2)])
            if _is_tw_animated_dot(line, dot_ctx):
                prob = f"{owner[1]}: decorative pulsing status dot"
                if prob not in seen_slop:
                    seen_slop.add(prob)
                    out.append(core.Finding(
                        rel, number, "design", prob,
                        "colour only the status word with a semantic token; keep pulse animations for real loading states",
                    ))
    _composed_slop_pass(rel, raw_lines, suffix, base_look, skip, test_from, is_modal_file, out)
    return out


def scan_file(path, only=None, tokens=None, checks=None, cache=None, include_consistency=True):
    return [
        f for f in raw_scan_file(
            path, only=only, tokens=tokens, checks=checks, cache=cache,
            include_consistency=include_consistency,
        )
        if not core.accepted(f)
    ]


def raw_scan_file(path, only=None, tokens=None, checks=None, cache=None, include_consistency=True):
    rel = core.relative(path)
    suffix = path.suffix.lower()
    if suffix not in SCAN_EXT or core.C.skip_dirs & set(Path(rel).parts) or not path.is_file():
        return []
    if _own_debloat_case(path, rel) or path.stat().st_size > core.MAX_BYTES:
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    if core.is_test(rel) or not _is_target_file(rel, suffix, source):
        return []

    wanted = checks or CHECKS
    tokens = tokens or discover_project_tokens()
    raw_lines = source.splitlines()
    norm_lines = normalize_jsx_tags(raw_lines) if suffix in WEB_EXT else raw_lines
    lines = core.jsx_lines(norm_lines, normalized=True) if suffix in JSX_LIKE else norm_lines
    skip = core.ignored_lines(raw_lines)
    base_look = (lambda n: n not in skip) if only is None else (lambda n: n in only and n not in skip)
    look = spacing._absorbed_look(raw_lines, norm_lines, base_look) if suffix in WEB_EXT else base_look
    test_from = core.rust_test_start(raw_lines) if suffix == ".rs" else None

    out = []
    if suffix in CODE_EXT and "design" in wanted and not is_theme_file(rel, source):
        _audit_file_design(
            rel, path, lines, source, suffix, test_from, look, skip, tokens, out,
            cache=cache, include_consistency=include_consistency,
        )
    if "design" in wanted and not is_theme_file(rel, source):
        _composed_slop_pass(
            rel, raw_lines, suffix, base_look, skip, test_from,
            bool(MODAL_CALLEE_RE.search(rel)), out, full=True,
        )
    if "style" in wanted:
        _style_checks(rel, raw_lines, suffix, base_look, skip, test_from, tokens, out, source=source)
    if "spacing" in wanted:
        _spacing_checks(rel, raw_lines, suffix, base_look, test_from, out, source=source)
    if suffix in core.CODE and "bloat" in wanted:
        out += [f for f in hunt.scan_file(path, only) if f.kind == "copy"]
    return out


def stop_report(repo, session, state, gate, files=None, lean=False, style_only=False):
    """Hook report for newly edited UI files. lean (every session): only layout clutter (dividers,
    boxes, chips, stacked texts); a tautology-debloat-design session also checks hardcoded styles, and
    so does a file where an AI visual tic was reported. style_only: the pass right after a block, on
    the files that had a tic: did the fix bring a hardcoded color, size or radius?"""
    edited = state / f"{session}.files"
    shown = state / f"{session}.design.shown"
    if files is None:
        if not edited.is_file():
            return None
        file_paths = [Path(raw) for raw in dict.fromkeys(edited.read_text(encoding="utf-8").splitlines()) if raw]
    else:
        file_paths = [Path(raw) for raw in files if raw]
    if not file_paths:
        return None
    os.chdir(repo)
    root = repo.resolve()
    core.use_repo(root)
    tics = core.tic_files(state, session)
    if style_only:
        file_paths = [p for p in file_paths if core.relative(p) in tics]
        if not file_paths:
            return None
    styled = {p for p in file_paths if style_only or not lean or core.relative(p) in tics}
    # Lean mode skips the full style token walk, but still checks Rust kit helpers so hook == CLI.
    if styled:
        tokens = discover_project_tokens(root)
    else:
        tokens = {"uses_tooltips": bool(core.C.raw.get("tooltip_calls"))}
        if any(p.suffix.lower() == ".rs" for p in file_paths):
            ph, nph = _discover_kit_helpers(root)
            tokens["primary_helpers"], tokens["non_primary_helpers"] = ph, nph
    cache = {}
    findings = []
    for path in file_paths:
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        snap = gate.snapshot_of(state, session, path)
        only = gate.new_lines(snap, path) if snap.is_file() else core.added_lines(path)
        if only is None or only:
            checks = {"style"} if style_only else ({"design", "style"} | ({"spacing"} if not lean else set())) if path in styled else {"design"}
            findings += scan_file(
                path, only=only, tokens=tokens, checks=checks, cache=cache,
                include_consistency=not lean,
            )
    if not lean:  # pruning rescans every check, style included; a full run does it
        core.prune_paths(
            file_paths,
            lambda p: raw_scan_file(p, only=None, tokens=tokens, checks=CHECKS, cache=cache),
            "design_audit.py",
            repo=root,
        )
    before = set(shown.read_text(encoding="utf-8").splitlines()) if shown.is_file() else set()
    keyed = {hashlib.sha1(f.key().encode()).hexdigest()[:16]: f for f in findings}
    fresh = [k for k in keyed if k not in before]
    new = [keyed[k] for k in fresh]
    core.note_tics(new, state, session)
    if not new:
        return None
    limit = 4 if lean else 8
    with shown.open("a", encoding="utf-8") as handle:  # also the cut ones: never back in a later turn
        handle.write("\n".join(fresh) + "\n")
    if style_only:
        lines = ["The tic fix hardcodes styles; use the project's tokens instead:", *core.report_lines(new[:limit])]
        return "\n".join(lines)
    lines = ["UI layout clutter:" if lean else
             f"Design debloat check before delivering (skill tautology-debloat-design, {(HERE / 'SKILL.md').as_posix()})."]
    lines += core.report_lines(new[:limit])
    if len(new) > limit:
        lines.append(f"(+{len(new) - limit} more in CLI)")
    accept = core.accept_command(HERE / "design_audit.py", reason="<why>")
    lines.append(f"Not clutter? Accept silently: {accept}." if lean else
                 "Fix overloaded layout hierarchy and replace hardcoded styles with project theme variables/tokens. "
                 f"False positive? Accept silently: {accept}.")
    return "\n".join(lines)


def selftest():
    """Checks design/style findings, clean theme compliance, --find, and session hooks end to end."""
    import json
    import shutil
    import subprocess
    import tempfile

    problems = []
    repo = Path(tempfile.mkdtemp(prefix="debloat-"))
    try:
        shutil.copytree(CASES, repo, dirs_exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        core.SELFTEST = True
        os.chdir(repo)
        core.use_repo(repo)

        found_screens = find_screens("dirty", root=repo)
        if not found_screens or "dirty_modal.rs" not in found_screens[0][1]:
            problems.append(f"--find failed to locate dirty_modal.rs: {found_screens!r}")

        tokens = discover_project_tokens(repo)
        dirty = scan_file(repo / "src" / "screens" / "dirty_modal.rs", only=None, tokens=tokens, checks=CHECKS)
        dirty_text = "\n".join(str(f) for f in dirty)
        for needed in (
            "overloaded hierarchy",
            "dividers/separators",
            "explicit spacer boxes",
            "hardcoded color",
            "hardcoded text_size",
            "hardcoded corner radius",
        ):
            if needed not in dirty_text:
                problems.append(f"dirty_modal.rs missing expected finding {needed!r}:\n{dirty_text}")

        clean = scan_file(repo / "src" / "screens" / "clean_modal.rs", only=None, tokens=tokens, checks=CHECKS)
        if clean:
            problems.append(f"clean_modal.rs reported false positive(s): {[str(f) for f in clean]!r}")

        theme_file = scan_file(repo / "src" / "theme" / "mod.rs", only=None, tokens=tokens, checks=CHECKS)
        if theme_file:
            problems.append(f"theme/mod.rs reported false positive(s): {[str(f) for f in theme_file]!r}")

        modular = scan_file(repo / "src" / "screens" / "modular_dirty.rs", only=None, tokens=tokens, checks={"design"})
        modular_text = "\n".join(str(f) for f in modular)
        for needed in ("composed hierarchy", "boxed cards/panels across composed view", "info_hint badges across composed view"):
            if needed not in modular_text:
                problems.append(f"modular_dirty.rs missing expected finding {needed!r}:\n{modular_text}")

        # Stacked/exposed filter toolbar + competing solid primary accents + fixed-width triggers.
        dirty_bar = scan_file(repo / "src" / "screens" / "toolbar_clutter.rs", only=None, tokens=tokens, checks={"design"})
        dirty_bar_text = "\n".join(str(f) for f in dirty_bar)
        for needed in (
            "competing primary accents",
            "stacked filter controls across composed view",
            "fixed-width filter/sort trigger (175px)",
            "fixed-width filter/sort trigger (199px)",
        ):
            if needed not in dirty_bar_text:
                problems.append(f"toolbar_clutter.rs missing expected finding {needed!r}:\n{dirty_bar_text}")

        clean_bar = scan_file(repo / "src" / "screens" / "toolbar_clean.rs", only=None, tokens=tokens, checks=CHECKS)
        if clean_bar:
            problems.append(f"toolbar_clean.rs reported false positive(s): {[str(f) for f in clean_bar]!r}")

        # Spacing rhythm: off-scale values, lopsided padding, label far from its control, uneven like
        # siblings, irregular spacers, heading closer to what precedes it, inner stack looser.
        spaced = scan_file(repo / "src" / "screens" / "spacing_dirty.rs", only=None, tokens=tokens, checks={"spacing"})
        spaced_text = "\n".join(str(f) for f in spaced)
        for needed in (
            "render_profile_form: off-scale spacing 26px",
            "lopsided padding on div: 24px above, 8px below",
            "text_field sits 40px from its label field_label but 8px from the next item",
            "uneven spacing between like siblings (text_field): 32px and 8px",
            "irregular spacing between siblings of div: 24, 16, 6px",
            "section_title sits closer to what precedes it (4px) than to its content (20px)",
            "inner stack div spaced looser (24px) than its parent (8px)",
        ):
            if needed not in spaced_text:
                problems.append(f"spacing_dirty.rs missing expected finding {needed!r}:\n{spaced_text}")
        spaced_css = "\n".join(str(f) for f in scan_file(
            repo / "src" / "screens" / "spacing_form.css", only=None, tokens=tokens, checks={"spacing"}))
        for needed in (".form-row: lopsided padding: 24px above, 8px below", ".form-card: off-scale spacing 26px"):
            if needed not in spaced_css:
                problems.append(f"spacing_form.css missing expected finding {needed!r}:\n{spaced_css}")
        if ".form-title" in spaced_css or ".form-field" in spaced_css:
            problems.append(f"spacing_form.css flagged a heading margin or an even field:\n{spaced_css}")
        # A label tight to its input, a heading with more room above, border compensation, and
        # spacing inside a `.when` closure are all fine.
        spaced_clean = scan_file(repo / "src" / "screens" / "spacing_clean.rs", only=None, tokens=tokens, checks=CHECKS)
        if spaced_clean:
            problems.append(f"spacing_clean.rs reported false positive(s): {[str(f) for f in spaced_clean]!r}")
        # Tailwind: one element per class attribute, variants judged on their own (stacked ones
        # falling back to the outer variant), heading by tag or class, never by a `title=` attribute.
        spaced_tw = scan_file(repo / "src" / "screens" / "spacing_cards.tsx", only=None, tokens=tokens, checks={"spacing"})
        lopsided_tw = sorted(f.line for f in spaced_tw if "lopsided" in f.problem)
        if lopsided_tw != [4, 8, 9] or not any("off-scale spacing 26px" in f.problem and f.line == 10 for f in spaced_tw):
            problems.append("spacing_cards.tsx expected lopsided on lines 4, 8, 9 and 26px off-scale on 10:\n"
                            + "\n".join(str(f) for f in spaced_tw))
        # KPI stat strips (literal and mapped) and sibling cards with uneven padding (shadcn-style, no bg-).
        kpi_dirty = "\n".join(str(f) for f in scan_file(
            repo / "src" / "screens" / "kpi_dirty.tsx", only=None, tokens=tokens, checks={"design"}))
        for needed in (
            "OrdersScreen: KPI stat strip (3 metric cards",
            "OrdersScreen: inconsistent padding across sibling cards (8px, 16px, 24px)",
            "TeamScreen: KPI stat strip (a mapped row of metric cards",
        ):
            if needed not in kpi_dirty:
                problems.append(f"kpi_dirty.tsx missing expected finding {needed!r}:\n{kpi_dirty}")
        # Batch 2: shadcn/Vue kit primaries, Separator/v-divider, composed cn()/cva() and hoisted class
        # constants, CSS/SFC <style> rule blocks, GPUI gradient chains, StatCard strips, per-row badges.
        slop_dir = repo / "src" / "screens" / "slop"
        slop_expect = {
            "Toolbar.tsx": ("IssueToolbar: 3 competing primary accents", "IssueSidebar: 4 dividers/separators",
                            "IssueRows: 3 badges per list row", "Overview: KPI stat strip (3 metric cards <StatCard"),
            "Composed.tsx": ("SplitHero: decorative gradient/glassmorphism surface", "CvaDot: decorative pulsing status dot",
                             "Composed.tsx:4 [design] Composed: decorative gradient/glassmorphism surface"),
            "BillingVue.vue": ("BillingVue: 4 dividers/separators", "BillingVue: 2 competing primary accents",
                               "decorative gradient/glassmorphism surface (CSS .hero)",
                               "decorative gradient text (CSS .hero-title)", "decorative pulsing status dot (CSS .dot)",
                               "3 boxed surfaces nested in one component (CSS .plan-card"),
            "panel.css": ("decorative gradient/glassmorphism surface (CSS .panel)", "decorative gradient text (CSS .headline)"),
            "welcome.rs": ("welcome_hero: decorative gradient/glassmorphism surface (gradient fill + heavy shadow)",),
        }
        for fname, needs in slop_expect.items():
            got = "\n".join(str(f) for f in scan_file(slop_dir / fname, only=None, tokens=tokens, checks={"design"}))
            for needed in needs:
                if needed not in got:
                    problems.append(f"slop/{fname} missing expected finding {needed!r}:\n{got}")
        lean_composed = [str(f) for f in scan_lean_design(
            slop_dir / "Composed.tsx", "src/screens/slop/Composed.tsx",
            (slop_dir / "Composed.tsx").read_text(encoding="utf-8"), repo)]
        if len(lean_composed) != len(set(f.split(" -> ")[0].split(" [design] ")[1] for f in lean_composed)):
            problems.append(f"lean scan duplicated a composed slop finding: {lean_composed!r}")
        for fname in ("Clean.tsx", "clean.css"):
            fp = [str(f) for f in scan_file(slop_dir / fname, only=None, tokens=tokens, checks={"design"})]
            if fp:
                problems.append(f"slop/{fname} reported false positive(s): {fp!r}")
        kpi_clean = scan_file(repo / "src" / "screens" / "kpi_clean.tsx", only=None, tokens=tokens, checks=CHECKS)
        if kpi_clean:
            problems.append(f"kpi_clean.tsx reported false positive(s): {[str(f) for f in kpi_clean]!r}")
        spaced_qt = "\n".join(str(f) for f in scan_file(
            repo / "src" / "screens" / "spacing_qt.py", only=None, tokens=tokens, checks={"spacing"}))
        for needed in ("build_panel: lopsided margins: 24px above, 6px below", "build_panel: off-scale spacing 26px"):
            if needed not in spaced_qt:
                problems.append(f"spacing_qt.py missing expected finding {needed!r}:\n{spaced_qt}")

        # W1 (multi-line None) + W2 (if / else if / else + early return) must not false-positive.
        branched = scan_file(repo / "src" / "screens" / "branched_clean.rs", only=None, tokens=tokens, checks={"design"})
        if branched:
            problems.append(f"branched_clean.rs reported false positive(s): {[str(f) for f in branched]!r}")

        # W3 cross-file screen composition (split_screen/view.rs -> panels.rs).
        split_found = scan_file(repo / "src" / "screens" / "split_screen" / "view.rs", only=None, tokens=tokens, checks={"design"})
        split_text = "\n".join(str(f) for f in split_found)
        if "boxed cards/panels across composed view" not in split_text:
            problems.append(f"split_screen/view.rs missing cross-file composed cards finding:\n{split_text}")

        # W5 single-line chained nesting & sibling separation.
        inline_nested = repo / "src" / "screens" / "inline_chain.rs"
        inline_nested.write_text(
            "use gpui::{div, prelude::*};\nuse crate::theme::Palette;\n"
            "pub fn stacked(p: Palette) -> impl IntoElement {\n"
            "    div().bg(p.bg).child(div().bg(p.surface).child(div().border_1().child(div().bg(p.card).child(\"x\"))))\n"
            "}\n"
            "pub fn siblings(p: Palette) -> impl IntoElement {\n"
            "    div().bg(p.bg).child(div().bg(p.card).child(\"a\")).child(div().bg(p.card).child(\"b\"))\n"
            "}\n",
            encoding="utf-8",
        )
        inline_res = "\n".join(str(f) for f in scan_file(inline_nested, only=None, tokens=tokens, checks={"design"}))
        if "stacked: overloaded hierarchy with 4 nested" not in inline_res or "siblings:" in inline_res:
            problems.append(f"inline_chain.rs nesting mismatch:\n{inline_res}")

        # End-to-end Antigravity hook check with /tautology-debloat-design prompt + W6 persistence
        mod_gate = MODULAR_DIR / "hooks" / "modular_gate.py"
        edit_gate = TAUTOLOGY_DIR / "hooks" / "edit_gate.py"
        stop_gate = TAUTOLOGY_DIR / "hooks" / "stop_gate.py"

        def run_prompt(text):
            return subprocess.run(
                [sys.executable, str(mod_gate)],
                input=json.dumps({
                    "hookEventName": "PreInvocation",
                    "conversationId": "debloat-selftest",
                    "cwd": str(repo),
                    "lastUserInput": text,
                }),
                capture_output=True, text=True, encoding="utf-8", cwd=repo,
            )

        r = run_prompt("/tautology-debloat-design clean_modal")
        design_flag = repo / ".git/tautology/debloat-selftest.design"
        if r.returncode != 0 or not design_flag.is_file():
            problems.append(f"modular_gate did not set .design flag: rc={r.returncode} stderr={r.stderr}")

        # W6: iterative UI follow-ups must preserve .design flag; explicit task switch must clear it.
        run_prompt("pode remover a borda do card e ajustar o espacamento do header")
        if not design_flag.is_file():
            problems.append("modular_gate dropped .design flag on UI follow-up prompt")
        run_prompt("pode aplicar")
        if not design_flag.is_file():
            problems.append("modular_gate dropped .design flag on short continuation prompt")
        run_prompt("qual o status do pedido 123?")
        if design_flag.is_file():
            problems.append("modular_gate kept .design flag on unrelated short prompt")
        run_prompt("/tautology-debloat-design clean_modal")
        run_prompt("agora vamos auditar os testes unitarios do worker e depois fazer git commit")
        if design_flag.is_file():
            problems.append("modular_gate kept .design flag after switching away to tests/commit")
        run_prompt("/tautology-debloat-design clean_modal")

        target = repo / "src" / "screens" / "edited_modal.rs"
        subprocess.run(
            [sys.executable, str(edit_gate)],
            input=json.dumps({
                "hookEventName": "PreToolUse",
                "conversationId": "debloat-selftest",
                "cwd": str(repo),
                "toolCall": {"name": "edit_file", "args": {"Path": str(target)}},
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        target.write_text((repo / "src" / "screens" / "dirty_modal.rs").read_text(encoding="utf-8"), encoding="utf-8")
        r1 = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "debloat-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        out1 = json.loads(r1.stdout or "{}")
        reason = out1.get("reason", "")
        if out1.get("decision") != "continue" or "Design debloat check" not in reason or "hardcoded color" not in reason:
            problems.append(f"stop_gate did not enforce design_audit during .design session: {out1!r}")

        r2 = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "debloat-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        if json.loads(r2.stdout or "{}") != {}:
            problems.append(f"repeated stop_gate was not suppressed: {r2.stdout!r}")
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    # W4: Tailwind arbitrary values, .css outside theme, and late-discovered z_theme CSS tokens after >50 UI files.
    web_repo = Path(tempfile.mkdtemp(prefix="debloat-web-"))
    try:
        for idx in range(55):
            f = web_repo / "src" / f"a_{idx:02d}_widget.tsx"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("export function W() { return <div>ok</div>; }\n", encoding="utf-8")
        tok_css = web_repo / "src" / "z_theme" / "tokens.css"
        tok_css.parent.mkdir(parents=True, exist_ok=True)
        tok_css.write_text(":root {\n  --bg: #111111;\n  --font-size-body: 14px;\n  --radius-card: 12px;\n}\n", encoding="utf-8")
        card_tsx = web_repo / "src" / "components" / "TailwindCard.tsx"
        card_tsx.parent.mkdir(parents=True, exist_ok=True)
        card_tsx.write_text(
            "export function TailwindCard() {\n"
            "  return (\n"
            "    <div className=\"bg-[#ff00aa] text-[15px] rounded-[14px]\">\n"
            "      <div className=\"bg-[var(--bg)] text-sm rounded-lg\">Clean</div>\n"
            "      <div className=\"bg-card\"><div className=\"bg-card\"><div className=\"border\"><div className=\"bg-muted\">deep</div></div></div></div>\n"
            "    </div>\n"
            "  );\n"
            "}\n",
            encoding="utf-8",
        )
        card_css = web_repo / "src" / "components" / "CardExtras.css"
        card_css.write_text(
            ":root { --local-surface: #222222; }\n"
            ".card-extra { color: #ff00aa; font-size: 15px; border-radius: 14px; background: var(--bg); }\n",
            encoding="utf-8",
        )
        mod_css = web_repo / "src" / "components" / "styles.css"
        mod_css.write_text(".mod { color: #ff00aa; font-size: 15px; border-radius: 14px; }\n", encoding="utf-8")
        hook_ts = web_repo / "src" / "hooks" / "useCard.ts"
        hook_ts.parent.mkdir(parents=True, exist_ok=True)
        hook_ts.write_text("import { useState } from 'react'\nexport function useCard() { return useState(false) }\n", encoding="utf-8")
        os.chdir(web_repo)
        core.use_repo(web_repo)
        web_tokens = discover_project_tokens(web_repo)
        if not (web_tokens["has_palette"] and web_tokens["has_font_tokens"] and web_tokens["has_radius_tokens"]):
            problems.append(f"discover_project_tokens missed late CSS tokens after >50 files: {web_tokens!r}")
        tsx_out = "\n".join(str(f) for f in scan_file(card_tsx, only=None, tokens=web_tokens, checks=CHECKS))
        for needed in ("Tailwind arbitrary color", "Tailwind font-size", "Tailwind radius", "overloaded hierarchy"):
            if needed not in tsx_out:
                problems.append(f"TailwindCard.tsx missing expected finding {needed!r}:\n{tsx_out}")
        css_out = "\n".join(str(f) for f in scan_file(card_css, only=None, tokens=web_tokens, checks=CHECKS))
        for needed in ("CSS color literal", "CSS font-size", "CSS border-radius"):
            if needed not in css_out:
                problems.append(f"CardExtras.css missing expected finding {needed!r}:\n{css_out}")
        if "#222222" in css_out:
            problems.append(f"CardExtras.css falsely flagged :root custom property:\n{css_out}")
        tok_out = scan_file(tok_css, only=None, tokens=web_tokens, checks=CHECKS)
        if tok_out:
            problems.append(f"z_theme/tokens.css reported false positive(s): {[str(f) for f in tok_out]!r}")
        mod_out = "\n".join(str(f) for f in scan_file(mod_css, only=None, tokens=web_tokens, checks=CHECKS))
        if "CSS color literal" not in mod_out:
            problems.append(f"components/styles.css was skipped as theme file:\n{mod_out}")
        find_hits = [rel for _, rel, _ in find_screens("card", root=web_repo)]
        if not find_hits or find_hits[0] != "src/components/TailwindCard.tsx":
            problems.append(f"find_screens('card') did not prioritize component JSX over hooks: {find_hits!r}")

        # State discovery + generic manifest + pure-stdlib visual PNG checks
        states_manifest = web_repo / "states.json"
        states_manifest.write_text(json.dumps({
            "states": [
                {"id": "tailwind-empty", "label": "Empty state", "screen": "TailwindCard"},
                {"id": "other-screen", "label": "Other", "screen": "unrelated"},
            ]
        }), encoding="utf-8")
        cfg_file = web_repo / ".claude" / "tautology" / "config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps({"design": {"states_file": "states.json"}}), encoding="utf-8")
        core.use_repo(web_repo)
        card_tsx.write_text(
            card_tsx.read_text(encoding="utf-8")
            + "\nexport type CardMode = 'compact' | 'expanded';\n"
            + "// std::env::var(\"APP_MODAL_PREVIEW\") or process.env.PREVIEW_STATE\n",
            encoding="utf-8",
        )
        discovered = discover_states([card_tsx], root=web_repo)
        if "CardMode" not in discovered["enums"] or "tailwind-empty" not in [s["id"] for s in discovered["manifest"]]:
            problems.append(f"discover_states missed expected enum or manifest state: {discovered!r}")

        blank_png = web_repo / "blank.png"
        before_png = web_repo / "before.png"
        after_png = web_repo / "after.png"
        pw, ph = 240, 220
        visual_check.write_png(blank_png, pw, ph, [bytes([18, 18, 22] * pw) for _ in range(ph)])
        before_rows, after_rows = [], []
        for y in range(ph):
            row_a, row_b = bytearray(), bytearray()
            for x in range(pw):
                c_a = (18, 18, 22)
                c_b = (18, 18, 22)
                # Dense boxed grid in "before" vs fewer elements in "after" across 10+ shaded shades
                if 20 <= x < 220 and 20 <= y < 190:
                    if x in (20, 219) or y in (20, 189) or (y % 40 == 0):
                        c_a = (120 + (y % 10), 130, 150)
                    elif 30 <= x < 180 and 30 <= y < 170:
                        c_a = (52 + (x % 9), 56, 66)
                if 20 <= x < 200 and 30 <= y < 110:
                    c_b = (110 + (x % 13), 125 + ((y // 16) * 10), 150)
                row_a.extend(c_a)
                row_b.extend(c_b)
            before_rows.append(bytes(row_a))
            after_rows.append(bytes(row_b))
        visual_check.write_png(before_png, pw, ph, before_rows)
        visual_check.write_png(after_png, pw, ph, after_rows)

        if visual_check.inspect_image(blank_png)["ok"]:
            problems.append("inspect_image accepted a uniform blank PNG")
        before_info = visual_check.inspect_image(before_png)
        if not before_info["ok"]:
            problems.append(f"inspect_image rejected valid test PNG: {before_info!r}")
        cmp_res = visual_check.compare_images(before_png, after_png)
        if not cmp_res["ok"] or cmp_res["density_delta"] >= 0 or not cmp_res["regions"]:
            problems.append(f"compare_images failed on before->after reduction: {cmp_res!r}")
        if visual_check.inspect_image(web_repo / "missing.png")["ok"]:
            problems.append("inspect_image accepted a nonexistent file")
        corrupt_png = web_repo / "corrupt.png"
        corrupt_png.write_bytes(visual_check.SIG + b"\x00\x00\x00\x0dIHDRbad")
        if visual_check.inspect_image(corrupt_png)["ok"]:
            problems.append("inspect_image accepted a corrupt PNG")
        import struct
        bmp_path = web_repo / "before.bmp"
        bmp_body = b"".join(
            bytes(b for i in range(0, len(row), 3) for b in (row[i + 2], row[i + 1], row[i]))
            for row in reversed(before_rows)
        )
        bmp_hdr = b"BM" + struct.pack("<IHHI", 54 + len(bmp_body), 0, 0, 54)
        bmp_dib = struct.pack("<IIIHHIIIIII", 40, pw, ph, 1, 24, 0, len(bmp_body), 2835, 2835, 0, 0)
        bmp_path.write_bytes(bmp_hdr + bmp_dib + bmp_body)
        bmp_info = visual_check.inspect_image(bmp_path)
        if not bmp_info["ok"] or bmp_info["width"] != pw or bmp_info["height"] != ph:
            problems.append(f"inspect_image failed on non-PNG BMP capture: {bmp_info!r}")

        # Regression: component factory declarations (`pub fn primary_button`, `pub fn solid_card`) must not self-count
        kit_rs = web_repo / "src" / "components" / "kit.rs"
        kit_rs.write_text(
            "use gpui::prelude::*;\n"
            "pub fn primary_button(p: Palette) -> Button {\n"
            "    button().bg(p.primary).text_color(p.on_primary())\n"
            "}\n"
            "pub fn solid_card(p: Palette) -> impl IntoElement {\n"
            "    div().bg(p.bg)\n"
            "        .child(div().bg(p.surface)\n"
            "            .child(div().border_1().child(\"ok\")))\n"
            "}\n",
            encoding="utf-8",
        )
        kit_out = "\n".join(str(f) for f in scan_file(kit_rs, only=None, tokens=web_tokens, checks={"design"}))
        if "competing primary accents" in kit_out or "overloaded hierarchy" in kit_out:
            problems.append(f"component factory declaration falsely triggered design finding:\n{kit_out}")

        # Regression: *_layer / *_drawer / *_sheet overlays, complementary single-subject if branches
        # (is_some/else vs if let Some, eq variants), and early-return local vs callee unification
        # must not false-positive; same-polarity / reassigned / non-modal helpers must still flag.
        for hit in ("key_editor_layer", "notice_layer", "filter_drawer", "ConfirmDialog", "BottomSheet"):
            if not MODAL_CALLEE_RE.search(hit):
                problems.append(f"MODAL_CALLEE_RE failed to match overlay helper {hit!r}")
        for non_hit in ("sound_player", "display_player", "toolbar", "render_filters"):
            if MODAL_CALLEE_RE.search(non_hit):
                problems.append(f"MODAL_CALLEE_RE falsely matched non-modal helper {non_hit!r}")

        layered_rs = web_repo / "src" / "screens" / "layered_settings.rs"
        layered_rs.parent.mkdir(parents=True, exist_ok=True)
        layered_rs.write_text(
            "use gpui::{div, prelude::*};\n"
            "impl SettingsView {\n"
            "    fn updates_section(&self, p: Palette) -> impl IntoElement {\n"
            "        let check = if action_label.is_some() {\n"
            "            kit::secondary_button(\"check\", p)\n"
            "        } else {\n"
            "            kit::primary_button(\"check\", p, true)\n"
            "        };\n"
            "        let mut row = div().child(check);\n"
            "        if let Some(lbl) = action_label {\n"
            "            row = row.child(kit::primary_button(\"act\", p, true).child(lbl));\n"
            "        }\n"
            "        row\n"
            "    }\n"
            "    fn tab_body(&self, p: Palette) -> impl IntoElement {\n"
            "        let mut body = div();\n"
            "        if self.tab == Tab::A {\n"
            "            body = body.child(kit::primary_button(\"a\", p, true));\n"
            "        }\n"
            "        if self.tab == Tab::B {\n"
            "            body = body.child(kit::primary_button(\"b\", p, true));\n"
            "        }\n"
            "        body\n"
            "    }\n"
            "    fn key_editor_layer(&self, p: Palette) -> Option<gpui::Div> {\n"
            "        Some(div().child(kit::primary_button(\"save\", p, true)))\n"
            "    }\n"
            "    fn notice_layer(&self, p: Palette) -> Option<gpui::Div> {\n"
            "        Some(div().child(kit::primary_button(\"ok\", p, true)))\n"
            "    }\n"
            "    fn filter_drawer(&self, p: Palette) -> Option<gpui::Div> {\n"
            "        Some(div().child(kit::primary_button(\"apply\", p, true)))\n"
            "    }\n"
            "    pub fn render(&self, p: Palette) -> impl IntoElement {\n"
            "        if self.blocked {\n"
            "            return div().child(kit::primary_button(\"retry\", p, true));\n"
            "        }\n"
            "        let body = if self.show_updates {\n"
            "            self.updates_section(p)\n"
            "        } else {\n"
            "            self.tab_body(p)\n"
            "        };\n"
            "        div()\n"
            "            .child(body)\n"
            "            .children(self.key_editor_layer(p))\n"
            "            .children(self.notice_layer(p))\n"
            "            .children(self.filter_drawer(p))\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        layered_out = "\n".join(str(f) for f in scan_file(layered_rs, only=None, tokens=web_tokens, checks={"design"}))
        if "competing primary accents" in layered_out:
            problems.append(f"layered_settings.rs falsely triggered competing primary accents:\n{layered_out}")

        same_pol_rs = web_repo / "src" / "screens" / "same_polarity_dirty.rs"
        same_pol_rs.write_text(
            "use gpui::{div, prelude::*};\n"
            "pub fn same_polarity(p: Palette, flag: bool) -> impl IntoElement {\n"
            "    let mut row = div();\n"
            "    if flag {\n"
            "        row = row.child(kit::primary_button(\"a\", p, true));\n"
            "    }\n"
            "    if flag {\n"
            "        row = row.child(kit::primary_button(\"b\", p, true));\n"
            "    }\n"
            "    row\n"
            "}\n"
            "pub fn reassigned_opt(p: Palette, mut opt: Option<&str>) -> impl IntoElement {\n"
            "    let mut row = div();\n"
            "    if opt.is_none() {\n"
            "        row = row.child(kit::primary_button(\"a\", p, true));\n"
            "    }\n"
            "    opt = Some(\"x\");\n"
            "    if let Some(_) = opt {\n"
            "        row = row.child(kit::primary_button(\"b\", p, true));\n"
            "    }\n"
            "    row\n"
            "}\n",
            encoding="utf-8",
        )
        same_pol_out = "\n".join(str(f) for f in scan_file(same_pol_rs, only=None, tokens=web_tokens, checks={"design"}))
        for fn_expected in ("same_polarity: 2 competing primary accents", "reassigned_opt: 2 competing primary accents"):
            if fn_expected not in same_pol_out:
                problems.append(f"same_polarity_dirty.rs missed expected finding {fn_expected!r}:\n{same_pol_out}")

        # Regression: GPUI modal states (Pending::Search/Register, searched_once, offers/keywords.is_empty())
        # and GPUI design coverage (kit::segmented + kit::primary_button, inner visual::card + second HEADLINE).
        tg_dir = web_repo / "src" / "screens" / "telegram_modal"
        tg_dir.mkdir(parents=True, exist_ok=True)
        tg_mod = tg_dir / "mod.rs"
        tg_view = tg_dir / "view.rs"
        tg_mod.write_text(
            "pub(super) enum Pending {\n"
            "    Search,\n"
            "    Register,\n"
            "}\n"
            "pub struct TelegramModal {\n"
            "    pub(super) pending: Option<Pending>,\n"
            "    pub(super) searched_once: bool,\n"
            "    pub(super) offers: Vec<String>,\n"
            "    pub(super) keywords: Vec<String>,\n"
            "    pub(super) error: Option<String>,\n"
            "}\n",
            encoding="utf-8",
        )
        tg_view.write_text(
            "use gpui::{div, prelude::*, px};\n"
            "use crate::theme::{Palette, visual};\n"
            "use crate::components::kit;\n"
            "impl TelegramModal {\n"
            "    pub fn render(&self, p: Palette) -> impl IntoElement {\n"
            "        let searching = matches!(self.pending, Some(Pending::Search));\n"
            "        let registering = matches!(self.pending, Some(Pending::Register));\n"
            "        let bar = div()\n"
            "            .flex()\n"
            "            .child(kit::segmented(\"scope\", p))\n"
            "            .child(kit::primary_button(\"search\", p, !searching && !registering));\n"
            "        let monitor = visual::card(p)\n"
            "            .child(div().text_size(px(visual::HEADLINE)).child(\"Monitorar\"))\n"
            "            .when(self.keywords.is_empty(), |el| el.child(\"Sem alertas\"));\n"
            "        let mut body = div()\n"
            "            .bg(p.card)\n"
            "            .border_1()\n"
            "            .child(div().text_size(px(visual::HEADLINE)).child(\"Buscar no Telegram\"))\n"
            "            .child(bar)\n"
            "            .child(monitor);\n"
            "        if let Some(err) = &self.error {\n"
            "            body = body.child(err.clone());\n"
            "        }\n"
            "        if self.offers.is_empty() && self.searched_once {\n"
            "            body = body.child(\"Nenhuma oferta encontrada\");\n"
            "        }\n"
            "        body\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        kit_rs.write_text(
            "use gpui::{div, prelude::*};\n"
            "pub fn primary_button(p: Palette) -> gpui::Div {\n"
            "    div().bg(p.primary).text_color(p.on_primary())\n"
            "}\n"
            "pub fn segmented(_id: &str, p: Palette) -> gpui::Div {\n"
            "    div().bg(p.primary).text_color(p.on_primary())\n"
            "}\n",
            encoding="utf-8",
        )
        tg_states = discover_states([tg_view], root=web_repo)
        if tg_states["enums"].get("Pending") != ["Search", "Register"]:
            problems.append(f"discover_states missed Pending::Search/Register: {tg_states!r}")
        for expected_branch in ("error", "keywords.is_empty()", "offers.is_empty()", "searched_once"):
            if expected_branch not in tg_states["branches"]:
                problems.append(f"discover_states missed GPUI state {expected_branch!r}: {tg_states!r}")
        tg_tokens = discover_project_tokens(web_repo)
        tg_out = "\n".join(str(f) for f in scan_file(tg_view, only=None, tokens=tg_tokens, checks={"design"}))
        for needed in (
            "competing primary accents",
            "competing HEADLINE headings",
            "inner boxed card inside a modal",
        ):
            if needed not in tg_out:
                problems.append(f"telegram_modal/view.rs missing expected GPUI finding {needed!r}:\n{tg_out}")
        # After refactoring kit::segmented to neutral fill and flattening the modal, 0 design findings remain.
        kit_rs.write_text(
            "use gpui::{div, prelude::*};\n"
            "pub fn primary_button(p: Palette) -> gpui::Div {\n"
            "    div().bg(p.primary).text_color(p.on_primary())\n"
            "}\n"
            "pub fn segmented(_id: &str, p: Palette) -> gpui::Div {\n"
            "    div().bg(p.selected).text_color(p.strong)\n"
            "}\n",
            encoding="utf-8",
        )
        tg_view.write_text(
            "use gpui::{div, prelude::*, px};\n"
            "use crate::theme::{Palette, visual};\n"
            "use crate::components::kit;\n"
            "impl TelegramModal {\n"
            "    pub fn render(&self, p: Palette) -> impl IntoElement {\n"
            "        div()\n"
            "            .bg(p.card)\n"
            "            .border_1()\n"
            "            .child(div().text_size(px(visual::HEADLINE)).child(\"Buscar no Telegram\"))\n"
            "            .child(div().flex().child(kit::segmented(\"scope\", p)).child(kit::primary_button(\"search\", p, true)))\n"
            "            .child(div().text_size(px(visual::EMPHASIS)).child(\"Monitorar\"))\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        tg_tokens_clean = discover_project_tokens(web_repo)
        tg_clean_out = scan_file(tg_view, only=None, tokens=tg_tokens_clean, checks={"design"})
        if tg_clean_out:
            problems.append(f"refactored telegram_modal/view.rs reported false positive(s): {[str(f) for f in tg_clean_out]!r}")

        # Review regressions:
        # 1) Non-modal screen with a modal helper must not flag page visual::card as modal inner card.
        # 2) Lean kit helper discovery matches CLI for neutral kit::segmented.
        # 3) Variant-bool kit helper (primary: bool) and method calls (.segmented()) must not false-positive.
        # 4) .when(matches!(self.job, Some(SyncJob::Pull)), ...) must preserve SyncJob::Pull in --states.
        # 5) 2-level modal helper chain (render -> body -> keywords_section) emits inner card once, not twice.
        kit_rs.write_text(
            "use gpui::{div, prelude::*};\n"
            "pub fn primary_button(p: Palette) -> gpui::Div {\n"
            "    div().bg(p.primary).text_color(p.on_primary())\n"
            "}\n"
            "pub fn segmented(_id: &str, p: Palette) -> gpui::Div {\n"
            "    div().bg(p.selected).text_color(p.strong)\n"
            "}\n"
            "pub fn button(_id: &str, primary: bool, p: Palette) -> gpui::Div {\n"
            "    if primary { div().bg(p.primary) } else { div().bg(p.surface) }\n"
            "}\n",
            encoding="utf-8",
        )
        lean_ph, lean_nph = _discover_kit_helpers(web_repo)
        if "segmented" not in lean_nph or "button" in lean_ph:
            problems.append(f"_discover_kit_helpers misclassified segmented/button: ph={lean_ph!r} nph={lean_nph!r}")
        page_with_modal = web_repo / "src" / "screens" / "settings_page.rs"
        page_with_modal.write_text(
            "use gpui::{div, prelude::*};\n"
            "use crate::theme::{Palette, visual};\n"
            "use crate::components::kit;\n"
            "impl SettingsPage {\n"
            "    fn confirm_dialog(&self, p: Palette) -> impl IntoElement {\n"
            "        visual::modal_card(p).child(\"Confirm\")\n"
            "    }\n"
            "    pub fn render(&self, p: Palette) -> impl IntoElement {\n"
            "        div()\n"
            "            .when(matches!(self.job, Some(SyncJob::Pull)), |el| el.child(\"Pulling\"))\n"
            "            .child(visual::card(p).child(kit::button(\"cancel\", false, p)))\n"
            "            .child(kit::primary_button(\"save\", p, true))\n"
            "            .child(self.confirm_dialog(p))\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        rev_tokens = discover_project_tokens(web_repo)
        page_out = scan_file(page_with_modal, only=None, tokens=rev_tokens, checks={"design"})
        if page_out:
            problems.append(f"settings_page.rs reported false positive(s): {[str(f) for f in page_out]!r}")
        page_states = discover_states([page_with_modal], root=web_repo)
        if page_states["enums"].get("SyncJob") != ["Pull"]:
            problems.append(f"discover_states dropped SyncJob::Pull inside .when(matches!(...)): {page_states!r}")
        deep_modal = web_repo / "src" / "screens" / "deep_modal.rs"
        deep_modal.write_text(
            "use gpui::{div, prelude::*};\n"
            "use crate::theme::{Palette, visual};\n"
            "impl DeepModal {\n"
            "    fn keywords_section(&self, p: Palette) -> impl IntoElement {\n"
            "        visual::card(p).child(\"Monitorar\")\n"
            "    }\n"
            "    fn body(&self, p: Palette) -> impl IntoElement {\n"
            "        div().child(self.keywords_section(p))\n"
            "    }\n"
            "    pub fn render(&self, p: Palette) -> impl IntoElement {\n"
            "        div().bg(p.card).border_1().child(self.body(p))\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        deep_out = [f for f in scan_file(deep_modal, only=None, tokens=rev_tokens, checks={"design"})
                    if "inner boxed card inside a modal" in f.problem]
        if len(deep_out) != 1:
            problems.append(f"deep_modal.rs expected 1 inner-card finding, got {len(deep_out)}: {[str(f) for f in deep_out]!r}")

        # Regression: editing unrelated helpers (sources, quota_summary) must not re-fire pre-existing
        # render (composed) findings from untouched tabs/headers, while touching a contributing element still fires.
        games_view = web_repo / "src" / "screens" / "games_view.rs"
        games_view.write_text(
            "use gpui::{div, prelude::*, px};\n"
            "use crate::theme::{Palette, visual};\n"
            "impl GamesView {\n"
            "    fn header(&self, p: Palette) -> impl IntoElement {\n"
            "        div().text_size(px(visual::HEADLINE)).child(\"Jogos\")\n"
            "    }\n"
            "    fn tabs(&self, p: Palette) -> impl IntoElement {\n"
            "        div().text_size(px(visual::HEADLINE)).child(\"Abas\")\n"
            "    }\n"
            "    fn sources(&self, _p: Palette) -> impl IntoElement {\n"
            "        div().child(\"Fontes atualizadas\")\n"
            "    }\n"
            "    fn quota_summary(&self, _p: Palette) -> impl IntoElement {\n"
            "        div().child(\"Quota restante\")\n"
            "    }\n"
            "    pub fn render(&self, p: Palette) -> impl IntoElement {\n"
            "        div()\n"
            "            .child(self.header(p))\n"
            "            .child(self.tabs(p))\n"
            "            .child(self.sources(p))\n"
            "            .child(self.quota_summary(p))\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        # Only lines 10-15 (sources + quota_summary) edited: pre-existing composed HEADLINE in header+tabs must stay silent.
        partial_games = scan_file(games_view, only={10, 11, 12, 13, 14, 15}, tokens=rev_tokens, checks={"design"})
        if partial_games:
            problems.append(f"games_view.rs leaked pre-existing composed finding on unrelated helper edit: {[str(f) for f in partial_games]!r}")
        # Full scan (--all) still catches render (composed) competing HEADLINE headings.
        full_games = "\n".join(str(f) for f in scan_file(games_view, only=None, tokens=rev_tokens, checks={"design"}))
        if "render (composed): 2 competing HEADLINE headings" not in full_games:
            problems.append(f"games_view.rs --all missed composed HEADLINE finding:\n{full_games}")
        # Editing a line that actually touches a HEADLINE still reports render (composed).
        touched_headline = "\n".join(str(f) for f in scan_file(games_view, only={8}, tokens=rev_tokens, checks={"design"}))
        if "render (composed): 2 competing HEADLINE headings" not in touched_headline:
            problems.append(f"games_view.rs missed composed HEADLINE finding when tab HEADLINE was edited:\n{touched_headline}")

        # Regression: branch-aware touched tracking — editing a clean tab's single HEADLINE must not
        # trigger a pre-existing 2-HEADLINE finding from an untouched sibling tab.
        tabbed_view = web_repo / "src" / "screens" / "tabbed_view.rs"
        tabbed_view.write_text(
            "use gpui::{div, prelude::*, px};\n"
            "use crate::theme::{Palette, visual};\n"
            "impl TabbedView {\n"
            "    fn head_a(&self, p: Palette) -> impl IntoElement {\n"
            "        div().text_size(px(visual::HEADLINE)).child(\"A1\")\n"
            "    }\n"
            "    fn sub_a(&self, p: Palette) -> impl IntoElement {\n"
            "        div().text_size(px(visual::HEADLINE)).child(\"A2\")\n"
            "    }\n"
            "    fn tab_a(&self, p: Palette) -> impl IntoElement {\n"
            "        div().child(self.head_a(p)).child(self.sub_a(p))\n"
            "    }\n"
            "    fn tab_b(&self, p: Palette) -> impl IntoElement {\n"
            "        div().text_size(px(visual::HEADLINE)).child(\"B1\")\n"
            "    }\n"
            "    pub fn render(&self, p: Palette) -> impl IntoElement {\n"
            "        if self.tab == Tab::A {\n"
            "            self.tab_a(p)\n"
            "        } else {\n"
            "            self.tab_b(p)\n"
            "        }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        tab_b_only = scan_file(tabbed_view, only={14}, tokens=rev_tokens, checks={"design"})
        if tab_b_only:
            problems.append(f"tabbed_view.rs leaked untouched tab_a finding when editing clean tab_b: {[str(f) for f in tab_b_only]!r}")
        tab_all = "\n".join(str(f) for f in scan_file(tabbed_view, only=None, tokens=rev_tokens, checks={"design"}))
        if "render (composed): 2 competing HEADLINE headings" not in tab_all:
            problems.append(f"tabbed_view.rs --all missed composed HEADLINE finding:\n{tab_all}")
        tab_a_edit = "\n".join(str(f) for f in scan_file(tabbed_view, only={8}, tokens=rev_tokens, checks={"design"}))
        if "render (composed): 2 competing HEADLINE headings" not in tab_a_edit:
            problems.append(f"tabbed_view.rs missed composed HEADLINE finding when tab_a was edited:\n{tab_a_edit}")

        # Regression: enum struct variants + #[default] attributes preserved; string literals and inline // comments ignored in --states.
        rich_enum_rs = web_repo / "src" / "screens" / "rich_enum.rs"
        rich_enum_rs.write_text(
            "use gpui::{div, prelude::*};\n"
            "pub enum Pending {\n"
            "    #[default]\n"
            "    Idle,\n"
            "    Register { id: i64, channel: String },\n"
            "    Search,\n"
            "}\n"
            "pub struct RichScreen { offers: Vec<String>, searched_once: bool }\n"
            "impl RichScreen {\n"
            "    pub fn render(&self, key: &str) -> impl IntoElement {\n"
            "        let _ = match key { // searched_once\n"
            "            \"offers\" => 1,\n"
            "            \"Fake::Variant\" => 2,\n"
            "            _ => 0,\n"
            "        };\n"
            "        div()\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        rich_states = discover_states([rich_enum_rs], root=web_repo)
        if rich_states["enums"].get("Pending") != ["Idle", "Register", "Search"]:
            problems.append(f"discover_states truncated enum with #[default]/struct variant: {rich_states!r}")
        if "Fake" in rich_states["enums"] or "offers" in rich_states["branches"] or "searched_once" in rich_states["branches"]:
            problems.append(f"discover_states polluted by string literal or inline comment: {rich_states!r}")

        # Regression: spacing.on_scale(spacing.nearest(v, steps), steps) invariant (no 38px->36px->36px loop, >256px)
        sample_steps = (2., 4., 6., 8., 10., 12., 14., 16., 20., 24.)
        if spacing.on_scale(38., sample_steps) or spacing.nearest(38., sample_steps) != 36.:
            problems.append("spacing 38px should be off-scale and snap to 36px")
        if spacing.nearest(302., sample_steps) != 300.:
            problems.append(f"spacing 302px should snap to 300px, got {spacing.nearest(302., sample_steps)}")
        if not spacing.on_scale(36., sample_steps):
            problems.append("spacing 36px (nearest snap for 38px) must be accepted by on_scale")
        for candidate in range(1, 320):
            snapped = spacing.nearest(float(candidate), sample_steps)
            if not spacing.on_scale(snapped, sample_steps):
                problems.append(f"spacing invariant broken: nearest({candidate})={snapped} is still off-scale")
                break
            snapped_fb = spacing.nearest(float(candidate), None)
            if not spacing.on_scale(snapped_fb, None):
                problems.append(f"fallback spacing invariant broken: nearest({candidate})={snapped_fb} is still off-scale")
                break

        # Wave 1+3: spacing.on_scale fallback math (rejects odd <=16 and 4k+/-1 >16 unless bordered)
        for bad_px in (7., 9., 11., 13., 15., 17., 19., 25.):
            if spacing.on_scale(bad_px, None):
                problems.append(f"spacing.on_scale fallback falsely accepted {bad_px:g}px")
        if not spacing.on_scale(11., None, bordered=True):
            problems.append("spacing.on_scale rejected bordered 11px border compensation")

        # Wave 1+3: Tailwind p-2 pt-8, horizontal pl/pr, cn(...), arbitrary [24px], input negative control
        tw_space = web_repo / "src" / "components" / "SpaceTw.tsx"
        tw_space.write_text(
            "import { cn } from './utils';\n"
            "export default function SpaceTw() {\n"
            "  const extra = cn(\n"
            "    'pt-8 pb-2',\n"
            "    'p-[26px]'\n"
            "  );\n"
            "  return (\n"
            "    <div className=\"p-2 pt-8\">\n"
            "      <div className=\"p-2 pl-8\">horiz</div>\n"
            "      <div className=\"pt-[24px] pb-[4px]\">arb</div>\n"
            "      <div\n"
            "        className={cn(\n"
            "          'pt-8 pb-2',\n"
            "          'p-[25px]'\n"
            "        )}\n"
            "      />\n"
            "      <input className=\"pl-10 pr-3\" />\n"
            "    </div>\n"
            "  );\n"
            "}\n",
            encoding="utf-8",
        )
        tw_space_out = "\n".join(str(f) for f in scan_file(tw_space, only=None, tokens=rev_tokens, checks={"spacing"}))
        for needed in (
            "SpaceTw: lopsided padding: 32px above, 8px below",
            "SpaceTw: lopsided padding: 32px left, 8px right",
            "SpaceTw: lopsided padding: 24px above, 4px below",
            "SpaceTw: off-scale spacing 26px",
            "SpaceTw: off-scale spacing 25px",
        ):
            if needed not in tw_space_out:
                problems.append(f"SpaceTw.tsx missing spacing finding {needed!r}:\n{tw_space_out}")
        if "40px left, 12px right" in tw_space_out:
            problems.append(f"SpaceTw.tsx falsely flagged leading-icon <input> horizontal padding:\n{tw_space_out}")

        # Wave 1+3: CSS auto, !important, rem, horizontal lopsided, and styles/ non-theme vs token-only CSS
        styles_dir = web_repo / "src" / "styles"
        styles_dir.mkdir(parents=True, exist_ok=True)
        dash_css = styles_dir / "Dashboard.css"
        dash_css.write_text(
            ".dash { color: #9333ea; font-size: 15px; border-radius: 14px; margin: 24px auto 4px; }\n"
            ".imp { padding: 24px 16px 4px !important; }\n"
            ".rem { padding: 1.625rem 0.25rem 0.25rem; }\n"
            ".horiz { padding-left: 24px; padding-right: 4px; }\n",
            encoding="utf-8",
        )
        vars_css = styles_dir / "variables.css"
        vars_css.write_text(":root { --brand: #9333ea; --surface: #111111; --gap-md: 16px; }\n", encoding="utf-8")
        dash_css_out = "\n".join(str(f) for f in scan_file(dash_css, only=None, tokens=rev_tokens, checks=CHECKS))
        for needed in (
            "CSS color literal",
            ".dash: lopsided margin: 24px above, 4px below",
            ".imp: lopsided padding: 24px above, 4px below",
            ".rem: off-scale spacing 26px",
            ".rem: lopsided padding: 26px above, 4px below",
            ".horiz: lopsided padding: 24px left, 4px right",
        ):
            if needed not in dash_css_out:
                problems.append(f"styles/Dashboard.css missing finding {needed!r}:\n{dash_css_out}")
        if scan_file(vars_css, only=None, tokens=rev_tokens, checks=CHECKS):
            problems.append("styles/variables.css (>50% custom properties) was not exempted as theme file")

        # Wave 1+3: SlopDashboard.tsx (multi-line JSX + glassmorphism + gradient text + lucide icons + pills + FilterChip + pulsing dot + <p> stack)
        # tautology: off (bad-on-purpose Tailwind fixture strings)
        slop_tsx = web_repo / "src" / "screens" / "SlopDashboard.tsx"
        slop_tsx.write_text(
            'import { Sparkles, Zap, Shield, Rocket, Star, Check, ArrowRight, Bell, Globe, Cpu, Layers, Activity } from "lucide-react";\n'
            "export default function SlopDashboard({ filters }: any) {\n"
            "  return (\n"
            "    <div\n"
            '      className="min-h-screen bg-gradient-to-br from-purple-900 to-slate-900 shadow-2xl p-8"\n'
            "    >\n"
            "      <div\n"
            '        className="rounded-2xl border border-white/10 bg-white/5 backdrop-blur-xl shadow-2xl p-6"\n'
            "      >\n"
            "        <div\n"
            '          className="rounded-xl border border-purple-500/30 bg-slate-900/80 p-4"\n'
            "        >\n"
            "          <div\n"
            '            className="rounded-lg border border-indigo-400/20 bg-slate-800/60 p-4"\n'
            "          >\n"
            "            <div\n"
            '              className="rounded-md border border-slate-700 bg-slate-950 p-3"\n'
            "            >\n"
            '              <h1 className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">AI Studio</h1>\n'
            '              <span className="w-2 rounded-full h-2 bg-emerald-400 animate-pulse" />\n'
            "              <Sparkles /> <Zap /> <Shield /> <Rocket /> <Star /> <Check />\n"
            "              <ArrowRight /> <Bell /> <Globe /> <Cpu /> <Layers /> <Activity />\n"
            '              <span className="inline-flex rounded-full px-2 py-0.5 text-xs bg-emerald-500/20">Live</span>\n'
            '              <span className="inline-flex rounded-full px-2 py-0.5 text-xs bg-purple-500/20">AI Powered</span>\n'
            '              <span className="inline-flex rounded-full px-2 py-0.5 text-xs bg-blue-500/20">Beta</span>\n'
            '              <span className="inline-flex rounded-full px-2 py-0.5 text-xs bg-amber-500/20">Pro</span>\n'
            '              <span className="inline-flex rounded-full px-2 py-0.5 text-xs bg-pink-500/20">New</span>\n'
            '              <span className="inline-flex rounded-full px-2 py-0.5 text-xs bg-cyan-500/20">Fast</span>\n'
            '              <span className="inline-flex rounded-full px-2 py-0.5 text-xs bg-rose-500/20">Hot</span>\n'
            "              <Select />\n"
            "              {filters.map((f: string) => (\n"
            "                <FilterChip key={f}>{f}</FilterChip>\n"
            "              ))}\n"
            "              <p>\n"
            "                Welcome to your AI powered analytics command center today.\n"
            "              </p>\n"
            "              <p>\n"
            "                Track all your metrics seamlessly in one unified workspace.\n"
            "              </p>\n"
            "              <p>\n"
            "                Get instant actionable insights with zero manual configuration needed.\n"
            "              </p>\n"
            "              <p>\n"
            "                Click any card below to explore detailed performance trends.\n"
            "              </p>\n"
            "            </div>\n"
            "          </div>\n"
            "        </div>\n"
            "      </div>\n"
            "    </div>\n"
            "  );\n"
            "}\n",
            encoding="utf-8",
        )
        # tautology: on
        slop_out = "\n".join(str(f) for f in scan_file(slop_tsx, only=None, tokens=rev_tokens, checks={"design"}))
        for needed in (
            "overloaded hierarchy",
            "icons in one view",
            "badges/chips cluttering one view",
            "stacked filter controls",
            "stacked text blocks competing in sequence",
            "decorative gradient/glassmorphism surface",
            "decorative gradient text",
            "decorative pulsing status dot",
        ):
            if needed not in slop_out:
                problems.append(f"SlopDashboard.tsx missing expected finding {needed!r}:\n{slop_out}")
        lean_slop = "\n".join(str(f) for f in scan_lean_design(slop_tsx, "src/screens/SlopDashboard.tsx", slop_tsx.read_text(encoding="utf-8"), web_repo))
        for needed in (
            "cards nested inside each other",
            "decorative badges",
            "decorative gradient/glassmorphism surface",
            "decorative gradient text",
            "decorative pulsing status dot",
        ):
            if needed not in lean_slop:
                problems.append(f"scan_lean_design missed {needed!r} on SlopDashboard.tsx:\n{lean_slop}")

        # Negative controls: multi-line self-closing <Card /> and border-none/transparent must NOT false-positive
        neg_tsx = web_repo / "src" / "screens" / "NegControl.tsx"
        neg_tsx.write_text(
            "export const NegControl: React.FC = () => (\n"
            '  <div className="bg-transparent border-none">\n'
            '    <div className="border-transparent">\n'
            '      <div className="border-none">\n'
            '        <div className="border-none">\n'
            "          <Card\n"
            '            title="One"\n'
            "          />\n"
            "          <Card\n"
            '            title="Two"\n'
            "          />\n"
            "          <Card\n"
            '            title="Three"\n'
            "          />\n"
            "        </div>\n"
            "      </div>\n"
            "    </div>\n"
            "  </div>\n"
            ");\n",
            encoding="utf-8",
        )
        if scan_file(neg_tsx, only=None, tokens=rev_tokens, checks={"design"}):
            problems.append("NegControl.tsx falsely triggered design findings on border-none / self-closing <Card />")
        if scan_lean_design(neg_tsx, "src/screens/NegControl.tsx", neg_tsx.read_text(encoding="utf-8"), web_repo):
            problems.append("scan_lean_design falsely triggered on border-none / multi-line self-closing <Card />")

        # Multi-line JSX style={{ fontSize, borderRadius }} & per-view consistency (full only, NOT lean)
        consist_tsx = web_repo / "src" / "screens" / "ConsistView.tsx"
        consist_tsx.write_text(
            "export const ConsistView: React.FC = () => (\n"
            "  <div>\n"
            "    <div\n"
            "      style={{\n"
            "        fontSize: 15,\n"
            "        borderRadius: 14,\n"
            "      }}\n"
            "    >\n"
            '      <div className="rounded-sm text-xs">a</div>\n'
            '      <div className="rounded-md text-sm">b</div>\n'
            '      <div className="rounded-lg text-base">c</div>\n'
            '      <div className="rounded-2xl text-lg">d</div>\n'
            '      <span className="text-xl">e</span>\n'
            '      <span className="text-2xl">f</span>\n'
            '      <div className="bg-card border p-3">Card 1</div>\n'
            '      <div className="bg-card border p-8">Card 2</div>\n'
            "    </div>\n"
            "  </div>\n"
            ");\n",
            encoding="utf-8",
        )
        consist_full = "\n".join(str(f) for f in scan_file(consist_tsx, only=None, tokens=rev_tokens, checks={"design", "style"}))
        for needed in (
            "hardcoded inline fontSize in JSX style",
            "hardcoded inline borderRadius in JSX style",
            "inconsistent corner radii",
            "inconsistent typography scale",
            "inconsistent padding across sibling cards",
        ):
            if needed not in consist_full:
                problems.append(f"ConsistView.tsx missing {needed!r}:\n{consist_full}")
        consist_lean = "\n".join(str(f) for f in scan_lean_design(consist_tsx, "src/screens/ConsistView.tsx", consist_tsx.read_text(encoding="utf-8"), web_repo))
        if "inconsistent" in consist_lean:
            problems.append(f"scan_lean_design leaked heavy consistency check:\n{consist_lean}")

        # egui Rust support + GPUI status dot in scan_lean_design
        egui_rs = web_repo / "src" / "screens" / "egui_panel.rs"
        egui_rs.write_text(
            "use egui::{Color32, Frame, RichText, Ui};\n"
            "pub fn show_settings(ui: &mut Ui) {\n"
            "    Frame::group(ui.style()).fill(Color32::from_rgb(255, 0, 170)).show(ui, |ui| {\n"
            "        Frame::group(ui.style()).show(ui, |ui| {\n"
            "            Frame::group(ui.style()).show(ui, |ui| {\n"
            "                Frame::group(ui.style()).show(ui, |ui| {\n"
            '                    ui.heading(RichText::new("One").size(22.0));\n'
            '                    ui.heading(RichText::new("Two").size(22.0));\n'
            "                    ui.separator(); ui.separator(); ui.separator(); ui.separator();\n"
            "                    ui.add_space(26.0);\n"
            "                });\n"
            "            });\n"
            "        });\n"
            "    });\n"
            "}\n",
            encoding="utf-8",
        )
        egui_out = "\n".join(str(f) for f in scan_file(egui_rs, only=None, tokens=rev_tokens, checks=CHECKS))
        for needed in ("overloaded hierarchy", "competing HEADLINE", "dividers/separators", "hardcoded color literal", "off-scale spacing 26px"):
            if needed not in egui_out:
                problems.append(f"egui_panel.rs missing {needed!r}:\n{egui_out}")

        gpui_dot_rs = web_repo / "src" / "screens" / "status_bar.rs"
        gpui_dot_rs.write_text(
            "use gpui::{div, prelude::*, px};\n"
            "pub fn status_badge(p: Palette) -> impl IntoElement {\n"
            "    div().flex()\n"
            "        .child(div().size(px(7.)).rounded_full().bg(p.success))\n"
            '        .child(div().text_color(p.text).child("Online"))\n'
            "}\n",
            encoding="utf-8",
        )
        dot_lean = "\n".join(str(f) for f in scan_lean_design(gpui_dot_rs, "src/screens/status_bar.rs", gpui_dot_rs.read_text(encoding="utf-8"), web_repo))
        if "decorative status dot" not in dot_lean:
            problems.append(f"scan_lean_design missed GPUI decorative status dot:\n{dot_lean}")

        # Vue & Svelte support
        vue_f = web_repo / "src" / "screens" / "Panel.vue"
        vue_f.write_text(
            '<template><div class="bg-[#ff00aa] p-[26px] bg-gradient-to-r backdrop-blur">Hi</div></template>\n'
            "<style>.box { padding: 25px; color: #123456; }</style>\n",
            encoding="utf-8",
        )
        svelte_f = web_repo / "src" / "screens" / "Panel.svelte"
        svelte_f.write_text('<div class="bg-[#ff00aa] p-[26px]">Hi</div>\n', encoding="utf-8")
        vue_out = "\n".join(str(f) for f in scan_file(vue_f, only=None, tokens=rev_tokens, checks=CHECKS))
        svelte_out = "\n".join(str(f) for f in scan_file(svelte_f, only=None, tokens=rev_tokens, checks=CHECKS))
        for needed in ("Tailwind arbitrary color", "off-scale spacing 26px", "off-scale spacing 25px", "CSS color literal", "decorative gradient/glassmorphism"):
            if needed not in vue_out:
                problems.append(f"Panel.vue missing {needed!r}:\n{vue_out}")
        for needed in ("Tailwind arbitrary color", "off-scale spacing 26px"):
            if needed not in svelte_out:
                problems.append(f"Panel.svelte missing {needed!r}:\n{svelte_out}")

        # TW_RAW_COLOR_RE gating: requires semantic Tailwind tokens, and runs alongside TW_COLOR_RE
        tw_mix = web_repo / "src" / "screens" / "TwColorMix.tsx"
        tw_mix.write_text('export function TwMix() { return <div className="bg-[#ff00aa] text-purple-500">x</div>; }\n', encoding="utf-8")
        no_sem_tokens = {**rev_tokens, "has_tw_semantic_colors": False}
        sem_tokens = {**rev_tokens, "has_tw_semantic_colors": True}
        out_no_sem = "\n".join(str(f) for f in scan_file(tw_mix, only=None, tokens=no_sem_tokens, checks={"style"}))
        out_sem = "\n".join(str(f) for f in scan_file(tw_mix, only=None, tokens=sem_tokens, checks={"style"}))
        if "stock Tailwind color" in out_no_sem:
            problems.append(f"TW_RAW_COLOR_RE fired without semantic Tailwind tokens:\n{out_no_sem}")
        if "Tailwind arbitrary color" not in out_sem or "stock Tailwind color" not in out_sem:
            problems.append(f"TW_RAW_COLOR_RE skipped alongside TW_COLOR_RE:\n{out_sem}")

        if visual_check.selftest() != 0:
            problems.append("visual_check.selftest() failed")
    finally:
        core.SELFTEST = False
        shutil.rmtree(web_repo, ignore_errors=True)
    for p in problems:
        print(f"FAIL    {p}")
    print(f"design_audit selftest: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


STATE_ENUM_NAME_RE = re.compile(
    r"(?:Tab|Modal|Dialog|State|Mode|Step|Category|View|Section|Filter|Page|Pending|Status|Phase|Stage|Panel|Sheet|Drawer|Overlay|Source|Scope|Busy)"
)
RS_ENUM_HEAD_RE = re.compile(r"\benum\s+([A-Z]\w*)(?:<[^>]*>)?\s*\{")
RS_STRUCT_BLOCK_RE = re.compile(r"\bstruct\s+[A-Z]\w*(?:<[^>]*>)?\s*\{([^}]+)\}", re.S)
RS_STATE_FIELD_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?([a-z_][a-z0-9_]+)\s*:\s*(bool|Option\s*<|Vec\s*<|HashSet\s*<|BTreeSet\s*<|[A-Z]\w*)\b",
    re.M,
)
TS_UNION_RE = re.compile(
    r"\btype\s+(\w*(?:Tab|Modal|Dialog|State|Mode|Step|Category|View|Section|Filter|Page|Pending|Status|Phase|Stage|Panel|Sheet|Drawer|Overlay|Source|Scope|Busy)\w*)\s*=\s*([^;\n]+)"
)
ROUTE_KEY_RE = re.compile(
    r'"([a-z][a-z0-9_-]{1,32})"\s*=>\s*(?:Self|[\w:]*(?:Screen|Tab|Modal|Dialog|State|Mode|Page|Step|Section|Route))::'
    r'|<Route\b[^>]*path=["\']([^"\']+)["\']'
)
ENV_HOOK_RE = re.compile(
    r'(?:std::env::var|os\.environ(?:\.get)?)\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']|process\.env\.([A-Z][A-Z0-9_]{2,})'
)
COND_STATE_RE = re.compile(
    r"\b(?:match|if)\b[^\n{]*\b(loading|empty|error|offline|editing|deleting|confirm\w*|collapsed|expanded|modal|dialog|pending|searching|submitting|saving|busy)\b",
    re.I,
)
RS_COND_EXPR_RE = re.compile(
    r"(?:\b(?:if\s+let\b|else\s+if\b|if\b|match\b)\s*([^\n{;]+)"
    r"|\bmatches!\s*\(([^;\n]+)\)"
    r"|^\s*([^/\n{}]+?)\s*=>)",
    re.M,
)
RS_WHEN_CALL_RE = re.compile(r"\.(?:when|when_some|when_none)\s*\(")
RS_ENUM_VARIANT_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*)::([A-Z][A-Za-z0-9]*)\b")
RS_IS_EMPTY_RE = re.compile(r"(?<!\)\.)(?:\b(?:self|state|model)\.)*([a-z_][a-z0-9_]{2,})\.is_empty\(\)")
RS_SELF_FIELD_RE = re.compile(r"\bself\.([a-z_][a-z0-9_]{2,})\b(?!\s*\()")
STD_STATE_ENUM_SKIP = {
    "Option", "Result", "Ordering", "Self", "FontWeight", "CursorStyle", "AlignItems", "JustifyContent",
    "FlexDirection", "Overflow", "Position", "Display", "MouseButton", "Key", "Keystroke", "Modifiers",
    "Length", "Edges", "Corners", "Rgba", "Hsla", "Pixels", "SharedString", "Entity", "FocusHandle",
    "ScrollHandle", "Path", "PathBuf", "Duration", "Instant", "String", "Vec", "Box", "Arc", "Rc",
    "Cow", "HashMap", "HashSet", "BTreeMap", "BTreeSet", "Window", "Context", "App", "Palette",
}
SKIP_STATE_FIELDS = {
    "theme", "palette", "style", "cx", "ctx", "window", "view", "entity", "handle", "focus_handle",
    "scroll_handle", "bounds", "size", "width", "height", "rect", "color", "bg", "fg", "id", "key",
    "idx", "index", "offset", "scroll", "hovered", "pressed", "focused", "app", "client", "db",
    "store", "sender", "tx", "rx", "task", "subscription", "subscriptions", "weak",
}
GENERIC_EMPTY_SKIP = {
    "str", "text", "line", "val", "value", "raw", "trimmed", "buf", "out", "part",
    "label", "title", "name", "path", "url", "msg", "err", "trim",
}
STATE_SIBLING_NAMES = ("mod.rs", "model.rs", "state.rs", "types.ts", "types.tsx")


def _extract_braced_enums(src):
    """Yields (name, body) for each enum with balanced braces so struct variants do not truncate the enum."""
    for m in RS_ENUM_HEAD_RE.finditer(src):
        depth, pos = 1, m.end()
        while pos < len(src) and depth > 0:
            ch = src[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        if depth == 0:
            yield m.group(1), src[m.end():pos - 1]


def _parse_enum_variants(body):
    out = []
    for raw in core.split_top(body):
        item = re.sub(r"#\[[^\]]*\]", "", raw)
        item = item.split("(")[0].split("{")[0].split("=")[0].strip()
        if item and re.match(r"^[A-Za-z_]\w*$", item):
            out.append(item)
    return out[:12]


def _strip_rs_literals_and_inline_comments(src):
    return "\n".join(re.sub(r"//.*$", "", _blank_literals(line)) for line in src.splitlines())


def _code_without_tests_and_comments(src, suffix):
    lines = src.splitlines()
    if suffix == ".rs":
        test_from = core.rust_test_start(lines)
        if test_from:
            lines = lines[:test_from - 1]
    return "\n".join(l for l in lines if core.comment_of(l, suffix) is None)


def discover_states(targets, root=None):
    """Inventories UI states (enums, routes, modal/empty/bool branches, env hooks, and optional state manifest)."""
    base = Path(root or core.REPO).resolve()
    paths = _expand_targets(targets) if targets else [p for _, p, _ in repo_ui_files(base)]
    seen = set()
    file_cache = {}
    scan_list = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen and rp.is_file():
            seen.add(rp)
            scan_list.append(rp)
        for _, pp, *_ in _screen_peer_paths(rp, file_cache):
            prp = pp.resolve()
            if prp not in seen:
                seen.add(prp)
                scan_list.append(prp)
        if rp.parent.name.lower() not in GENERIC_UI_DIRS:
            for sib_name in STATE_SIBLING_NAMES:
                sib = (rp.parent / sib_name).resolve()
                if sib.is_file() and sib not in seen and sib.stat().st_size <= core.MAX_BYTES:
                    seen.add(sib)
                    scan_list.append(sib)

    sources = []
    all_enums, struct_fields = {}, set()
    enums, routes, env_hooks, branch_states = {}, [], set(), set()
    screen_hints = {p.stem.lower() for p in scan_list} | {p.parent.name.lower() for p in scan_list}
    for p in scan_list:
        rel = core.relative(p)
        if core.is_test(rel) or p.suffix.lower() in STYLE_EXT or p.stat().st_size > core.MAX_BYTES:
            continue
        raw_src = p.read_text(encoding="utf-8", errors="replace")
        suffix = p.suffix.lower()
        src = _code_without_tests_and_comments(raw_src, suffix)
        sources.append((p, suffix, raw_src, src))
        if suffix == ".rs":
            for ename, body in _extract_braced_enums(src):
                clean = _parse_enum_variants(body)
                if clean:
                    all_enums[ename] = clean
                    if STATE_ENUM_NAME_RE.search(ename):
                        enums[ename] = clean
            for sbody in RS_STRUCT_BLOCK_RE.findall(src):
                for fname, ftype in RS_STATE_FIELD_RE.findall(sbody):
                    if fname not in SKIP_STATE_FIELDS and ftype not in STD_STATE_ENUM_SKIP:
                        struct_fields.add(fname)
        else:
            for ename, body in _extract_braced_enums(src):
                if STATE_ENUM_NAME_RE.search(ename):
                    clean = _parse_enum_variants(body)
                    if clean:
                        enums[ename] = clean
        for tname, rhs in TS_UNION_RE.findall(src):
            opts = re.findall(r"""['"]([A-Za-z0-9_-]+)['"]""", rhs)
            if opts:
                enums[tname] = opts[:12]

    for _, suffix, raw_src, src in sources:
        for m in ROUTE_KEY_RE.finditer(src):
            routes.append(m.group(1) or m.group(2))
        for m in ENV_HOOK_RE.finditer(raw_src):
            env_hooks.add(m.group(1) or m.group(2))
        for m in COND_STATE_RE.finditer(src):
            branch_states.add(m.group(1).lower())
        if suffix != ".rs":
            continue
        rs_code = _strip_rs_literals_and_inline_comments(src)
        cond_exprs = [
            m.group(1) or m.group(2) or m.group(3) or ""
            for m in RS_COND_EXPR_RE.finditer(rs_code)
        ]
        src_lines = rs_code.splitlines()
        for lineno, line in enumerate(src_lines, 1):
            for wm in RS_WHEN_CALL_RE.finditer(line):
                span, _ = copy_rules.call_span(src_lines, lineno, wm.end(), limit=6)
                args = core.split_top(span.replace("\n", " "))
                if args:
                    cond_exprs.append(args[0])
        for expr in cond_exprs:
            for ename, variant in RS_ENUM_VARIANT_RE.findall(expr):
                if ename in STD_STATE_ENUM_SKIP:
                    continue
                if ename in all_enums:
                    enums.setdefault(ename, list(all_enums[ename]))
                else:
                    cur = enums.setdefault(ename, [])
                    if variant not in cur and len(cur) < 12:
                        cur.append(variant)
            empty_fields = set()
            for fname in RS_IS_EMPTY_RE.findall(expr):
                if fname in struct_fields or fname not in GENERIC_EMPTY_SKIP:
                    branch_states.add(f"{fname}.is_empty()")
                    empty_fields.add(fname)
            for fm in RS_SELF_FIELD_RE.finditer(expr):
                fname = fm.group(1)
                tail = expr[fm.end():]
                if fname in SKIP_STATE_FIELDS or fname in empty_fields or tail.startswith(".is_empty()"):
                    continue
                branch_states.add(fname)
            if struct_fields:
                for tok in re.findall(r"\b([a-z_][a-z0-9_]{2,})\b", expr):
                    if tok in struct_fields and tok not in SKIP_STATE_FIELDS and tok not in empty_fields:
                        branch_states.add(tok)

    manifest = []
    states_rel = core.C.design.get("states_file")
    if states_rel:
        sfile = (base / states_rel).resolve()
        if sfile.is_file():
            try:
                raw = json.loads(sfile.read_text(encoding="utf-8", errors="replace"))
                for entry in raw.get("states") or []:
                    if not isinstance(entry, dict) or not entry.get("id"):
                        continue
                    scr = str(entry.get("screen") or "").lower()
                    if not scr or scr in screen_hints or not targets:
                        manifest.append({"id": entry["id"], "label": entry.get("label") or entry["id"], "screen": entry.get("screen") or ""})
            except (OSError, ValueError):
                pass
    return {
        "enums": enums,
        "routes": list(dict.fromkeys(routes))[:20],
        "branches": sorted(branch_states),
        "env_hooks": sorted(env_hooks),
        "manifest": manifest[:20],
        "capture_command": core.C.design.get("capture_command") or "",
    }


def _format_states(info):
    lines = []
    for name, variants in sorted(info["enums"].items()):
        lines.append(f"state enum {name}: {', '.join(variants)}")
    if info["routes"]:
        lines.append("routes/keys: " + ", ".join(info["routes"]))
    if info["branches"]:
        lines.append("conditional states: " + ", ".join(info["branches"]))
    if info["env_hooks"]:
        lines.append("env preview hooks: " + ", ".join(info["env_hooks"]))
    if info["manifest"]:
        lines.append("declared states: " + ", ".join(f"{s['id']} ({s['label']})" for s in info["manifest"]))
    if info["capture_command"]:
        lines.append("configured capture_command (run only when authorized): " + info["capture_command"])
    return "\n".join(lines) if lines else "No explicit UI states or modals detected."


def _expand_targets(targets):
    out = []
    for raw in targets:
        path = Path(raw).resolve()
        if path.is_dir():
            out += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SCAN_EXT)
        else:
            out.append(path)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--find", help="locate UI files matching screen name, symbol or title")
    parser.add_argument("--states", action="store_true", help="list interactive states, enums, modals, and hooks")
    parser.add_argument("--check-image", help="verify PNG screenshot is non-blank and measure visual density")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE_PNG", "AFTER_PNG"),
                        help="compare before/after UI PNG captures by region and visual density")
    parser.add_argument("--dpr", type=float, default=1.0,
                        help="device pixel ratio for --check-image/--compare pixel thresholds (default 1.0)")
    parser.add_argument("--viewport-height", type=int, default=None,
                        help="viewport fold height in capture px for long full-page captures")
    parser.add_argument("--diff", action="store_true", help="changed UI lines against HEAD")
    parser.add_argument("--all", action="store_true", help="scan the whole file, not only changed lines")
    parser.add_argument("--only", help="comma-separated checks: " + ", ".join(sorted(CHECKS)))
    parser.add_argument("--accept", help="finding line (or '-' to read from stdin) that is not a defect")
    parser.add_argument("--reason", default="", help="why the accepted finding is kept")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.accept:
        return core.accept(args.accept, args.reason, tool="design_audit.py")
    if args.selftest:
        return selftest()
    if args.check_image:
        info = visual_check.inspect_image(args.check_image, dpr=args.dpr, viewport_h=args.viewport_height)
        print(visual_check.format_inspect(args.check_image, info))
        return 0 if info["ok"] else 1
    if args.compare:
        res = visual_check.compare_images(
            args.compare[0], args.compare[1], dpr=args.dpr, viewport_h=args.viewport_height
        )
        print(visual_check.format_compare(res))
        return 0 if res["ok"] else 1
    if args.states:
        print(_format_states(discover_states(args.files)))
        return 0
    if args.find:
        hits = find_screens(args.find)
        if not hits:
            print(f"No UI screen matched {args.find!r}.")
            return 1
        for score, rel, siblings in hits:
            extra = f" (siblings: {', '.join(siblings)})" if siblings else ""
            print(f"[{score}] {rel}{extra}")
        return 0

    wanted = CHECKS
    if args.only:
        wanted = {c.strip().lower() for c in args.only.split(",")} & CHECKS
        if not wanted:
            print(f"--only takes: {', '.join(sorted(CHECKS))}")
            return 2

    tokens = discover_project_tokens()
    targets = core.changed_files() if args.diff else args.files
    paths = _expand_targets(targets) if targets else [p for _, p, _ in repo_ui_files()]
    cache = {}
    found = []
    for path in paths:
        only = None if args.all else core.added_lines(path)
        if only is not None and not only:
            continue
        found += scan_file(path, only=only, tokens=tokens, checks=wanted, cache=cache)
    if args.all and not args.diff and wanted == CHECKS:
        core.prune_paths(
            paths,
            lambda p: raw_scan_file(p, only=None, tokens=tokens, checks=CHECKS, cache=cache),
            "design_audit.py",
        )
    for f in found:
        print(f)
    print(f"{len(found)} design finding(s).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
