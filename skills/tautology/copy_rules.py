"""UI copy and prose: pleonasm, text that repeats its neighbour, tooltip, oversized text, too many
explanations on one screen, comment that restates the code."""
import re
from collections import defaultdict
from pathlib import Path

import core
from core import *  # noqa: F403


def text_checks(path, number, text, out):
    for m in C.pleonasm.finditer(text):
        out.append(Finding(path, number, "copy", f'pleonasm "{m.group(0)}"', "cut the part that repeats"))
    for m in DOUBLED_RE.finditer(text):
        if m.group(1).lower() not in C.doubled_ok:
            out.append(Finding(path, number, "copy", f'doubled word "{m.group(0)}"', "delete the repeated one"))


def scan_prose(rel, lines, look, out):
    sentences = defaultdict(list)
    fence = False
    for number, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence or not look(number):
            continue
        text_checks(rel, number, line, out)
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            norm = plain(sentence)
            if len(norm.split()) >= 7:
                sentences[norm].append(number)
    for norm, numbers in sentences.items():
        if len(numbers) >= 2:
            out.append(Finding(rel, numbers[1], "copy", f'repeated sentence (lines {", ".join(map(str, numbers[:4]))})',
                               "say it once, where it is read first"))


def check_comment_restates_code(rel, number, comment, lines, suffix, out):
    said = concepts(re.sub(r"`[^`]*`", " ", comment))  # a quoted name cites the origin, it does not describe
    if len(said) < 2 or len(said) > 8:
        return
    for following in lines[number:number + 3]:
        if following.strip() and comment_of(following, suffix) is None:
            code = code_concepts(strip_literals(following))
            hits = len(said & code)
            if hits / len(said) >= 0.8:
                out.append(Finding(rel, number, "code", f'comment restates the code: "{comment[:60]}"',
                                   "delete it, or say the why the code does not show"))
            return


PLACEHOLDER = re.compile(r"\{[^}]*\}|\d+")
BRANCH = re.compile(r"=>|\b(else|elif|case|except|return|if)\b|^\s*(Some|None|Ok|Err)\b")
TABLE_EDGE = re.compile(r"\b(const|static|let)\b|^\s*[\]}]\s*[;,)]?\s*$")
NAMED = re.compile(r"[“”\"'«»@\x60]")
# Calls that only wrap the text: two .child("...") in a row are title and subtitle, not siblings.
GENERIC_CALLS = {"child", "into", "format!", "from", "to_owned", "to_string", "Some", "Ok", "Err", "new",
                 "push", "push_str", "concat!", "tr", "text", "setText"}


def alternatives(lines, n1, n2):
    """Texts that never show together: match/if arms, or different lists."""
    return any(BRANCH.search(line) or TABLE_EDGE.search(line) for line in lines[n1:n2])


def table_row(line, suffix):
    return len(literals(line, suffix)) >= 2  # table row: (label, section, group)


def new_information(short, long):
    """What the longer text adds that is not filler: negation, condition, number, name, place."""
    ps, pl = plain(short), plain(long)
    if bool(C.negation.search(ps)) != bool(C.negation.search(pl)):
        return "negation"
    if C.condition.search(pl) and not C.condition.search(ps):
        return "condition"
    if set(re.findall(r"\d+|\{[^}]*\}", long)) - set(re.findall(r"\d+|\{[^}]*\}", short)):
        return "number"
    if NAMED.search(long) and not NAMED.search(short):
        return "name"
    capitals = set(re.findall(r"(?<=\s)[A-Z][\w.]+", long)) - set(short.split())
    if capitals:
        return "name"
    return None


def ui_concepts(text):
    """Like concepts(), but keeps 2-letter acronyms (id, ok): "Client id" and "Client key" differ."""
    return {concept(w) for w in plain(PLACEHOLDER.sub(" ", text)).split() if len(w) >= 2 and w not in C.stop}


# Words agents wrap around a label or title without adding anything: "Enter your email address" is
# "Email", "Manage your profile settings below" is "Profile settings". Removed before comparing.
# tautology: off (the list quotes what the hunt looks for)
FILLER = re.compile(r"(?i)\b(?:enter|type|insert|provide|select|choose|manage|update|edit|view|configure|"
                    r"tell us about)\s+(?:your|the|a)\b|\b(?:below|above|here|up to date|at any time)\b|"
                    r"\b(digite|informe|insira|preencha|escolha|selecione|gerencie|atualize|edite|configure)\s+"
                    r"(?:o|a|os|as|seu|sua|seus|suas)\b|\b(?:abaixo|acima|aqui|a qualquer momento)\b|"
                    r"\b(e-?mail|phone)\s+(?:address|number)\b|\b(?:endere[cç]o de|n[uú]mero de)\s+(e-?mail|telefone)\b")
# tautology: on


def core_concepts(text):
    """The concepts of a UI text once the filler around it is gone."""
    return ui_concepts(FILLER.sub(lambda m: " ".join(g for g in m.groups() if g) or " ", text))


def callee(line, literal):
    """Name of the call that receives the literal: group("A", ...) -> group."""
    start = line.find(literal)
    depth = 0
    for index in range(start - 1, -1, -1):
        char = line[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                m = re.search(r"([\w!]+)\s*$", line[:index])
                return m.group(1) if m else ""
            depth -= 1
    return ""


def siblings(l1, t1, l2, t2):
    """The same function called on both lines (two setting rows, two groups): different fields."""
    c1, c2 = callee(l1, t1), callee(l2, t2)
    return bool(c1) and c1 == c2 and c1 not in GENERIC_CALLS


LABEL_LINE = re.compile(r"<label\b|<(?:Label|FormLabel)\b|\bQLabel\(|\.label\(|\blabel\s*=")
PLACEHOLDER_ATTR = re.compile(r"(?i)\bplaceholder(?:Text)?\s*[=:(]|setPlaceholderText\(")
SINK = re.compile(r"=\s*(?:format!\()?\s*[\"']|\bset_\w+\(|\bmessage\b")


def propagated(l1, l2):
    """The same state written to two models (self.message = ...; detail.set_message(...))."""
    rendered = re.compile(r"child\(|hint\(|label\(|button")
    return all(SINK.search(l) and not rendered.search(l) for l in (l1, l2))


def near_repeats(rel, ui_literals, out, lines=(), whole=False, repeated=()):
    """Neighbouring UI texts (6 lines) or substantial screen-wide texts that say the same:
    title vs subtitle, label vs hint, modal subtitle vs input placeholder/hint."""
    reported = set()  # a text already reported as a repeat goes; it is no anchor for the next one
    for i, (n1, t1, l1) in enumerate(ui_literals):
        for n2, t2, l2 in ui_literals[i + 1:]:
            if n2 == n1 or (n1, t1) in reported or (n2, t2) in reported:
                continue  # variants on one line (singular/plural, $/%) never show together
            far = n2 - n1 > 6
            a, b = core_concepts(t1), core_concepts(t2)
            suffix = Path(rel).suffix
            # A one-word label and the placeholder of its field ("Email" / "Enter your email address").
            labelled = n2 - n1 <= 2 and any(PLACEHOLDER_ATTR.search(l) for l in (l1, l2))
            min_concepts = 4 if far else 1 if labelled else 2
            if len(a) < min_concepts or len(b) < min_concepts:
                continue
            if not far and lines and alternatives(lines, n1, n2):
                continue
            if far and lines and render_owner(lines, n1) == render_owner(lines, n2) and alternatives(lines, n1, n2):
                continue
            if (table_row(l1, suffix) and table_row(l2, suffix)) or siblings(l1, t1, l2, t2):
                continue
            if plain(t1) == plain(t2):
                if far or propagated(l1, l2):
                    continue
                if plain(t1) in repeated:
                    continue  # the whole-file scan already reports it as "the same text appears Nx"
                reported.add((n2, t2))
                out.append(Finding(rel, n2, "copy", f'"{t2[:50]}" repeats the text on line {n1}',
                                   "keep one, where the eye lands first"))
            elif ((a <= b or b <= a) and len(a ^ b) <= (2 if far else 3)) or (
                    far and len(a & b) >= 5 and len(a & b) / min(len(a), len(b)) >= 0.65):
                same_words = {stem(w) for w in words(PLACEHOLDER.sub(" ", t1))} == {stem(w) for w in words(PLACEHOLDER.sub(" ", t2))}
                if same_words:
                    continue  # singular/plural or another number in the same notice
                short, long = (t1, t2) if len(a) <= len(b) else (t2, t1)
                if new_information(t1, t2) if far else new_information(short, long):
                    continue  # the addition is information (where, when, how much, the opposite)
                reported.add((n2, t2))
                out.append(Finding(rel, n2, "copy", f'"{t2[:50]}" says the same as "{t1[:40]}" (line {n1})',
                                   "cut the repeat"))


def new_ui_texts(path, only=None, keep_accepted=False):
    """New UI texts with their neighbours (6 lines): the agent checks the paraphrase the word
    comparison misses. Returns [(rel, line, text, [(line, neighbour text)])]."""
    rel = relative(path)
    suffix = path.suffix.lower()
    if suffix not in {".py", ".rs"} | JS or not path.is_file() or path.stat().st_size > MAX_BYTES:
        return []
    if own_cases(path, rel):
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    if is_test(rel) or not is_ui(rel, source):
        return []
    lines = jsx_lines(source.splitlines()) if suffix in JSX else source.splitlines()
    skip = ignored_lines(lines)
    test_from = rust_test_start(lines) if suffix == ".rs" else None
    texts = []
    for number, line in enumerate(lines, 1):
        if number in skip or (test_from and number >= test_from) or comment_of(line, suffix) is not None:
            continue
        texts += [(number, t) for t in literals(line, suffix) if is_prose(t) and on_screen(line, t)]
    items = []
    for number, text in texts:
        if only is None or number in only:
            if not keep_accepted and accepted(Finding(rel, number, "copy", f'new UI text "{text}"', "")):
                continue
            near = [(n, t) for n, t in texts if abs(n - number) <= 6 and (n, t) != (number, text)]
            items.append((rel, number, text, near))
    return items


# ------------------------------------------------------------------------ visual bloat
# Too much text on screen is tautology too: the control, the icon and the context already say it.
def text_kind(lines, number, line, suffix=".rs"):
    """The text's kind, by the call that receives it: on the line itself or on the one that opens .child(...)."""
    for kind, pattern in C.kinds:
        if pattern.search(strip_literals(line)) or (kind == "hint" and C.hint_raw.search(line)):
            return kind
    if re.match(r'\s*\.child\(\s*"|\s*\{?"', line):
        # Climb to the receiver or enclosing call (a less indented line), or check a tuple key right above.
        if number >= 2 and C.hint_raw.search(lines[number - 2]):
            return "hint"
        indent = len(line) - len(line.lstrip())
        for back in range(number - 2, max(-1, number - 10), -1):
            above = lines[back]
            depth = len(above) - len(above.lstrip())
            if not above.strip() or depth >= indent:
                continue
            for kind, pattern in C.kinds:
                if pattern.search(strip_literals(above)) or (kind == "hint" and C.hint_raw.search(above)):
                    if kind == "title":
                        earlier = any(literals(lines[i], suffix) for i in range(back, number - 1))
                        if earlier:
                            return "hint"  # secondary string argument in a multi-line call (e.g. chip tooltip)
                    return kind
            if re.search(r"[(,{]\s*$", above) and not re.search(r"\b(?:div|h_flex|v_flex)\s*\(\s*$", above):
                indent = depth
                continue  # multi-line call or argument wrapper (Some((, if ... {): keep climbing
            if not re.fullmatch(r"\s*\.\w+\(\s*", above) and not re.match(r"\s*<\w", above):
                break  # receiver without a kind (div(), h_flex()...): plain text
            if re.match(r"\s*<\w", above):
                break  # JSX tag without a kind (<div>, <p>): plain text
            indent = depth
    return "text"


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.split()) >= 2]


def render_owner(lines, number):
    """The function that holds the line (Rust fn, Python def, JS function or component): one screen."""
    for back in range(number - 1, -1, -1):
        m = (re.match(r"\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|def)\s+(\w+)", lines[back])
             or re.match(r"\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)", lines[back])
             or re.match(r"\s*(?:export\s+)?(?:const|let)\s+([A-Z]\w*)\s*(?::[^=]+)?=\s*(?:\([^)]*\)|\w+)\s*=>", lines[back]))
        if m:
            return back + 1, m.group(1)
    return 0, "<file>"


def bloat_checks(rel, lines, suffix, look, skip, test_from, out, full=True):
    """full=False (the default Stop hook): only text that explains the obvious; no length, wordiness,
    tooltip size or density, which need judgement and cost turns."""
    explaining = defaultdict(list)  # (fn line, name) -> [lines of explanatory text]
    sensitive = bool(C.sensitive_paths) and any(p in rel for p in C.sensitive_paths)
    for number, line in enumerate(lines, 1):
        if number in skip or (test_from and number >= test_from) or comment_of(line, suffix) is not None:
            continue
        for literal in literals(line, suffix):
            if not is_prose(literal) or not on_screen(line, literal):
                continue
            kind = text_kind(lines, number, line, suffix)
            body = PLACEHOLDER.sub("X", literal)
            if kind == "title" and (literals(line, suffix).index(literal) > 0 or len(sentences(body)) > 1
                                    or (body.rstrip().endswith(".") and len(body.split()) >= 4)):
                kind = "hint"  # the second text of row(label, description) is the description
            # Density: only what stays on screen at once (not a match arm, not a built message).
            placed = C.placed.search(line) and "=>" not in line
            if placed and kind in ("tooltip", "hint", "text") and len(body.split()) >= C.descriptive:
                explaining[render_owner(lines, number)].append((number, literal))
            if not look(number):
                continue
            obvious = C.obvious.search(literal) if C.obvious else None
            if obvious and C.confirmation and C.confirmation.search(literal):
                obvious = None  # "Note: this cannot be undone" carries the warning, not a tip
            wordy = C.wordy.search(literal) if C.wordy else None
            if obvious:
                out.append(Finding(rel, number, "copy", f'"{obvious.group(0).strip()}" explains the obvious: '
                                   f'"{literal[:40]}"', "cut it; say only what the screen does not show"))
            if not full:
                continue
            if wordy and not obvious:
                out.append(Finding(rel, number, "copy", f'"{wordy.group(0).strip()}" is wordy: "{literal[:40]}"',
                                   'say it as an instruction ("Try again or go back"), without the detour'))
            if kind == "tooltip":
                continue  # tooltip size: measured whole, in balloon lines, by tooltip_checks
            words_max, chars_max, sentences_max = C.limits[kind]
            count, said = len(body.split()), sentences(body)
            if (C.confirmation and C.confirmation.search(literal)) or (said and said[0].rstrip().endswith("?")):
                sentences_max = max(sentences_max, 3)  # question, consequence and "cannot be undone"
                chars_max = max(chars_max, C.limits["text"][1])
                words_max = max(words_max, C.limits["text"][0])
            if sensitive and C.security and C.security.search(literal):
                pass  # payment or legal guarantee: shorten without losing it; judged in the new-texts list
            elif count > words_max or len(body) > chars_max:
                out.append(Finding(rel, number, "copy", f"{kind} with {count} words / {len(body)} characters "
                                   f'(limit {words_max} / {chars_max}): "{literal[:40]}"',
                                   "cut to the essential; the control or the screen already shows the rest"))
            elif len(said) > sentences_max:
                out.append(Finding(rel, number, "copy", f'{kind} with {len(said)} sentences (limit {sentences_max}): '
                                   f'"{literal[:40]}"', "keep the sentence the user needs"))
            if kind == "title" and len(said) == 1 and literal.rstrip().endswith(".") and count <= words_max:
                out.append(Finding(rel, number, "copy", f'title ends with a period: "{literal[:40]}"', "drop the period"))
    if not full:
        return
    tooltip_checks(rel, lines, suffix, look, skip, test_from, out)
    uses_tooltips = bool(C.raw.get("tooltip_calls")) or any(
        C.tooltip_call.search(l) or re.search(r"data-tooltip|\btitle=", l) for l in lines
    )
    for (start, name), entries in explaining.items():
        numbers = [n for n, _ in entries]
        if start and len(numbers) > C.density and any(look(n) for n in numbers):
            items = "; ".join(f'L{n} "{t[:28]}"' for n, t in entries[:5])
            action = (
                f"list the cluttering elements ({items}), then eliminate them or move them to a tooltip"
                if uses_tooltips
                else f"list the cluttering elements ({items}), then eliminate them or shorten to a label"
            )
            out.append(Finding(rel, start, "copy", f"{name}: {len(numbers)} explanatory texts on one screen "
                               f"(limit {C.density}; lines {', '.join(map(str, numbers[:6]))})", action))


# Tooltip: measured whole in balloon lines (tooltip_line_chars per line). More than tooltip_lines
# is a wall of text; the target is one line, one sentence, only what the control does not show.
def call_span(lines, number, col, limit=25):
    """Code of the call that opens at lines[number - 1][col - 1] == "(" until it closes: (text, last line)."""
    depth, parts = 0, []
    for index in range(number - 1, min(len(lines), number - 1 + limit)):
        line = lines[index][col:] if index == number - 1 else lines[index]
        for position, char in enumerate(line):
            depth += {"(": 1, ")": -1}.get(char, 0)
            if depth < 0:
                parts.append(line[:position])
                return "\n".join(parts), index + 1
        parts.append(line)
    return "\n".join(parts), number


def expression_of(lines, number, name):
    """Value of "let name = ..." or "const NAME: &str = ..." before the line (up to 40 above) or in the file."""
    pattern = re.compile(rf"\b(?:let|const|static)\s+(?:mut\s+)?{re.escape(name)}\b[^=]*=\s*")
    order = list(range(number - 2, max(-1, number - 42), -1)) + list(range(len(lines)))
    for index in order:
        m = pattern.search(lines[index])
        if m:
            body, end = [], index
            for later in range(index, min(len(lines), index + 25)):
                piece = lines[later][m.end():] if later == index else lines[later]
                body.append(piece)
                if piece.rstrip().endswith(";"):
                    break
            return "\n".join(body), index + 1, later + 1
    return None, None, None


def text_of(code):
    """The text the user reads: the expression's literals joined (concat!, line continuation, format!)."""
    code = re.sub(r"\\\n\s*", "", code)  # Rust "abc \ <newline> def" is one line
    first = RS_STR.findall(code)
    if not first:
        return ""
    if re.search(r"\belse\b|=>", code):
        return PLACEHOLDER.sub("XXXXXX", max(first, key=len))  # if/match: each arm is one tooltip
    if re.search(r"\bformat!\(", code):
        first = first[:1]  # the rest are format! arguments
    return PLACEHOLDER.sub("XXXXXX", "".join(first))


TOOLTIP_JS = re.compile(r"""(?:\btitle|data-tooltip|\btooltip|\bcontent)\s*=\s*\{?\s*(["'\x60])((?:(?!\1).)+)\1|"""
                        r"""\.title\s*=\s*(["'\x60])((?:(?!\3).)+)\3""")


def tooltip_rows(text):
    return sum(max(1, -(-len(part) // C.line_chars)) for part in text.split("\\n"))


def tooltip_finding(rel, number, text, out):
    rows, said = tooltip_rows(text), sentences(text.replace("\\n", " "))
    if rows > C.tooltip_lines or len(said) > 1:
        out.append(Finding(rel, number, "copy",
                           f'tooltip of ~{rows} lines in the balloon ({len(text)} characters, {len(said)} sentence(s)): '
                           f'"{text[:40]}"',
                           f"shorten to the minimum: one sentence of up to ~{C.line_chars} characters (1 line), "
                           "only what the control does not show; the rest goes"))


def tooltip_checks(rel, lines, suffix, look, skip, test_from, out):
    if suffix in JS:
        for number, line in enumerate(lines, 1):
            if number in skip or not look(number) or comment_of(line, suffix) is not None:
                continue
            for m in TOOLTIP_JS.finditer(line):
                if "content" in m.group(0)[:8] and "Tooltip" not in line:
                    continue  # content= is a tooltip only inside <Tooltip>
                text = re.sub(r"\$\{[^}]*\}", "X", m.group(2) or m.group(4))
                if is_prose(text):
                    tooltip_finding(rel, number, text, out)
        return
    if suffix != ".rs":
        return
    for number, line in enumerate(lines, 1):
        if number in skip or (test_from and number >= test_from) or comment_of(line, suffix) is not None:
            continue
        for call in C.tooltip_call.finditer(line):
            code, last = call_span(lines, number, call.end())
            args = split_top(code.replace("\n", " "))
            if not args:
                continue
            second = any(name in call.group(0) for name in C.tooltip_second)
            arg = args[1] if second and len(args) > 1 else args[0]
            touched = set(range(number, last + 1))
            name = re.fullmatch(r"\s*&?([A-Za-z_]\w*)(?:\.clone\(\)|\.to_owned\(\)|\.into\(\))?\s*", arg)
            if name:
                code, start, end = expression_of(lines, number, name.group(1))
                if not code:
                    continue
                touched |= set(range(start, end + 1))
            else:
                code = arg
            text = text_of(code)
            if not text or not any(look(n) for n in touched):
                continue
            tooltip_finding(rel, number, text, out)


# ------------------------------------------------------------------------- AI visual tics
# Agents put a helper line under every field and an emoji in every heading. One finding per screen,
# raised only when the session's own lines add to it, so an unrelated edit never pays for it.
FIELD_RE = re.compile(r"<(?:input|textarea|select|Input|TextField|TextArea|Textarea|Select|NumberInput|"
                      r"PasswordInput|DatePicker|Combobox)\b|\b(?:QLineEdit|QTextEdit|QPlainTextEdit|QComboBox|"
                      r"QSpinBox)\(|\btext_input\(|\bTextInput::new\(")
HELPER_ATTR = re.compile(r"\b(?:helperText|description|hint|helpText|caption)\s*=\s*[{\"']")
HELPER_TAG = re.compile(r"<(?:small|FormDescription|FormHelperText|HelperText|Description|Hint)\b|"
                        r"<(?:p|span)\b[^>]*(?:help|hint|muted|description|caption)")
ROW_CALL = re.compile(r"\b\w*(?:row|field|setting|option)\w*\(\s*\"[^\"]{1,40}\"\s*(?:,\s*\"([^\"]{12,})\")?")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-⛿✨⭐⭕‼⁉❗❓➡]")


def _helper_after(lines, number, suffix):
    """Line of the helper text a field carries: in its own attributes, or in the next 2 code lines."""
    if HELPER_ATTR.search(lines[number - 1]):
        return number
    seen = 0
    for index in range(number, min(len(lines), number + 6)):
        line = lines[index]
        if not line.strip() or comment_of(line, suffix) is not None:
            continue
        if FIELD_RE.search(line) or re.match(r"\s*<label\b", line):
            return None  # the next field begins: this one has no helper
        if HELPER_TAG.search(line) and any(is_prose(t) for t in literals(line, suffix)):
            return index + 1
        seen += 1
        if seen >= 2:
            return None
    return None


def clutter_checks(rel, lines, suffix, look, skip, test_from, out):
    """Screen-level AI tics: a helper text under (almost) every field, decorative emoji in UI text."""
    fields, helpers, emoji = defaultdict(list), defaultdict(list), defaultdict(list)
    for number, line in enumerate(lines, 1):
        if number in skip or (test_from and number >= test_from) or comment_of(line, suffix) is not None:
            continue
        owner = render_owner(lines, number)
        if FIELD_RE.search(line):
            fields[owner].append(number)
            helper = _helper_after(lines, number, suffix)
            if helper:
                helpers[owner].append(helper)
        else:
            row = ROW_CALL.search(line)  # GPUI/Qt: option_row("Sound", "Plays a short sound when...")
            if row:
                fields[owner].append(number)
                if row.group(1) and is_prose(row.group(1)):
                    helpers[owner].append(number)
        for literal in literals(line, suffix):
            if EMOJI_RE.search(literal) and (suffix in JSX or on_screen(line, literal)):
                emoji[owner].append(number)
                break
    for (start, name), found in helpers.items():
        total = len(fields[(start, name)])
        if total >= 3 and len(found) >= max(3, -(-3 * total // 4)) and any(look(n) for n in found):
            out.append(Finding(rel, found[0], "copy", f"{name}: {len(found)} of {total} fields carry a helper text "
                               f"(lines {', '.join(map(str, found[:5]))})",
                               "keep only the helper that says what the label and placeholder do not; cut the rest"))
    limit = C.raw.get("max_emojis", 0)
    for (start, name), found in emoji.items():
        if len(found) > limit and any(look(n) for n in found):
            out.append(Finding(rel, found[0], "copy", f"{name}: decorative emoji in {len(found)} UI texts "
                               f"(lines {', '.join(map(str, found[:5]))})",
                               "drop them; where an icon carries meaning, use the project's icon component"))

    tooltip_everywhere(rel, lines, suffix, look, skip, test_from, out)
    slop_badges(rel, lines, suffix, look, skip, test_from, out)
    footnotes(rel, lines, suffix, look, skip, test_from, out)
    if suffix in JSX:
        nested_cards(rel, lines, look, skip, out)


def _code_lines(lines, suffix, skip, test_from):
    for number, line in enumerate(lines, 1):
        if number in skip or (test_from and number >= test_from) or comment_of(line, suffix) is not None:
            continue
        yield number, line


# An info icon or tooltip on every toggle and field: the label stops being enough because every row
# explains itself. Icon-only buttons keep their tooltip; it is their only name.
CONTROL_RE = re.compile(FIELD_RE.pattern + r"|<(?:Switch|Toggle|Checkbox|Slider|RadioGroup)\b|type=[\"']checkbox|"
                        r"\b\w*(?:switch|toggle|check_box|checkbox|slider|dropdown|select)\s*\(|"
                        r"\bQ(?:CheckBox|Slider|RadioButton)\(")
# The GPUI row form spreads `Some((` and its `"x-info"` id over two lines.
TIP_RE = re.compile(r"<(?:Tooltip|InfoTip|InfoTooltip|HelpTooltip|HelpIcon|InfoIcon|HoverCard)\b|\bdata-tooltip\b|"
                    r"\b(?:info_hint|info_tip|help_tip|help_icon)\s*\(|\.tooltip\(|setToolTip\(|"
                    r"Some\(\(\s*\"[\w-]*(?:info|help|tip)\"|^\s*\"[\w-]*-(?:info|help|tip)\",")
INFO_TIP = re.compile(r"<(?:InfoTip|InfoTooltip|HelpTooltip|HelpIcon|InfoIcon)\b|\b(?:info_hint|info_tip|help_tip|help_icon)"
                      r"\s*\(|Some\(\(\s*\"[\w-]*(?:info|help|tip)\"|^\s*\"[\w-]*-(?:info|help|tip)\",")
ICON_BUTTON = re.compile(r"IconButton|\w*icon_(?:button|btn)\w*\(|close_button\(|size=[\"{]?[\"']?icon\b|aria-label=")
SHORTCUT = re.compile(r"\(\s*(?:Ctrl|Cmd|Alt|Shift|Esc|Enter|Tab|F\d{0,2}|\S)[^()\"]{0,8}\)\s*\"")


def tooltip_everywhere(rel, lines, suffix, look, skip, test_from, out):
    controls, tips = defaultdict(list), defaultdict(list)
    for number, line in _code_lines(lines, suffix, skip, test_from):
        owner = render_owner(lines, number)
        if CONTROL_RE.search(line) or ROW_CALL.search(line):
            controls[owner].append(number)
        tip = TIP_RE.search(line) or (C.tooltip_call and C.tooltip_call.search(line))
        if tip and not INFO_TIP.search(line) and not any(
                CONTROL_RE.search(l) for l in lines[max(0, number - 4):number + 3]):
            tip = None  # a tooltip on a button, price or chip is not a field explaining itself
        # A link's tooltip shows where it goes; an icon-only button's is its name.
        if tip and not re.search(r"\b[A-Z_]*URL\b|https?://|\bicon\w*\([^)]*\)\s*\.", line) and not SHORTCUT.search(line) and not any(
                ICON_BUTTON.search(l) for l in lines[max(0, number - 3):number + 3]):
            tips[owner].append(number)
    for (start, name), found in tips.items():
        # Info icons on section headings with no control nearby are not this tic.
        total = max(len(controls[(start, name)]), len(found))
        if len(controls[(start, name)]) >= 3 and len(found) >= 3 and len(found) >= -(-3 * total // 4) and any(look(n) for n in found):
            out.append(Finding(rel, found[0], "design", f"{name}: tooltip or info icon on {len(found)} of {total} "
                               f"controls (lines {', '.join(map(str, found[:5]))})",
                               "keep the tooltip only where the label cannot say it; a clear label needs none"))


# Badges on every heading and action, with the look agents reach for: a status dot, a gradient,
# a pulse, CAPS with tracking, an emoji or a hype word ("New", "Beta", "AI", "Pro").
BADGE_OPEN = re.compile(r"<(?:Badge|Chip|Tag|Pill)\b|\b(?:badge|chip|tag|pill|status_pill)\s*\(|"
                        r"<span\b[^>]*\brounded-full\b[^>]*\b(?:text-xs|text-\[1[01]px\])")
HYPE = re.compile(r"(?i)^\W*(?:new|novo|nova|beta|alpha|pro|ai|ia|hot|popular|recommended|recomendad[oa]|smart|"
                  r"premium|trending|exclusive|exclusivo|live|ao vivo|powered by \w+)\W*$")
SLOP_LOOK = re.compile(r"bg-gradient|linear-gradient|gradient\(|animate-(?:pulse|ping)|uppercase[^\"']*tracking-|"
                       r"tracking-\w+[^\"']*uppercase|^\s*[•●◉]|rounded-full\s+(?:h|w|size)-(?:1|1\.5|2)\b")


def slop_badges(rel, lines, suffix, look, skip, test_from, out):
    loud, styled = defaultdict(list), defaultdict(list)
    for number, line in _code_lines(lines, suffix, skip, test_from):
        if not BADGE_OPEN.search(line):
            continue
        owner = render_owner(lines, number)
        near = lines[number - 1:number + 2]
        texts = [t for l in near for t in literals(l, suffix)]
        if any(HYPE.match(t) or EMOJI_RE.search(t) for t in texts):
            loud[owner].append(number)
        elif any(SLOP_LOOK.search(l) for l in near) or any(SLOP_LOOK.search(t) for t in texts):
            styled[owner].append(number)
    for owner in set(loud) | set(styled):
        found = sorted(loud[owner] + styled[owner])
        if (loud[owner] or len(styled[owner]) >= 2) and any(look(n) for n in found):
            out.append(Finding(rel, found[0], "design", f"{owner[1]}: {len(found)} decorative badges "
                               f"(lines {', '.join(map(str, found[:5]))})",
                               "keep a badge only for a state the user acts on; no dot, gradient, pulse or hype word"))


# A note at the foot of a component or page ("Changes are saved automatically.", "Only in the
# installed app"): small, muted, after everything else. A site footer (<footer>, links, legal) stays.
FOOT_STYLE = re.compile(r"<small\b|\b(?:footnote|disclaimer|fine-?print)\b|variant=[\"']caption|"
                        r"\btext-(?:xs|\[1[0-2]px\])\b[^\"']*\b(?:muted|gray-[3-6]00|slate-[3-6]00|zinc-[3-6]00|"
                        r"neutral-[3-6]00|opacity-[2-7]0)|\b(?:muted|gray-[3-6]00|slate-[3-6]00|zinc-[3-6]00)\S*[^\"']*"
                        r"\btext-(?:xs|\[1[0-2]px\])\b|text_size\(px\([\w:]*(?:FOOTNOTE|CAPTION)\)\)|\.text_xs\(\)")
MUTED = re.compile(r"\.(?:muted|subtle|secondary|disabled)\b|muted|gray-[3-6]00|slate-[3-6]00|zinc-[3-6]00|opacity-")
MUTED_TUPLE = re.compile(r"(?<![\w>!])\(\s*\"([^\"]{12,})\"(?:\.into\(\)|\.to_string\(\)|\.to_owned\(\))?\s*,\s*\w+\.muted\s*\)")
SITE_FOOTER = re.compile(r"<footer\b|<Footer\b|role=[\"']contentinfo|\b(?:site_)?footer\s*\(")
LEGAL = re.compile(r"(?i)©|\(c\)|copyright|cnpj|cpf|direitos reservados|rights reserved|privacidade|privacy|termos|terms|"
                   r"cookies|ltda|inc\.|parcel|frete|shipping|\d")
CLOSER = re.compile(r"^\s*(?:</\w[\w.]*>\s*)+[)}]*[;,]?\s*$|^\s*[)}\]]+[;,]?\s*$")
CONTENT = re.compile(r"\.child(?:ren)?\(|<\w|\b\w*(?:button|row|field|input|switch|card|list)\w*\s*\(", re.I)
STATE = re.compile(r"^\s*(?:\}\s*else\s*)?if\b.*\{\s*$|\.when(?:_some)?\(|\.then\(|\{\s*\w[\w.!]*\s*&&\s*\(?\s*$|"
                   r"\?\s*\(?\s*$|spinner|progress|loading|skeleton", re.I)


def _in_site_footer(lines, number):
    for back in range(number - 1, max(-1, number - 60), -1):
        if re.search(r"</footer>|</Footer>", lines[back]):
            return False
        if SITE_FOOTER.search(lines[back]):
            return True
    return False


def _last_child(lines, number):
    """Nothing but closers follows the text until its container ends. In a builder chain
    (`.child(div()...child("note")),`) the first `)` only closes the text's own wrapper, so the
    container ends at a second `)` or at the end of the statement."""
    builder = ".child(" in lines[number - 1]
    closed = 0
    for line in lines[number:number + 6]:
        if not line.strip():
            continue
        if not CLOSER.match(line):
            break
        closed += line.count(")") + line.count("}") + 2 * line.count(";") if builder else 1
    return closed >= (2 if builder else 1)


def footnotes(rel, lines, suffix, look, skip, test_from, out):
    for number, line in _code_lines(lines, suffix, skip, test_from):
        if not look(number):
            continue
        tuple_note = MUTED_TUPLE.search(line)
        if tuple_note:
            text = tuple_note.group(1)
        else:
            texts = [t for t in literals(line, suffix) if is_prose(t) and len(t.split()) >= 3]
            if not texts or not _last_child(lines, number):
                continue
            chain = lines[max(0, number - 5):number]
            styled = FOOT_STYLE.search(line) or (
                any(FOOT_STYLE.search(l) for l in chain) and any(MUTED.search(l) for l in [*chain, line]))
            start = render_owner(lines, number)[0]
            if not styled or not any(CONTENT.search(l) for l in lines[max(start, number - 30):max(start, number - 5)]):
                continue  # nothing above it in this screen: a description under a title, not a footnote
            if any(FIELD_RE.search(l) or CONTROL_RE.search(l) for l in lines[max(0, number - 3):number - 1]):
                continue  # right under a field it is that field's helper, counted by clutter_checks
            if any(STATE.search(l) for l in lines[max(0, number - 7):number]):
                continue  # shown only in a state (empty, loading, read-only): a status, not a footnote
            text = texts[0]
        if LEGAL.search(text) or (C.security and C.security.search(text)) or _in_site_footer(lines, number):
            continue
        name = render_owner(lines, number)[1]
        out.append(Finding(rel, number, "design", f'{name}: footnote "{text[:48]}" under the content',
                           "cut it; if it matters, say it on the control it concerns (disabled state, label) "
                           "or once, when it happens"))


# A box with a border or a shadow is a card; a card inside a card inside a card is the
# "everything in a box" layout agents default to. Counted from the JSX tags themselves.
BOX_TAG = re.compile(r"<(/?)(Card|Paper|Panel|div|section|article|aside)\b([^>]*?)(/?)>|<(Card|Paper|Panel|div|section|"
                     r"article|aside)\b([^>]*)$")
LIST_ITEM = re.compile(r"\.(?:map|flatMap)\(|\bv-for=|\{#each\b")
CARD_CLASS = re.compile(r"\b(?:border|shadow(?:-\w+)?|card|ring)\b(?!-0\b)")


def nested_cards(rel, lines, look, skip, out):
    limit = C.design.get("max_card_nesting", 2)
    stack, deepest, where, touched = [], 0, 0, False  # stack: is each open box a card?
    for number, line in enumerate(lines, 1):
        if number in skip:
            continue
        for m in BOX_TAG.finditer(line):
            closing, name, attrs, selfclose = m.group(1), m.group(2) or m.group(5), m.group(3) or m.group(6) or "", m.group(4)
            if closing:
                if stack:
                    stack.pop()
                continue
            card = name in ("Card", "Paper", "Panel") or bool(CARD_CLASS.search(attrs))
            if card and any(LIST_ITEM.search(lines[i]) for i in range(max(0, number - 3), number)):
                card = False  # one card per item of a list is a list, not a box inside a box
            if selfclose:
                continue
            stack.append(card)
            depth = sum(stack)
            if card and depth > deepest:
                deepest, where = depth, number
            if card and depth > limit and look(number):
                touched = True
    if deepest > limit and touched:
        out.append(Finding(rel, where, "design", f"{deepest} cards nested inside each other (limit {limit})",
                           "keep one box; separate the inner groups with spacing and a heading, not another border"))
