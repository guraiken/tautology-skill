"""Shared core of the tautology hunt: project settings, findings, accepted findings, per-language
word lists, literal and comment reading per programming language, and changed-line helpers.
The rule modules (copy_rules, test_rules, answer_rules) and the modular skill build on it."""
import ast
import datetime
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


def find_repo():
    """The project root: CLAUDE_PROJECT_DIR, else the git top level of the working directory."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    return Path(top).resolve() if top else Path.cwd().resolve()


def project_subdir(repo, name):
    """Per-project settings folder: .agents/<name> when present, otherwise .claude/<name>."""
    agents = Path(repo) / ".agents" / name
    return agents if agents.is_dir() else Path(repo) / ".claude" / name


REPO = find_repo()
HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"                                   # the hunt's own test cases (--selftest)
PROJECT_DIR = project_subdir(REPO, "tautology")          # per-project settings and accepted findings
CONFIG_FILE = PROJECT_DIR / "config.json"
ACCEPTED_FILE = PROJECT_DIR / "accepted.txt"
SELFTEST = False

JS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
JSX = {".jsx", ".tsx"}
CODE = {".py", ".rs", ".ps1", ".bat", ".cmd", ".sh"} | JS
TEXT = {".md", ".txt"}
SKIP_DIRS = {"target", "__pycache__", ".venv", "venv", "node_modules", "fixtures", ".git"}
MAX_BYTES = 1_500_000
IGNORE_LINE = re.compile(r"tautology:\s*(example|ignore)")
IGNORE_BLOCK = re.compile(r"tautology:\s*(off|on)\b")
FIX_DIRECT = "call the original directly"
FIX_EXPECTED = "compare with the expected value"
SAME_CALL = "the expected value is the same call that produces the result"
SELF_WRITTEN = "checks the value the test itself wrote"

# ------------------------------------------------------------------------- languages
# Word lists per language. A project picks its languages in config.json ("languages"); the
# lists are joined, except words that mean something else in another active language.
# tautology: off (the lists quote what the hunt looks for)
LANG = {
    "en": {
        "stop": """a an the of to in on for with and or is are be was were this that it its by as at from your
            you we our not no yes if then so do does can will""",
        "pleonasms": ["free gift", "past history", "end result", "added bonus", "advance planning",
                      "repeat again", "return back", "revert back", "each and every", "completely full",
                      "new innovation", "close proximity", "final outcome"],
        "negation": ["not", "never", "without", "no"],
        "condition": ["if", "when", "unless", "until", "before", "after"],
        "obvious": [r"click here", r"this (screen|page|section) (shows|lets|lists)"],
        "wordy": [r"allows you to", r"you can "],
        "confirmation": [],
        "security": [],
        "doubled_ok": [],
        "endings": [],
    },
    "pt": {
        "stop": """a o os as um uma uns umas de do da dos das em no na nos nas num numa por pelo pela pelos
            pelas para pra pro com sem e ou que se ao aos à às é ser foi era está estão são sao esta este isto
            isso esse essa aquele aquela mais menos já ja não nao sim seu sua seus suas meu minha lhe ele ela
            eles elas eu você voce nós nos""",
        "pleonasms": [r"s[uo]b\w* para cima", r"desc\w* para baixo", r"entr\w* para dentro", r"sa[ií]\w* para fora",
                      r"volt\w* (para )?atrás", r"volt\w* para trás", r"repet\w* (de novo|novamente)", r"elo de ligação",
                      "certeza absoluta", "planejar antecipadamente", "prever antecipadamente", "acabamento final",
                      "outra alternativa", "surpresa inesperada", "consenso geral", "fato real", "brinde grátis",
                      "grátis de graça", "totalmente completo", "completamente cheio", "criar novo", "criar nova",
                      "manter o mesmo", r"há \d+ \w+ atrás"],
        "negation": ["nao", "nunca", "sem", "nenhum", "nenhuma", "jamais"],
        "condition": ["se", "quando", "ate", "antes d[eoa]s?", "depois d[eoa]s?", "caso", "enquanto", "exceto",
                      "salvo"],
        "obvious": [r"clique (aqui|neste|nesse|no botão|em cima)", r"use (este|esse|o) (botão|campo)",
                    r"n?(esta|essa) (tela|seção|página|aba|área|janela|lista) (mostra|exibe|lista|permite|serve|você)",
                    r"aqui (você|voce) (pode|vê|ve|encontra)", r"(você|voce) (encontra|vê|verá)",
                    r"basta (clicar|tocar)"],
        "wordy": [r"permite que (você|voce)", r"(você|voce) (pode|poderá|consegue) ", r"é possível ", r"lembre-se",
                  r"note que", r"observação:", r"de forma (fácil|rápida|simples)", r"facilmente", r"rapidamente",
                  r"simplesmente", r"por favor"],
        "confirmation": [r"apagar", r"excluir", r"remover", r"descartar", r"desfaz\w*", r"de vez",
                         r"permanente\w*", r"irrevers\w*", r"não (dá|da|pode) (para )?(voltar|desfazer)"],
        "security": [r"pix", r"pagamento", r"pagar", r"pedido", r"compra", r"cobran\w*", r"confirma\w*", r"termos?"],
        "doubled_ok": ["ha", "lá", "la", "tchau", "pisca", "corre", "bem"],
        "endings": ["amente", "mente", "acoes", "icoes", "coes", "cao", "ando", "endo", "indo", "adas", "idas",
                    "ados", "idos", "ada", "ida", "ado", "ido", "ar", "er", "ir", "as", "es", "os", "ou", "am",
                    "em", "a", "e", "o", "s"],
    },
}
# tautology: on
# A word that is a function word in another active language: "no" is "em + o" in Portuguese.
COLLISIONS = {"negation": {"no": {"pt"}}}

# Concepts across languages: "Search" and "Find" say the same, and the comment "close the window"
# says the same as window.close(). The first word of each line names the concept. Projects add
# their own domain lines in config.json ("synonyms").
SYNONYMS = """buscar procurar pesquisar search find lookup fetch
abrir open
ver mostrar exibir visualizar show view display
fechar sair close exit quit
apagar excluir remover deletar limpar delete remove erase clear
salvar gravar guardar save store persist
carregar load
copiar copy
editar alterar mudar modificar trocar atualizar edit change modify update
adicionar incluir acrescentar add insert append push
criar novo nova create new make build
configuracao ajuste opcao preferencia config settings setting options preferences
voltar retornar back return
lista listagem list
preco valor price value cost
aviso alerta notificacao avisar alertar notificar alert warning notification notify
clique clicar click press tap
iniciar comecar ligar start begin launch run
parar interromper desligar pausar stop pause halt
ativar habilitar enable active ativo
desativar desabilitar disable inativo
arquivo file
pasta diretorio folder dir directory
janela window
tela pagina screen page
botao button
texto text
erro falha error failure fail
vazio nenhum empty none
regra rule
conta account
usuario user
baixar download
enviar mandar send upload
ler read
escrever write
contar contador count counter
incrementar increment
decrementar decrement
definir set
obter pegar get
verificar conferir checar validar check verify validate
ordenar sort
filtrar filter
esconder ocultar hide
nome name
caminho path
chave key
imagem image
cor color
tamanho size
titulo title
rotulo label
dica tooltip hint
mensagem message msg"""

# Text kinds by the call that receives the text, each with its limits. Projects add their own
# component names in config.json ("kinds"), e.g. a design-system tooltip helper.
KIND_PATTERNS = [  # (kind, case-insensitive, default patterns)
    ("tooltip", True, [r"setToolTip\(", r"\.tooltip\(", r"\btitle=", r"data-tooltip", r"\btooltip=", r"\.title\s*=",
                       r"<Tooltip\b"]),
    ("button", True, [r"\w*button\(", r"MenuItem::\w+\(", r"QPushButton", r"QToolButton", r"menu_item", r"\baction\(",
                      r"<button\b", r"<(Button|MenuItem|Tab|option)\b"]),
    ("title", False, [r"<(h[1-4]|label|legend|th|Title|Heading)\b"]),
    ("hint", True, [r"tool_?tip", r"\bhint\(", r"placeholder", r"subtitle", r"descri", r"caption", r"\bhelp",
                    r"\bnote\b"]),
    ("title", True, [r"\bsection\(", r"title", r"heading", r"\bheader", r"\blabel\(", r"QLabel", r"chip\(", r"badge"]),
    ("notice", True, [r"\b(progress|notice\w*|status|toast|Err|invalid|blocked|message)\b"]),
]
DEFAULTS = {
    "languages": ["en"],
    "ui_paths": [],            # files or folders that are UI even without a framework import
    "test_paths": [],          # extra test folders
    "skip_dirs": [],           # extra folders to ignore
    "sensitive_paths": [],     # payment/legal copy: no length finding, listed for review instead
    "synonyms": [],            # extra concept lines: "first-word synonym synonym..."
    "kinds": {},               # kind -> extra regexes of calls that receive that kind of text
    "tooltip_calls": [],       # extra regexes of calls whose argument is a tooltip (measured whole)
    "tooltip_second_arg": [],  # tooltip calls whose tooltip is the second argument
    "placement_calls": [],     # extra calls that place explanatory text on screen (density count)
    "limits": {"button": [4, 28, 1], "title": [6, 48, 1], "hint": [16, 90, 2], "notice": [22, 140, 2],
               "text": [26, 160, 2]},  # kind: [words, characters, sentences]
    "tooltip_line_chars": 46,  # characters per line of the tooltip balloon
    "tooltip_lines": 2,
    "density": 5,              # explanatory texts per screen function
    "descriptive_words": 8,    # words from which a text explains instead of naming
}


def words_re(items):
    return r"\b(" + "|".join(items) + r")\b"


class Settings:
    """The project's settings, compiled. Rule modules read them at call time (C.x), so --selftest
    can switch to the cases' own settings after import."""

    def __init__(self):
        self.load(CONFIG_FILE)

    def load(self, path):
        raw = dict(DEFAULTS)
        self.path = Path(path)
        if self.path.is_file():
            raw.update(json.loads(self.path.read_text(encoding="utf-8")))
        self.raw = raw
        langs = [l for l in raw["languages"] if l in LANG] or ["en"]
        self.languages = langs

        def joined(key):
            items = []
            for lang in langs:
                for item in LANG[lang][key]:
                    blocked = COLLISIONS.get(key, {}).get(item, set())
                    if item not in items and not (blocked & set(langs) - {lang}):
                        items.append(item)
            return items

        self.stop = {w for lang in langs for w in LANG[lang]["stop"].split()}
        self.pleonasm = re.compile(words_re(joined("pleonasms")), re.I)
        self.negation = re.compile(words_re(joined("negation")))
        self.condition = re.compile(words_re(joined("condition")))
        self.obvious = re.compile(r"(?i)\b(" + "|".join(joined("obvious")) + ")") if joined("obvious") else None
        self.wordy = re.compile(r"(?i)\b(" + "|".join(joined("wordy")) + ")") if joined("wordy") else None
        self.confirmation = re.compile(r"(?i)\b(" + "|".join(joined("confirmation")) + ")") if joined("confirmation") else None
        self.security = re.compile("(?i)" + words_re(joined("security"))) if joined("security") else None
        self.doubled_ok = set(joined("doubled_ok"))
        self.endings = tuple(joined("endings"))
        self.ui_paths = tuple(raw["ui_paths"])
        self.test_paths = tuple(raw["test_paths"])
        self.skip_dirs = SKIP_DIRS | set(raw["skip_dirs"])
        self.sensitive_paths = tuple(raw["sensitive_paths"])
        self.limits = {k: tuple(v) for k, v in raw["limits"].items()}
        self.line_chars, self.tooltip_lines = raw["tooltip_line_chars"], raw["tooltip_lines"]
        self.density, self.descriptive = raw["density"], raw["descriptive_words"]
        extra = raw["kinds"]
        self.kinds = []
        for kind, nocase, patterns in KIND_PATTERNS:
            more = extra.get(kind, []) if nocase or kind != "title" else []
            self.kinds.append((kind, re.compile(("(?i)" if nocase else "") + "|".join(patterns + more))))
        calls = [r"setToolTip\(", r"\.tooltip\("] + raw["tooltip_calls"]
        self.tooltip_call = re.compile("|".join(calls))
        self.hint_raw = re.compile("(?i)" + "|".join([r"tool_?tip"] + raw["tooltip_calls"]))
        self.tooltip_second = tuple(raw["tooltip_second_arg"])
        self.placed = re.compile("(?i)" + "|".join([r"child\(", "hint", "descri", "subtitle", "caption", "note"]
                                                   + raw["placement_calls"]))
        self.concept = {}
        for group in (SYNONYMS + "\n" + "\n".join(raw["synonyms"])).splitlines():
            for word in group.split():
                self.concept.setdefault(stem(word, self.endings), stem(group.split()[0], self.endings))


def stem(word, endings=None):
    for ending in (C.endings if endings is None else endings):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


C = Settings()

DOUBLED_RE = re.compile(r"\b([A-Za-zÀ-ÿ]{2,})\s+\1\b", re.I)
TOOLTIP_RE = re.compile(r"(?:setToolTip|\.tooltip)\(\s*([\w.]+(?:\(\))?)\s*\)")
LABEL_RE = r"(?:setText|QPushButton|QLabel|QCheckBox|QRadioButton|\.label|\.child|Button::new|\.text)\(\s*{}\s*[,)]"
PY_STR = re.compile(r"""(?<![\w])[rbfuRBFU]{0,2}("(?:[^"\\\n]|\\.){3,}"|'(?:[^'\\\n]|\\.){3,}')""")
RS_STR = re.compile(r'"((?:[^"\\\n]|\\.){3,})"')
JS_STR = re.compile(r"""(["'`])((?:(?!\1)[^\\\n]|\\.){3,})\1""")
# Strings JS uses as code, not as text on screen: CSS class, selector, event, module, technical attribute.
JS_CODE_ARG = re.compile(r"""(?:className|class|classList\.\w+|querySelector(?:All)?|getElementById|addEventListener|"""
                         r"""import|from|require|type|id|key|href|src|role|name|rel|target|method|mode|"""
                         r"""data-[\w-]+|for|htmlFor|style|matches|closest)\s*[=(:]?\s*\{?\s*$""")
IDENT_RE = re.compile(r"(?<![\w])[A-Za-z_][A-Za-z0-9_]*")
NOT_PROSE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|WHERE|FROM|PRAGMA)\b|::|\(\?|\\\\|^\s*[#.]?[\w-]+\s*\{|\d+px"
                       r"|[#.][\w-]+\s*[\[,>.:]|\[[\w-]+[\]=*^$~|]|:(is|not|has|nth-\w+)\("
                       r"|</?[a-z][\w-]*[\s>/]|[\w-]+=\s*$")  # SQL, regex, CSS, SVG/HTML


class Finding:
    def __init__(self, path, line, kind, problem, fix):
        self.path, self.line, self.kind, self.problem, self.fix = path, line, kind, problem, fix

    def key(self):
        return f"{self.path}:{self.kind}:{self.problem}"

    @property
    def rule(self):
        return rule_and_subject(self.problem)[0]

    @property
    def subject(self):
        return rule_and_subject(self.problem)[1]

    def __str__(self):
        return f"{self.path}:{self.line} [{self.kind}] {self.problem} -> {self.fix}"


# ----------------------------------------------------------------------------- accepted
def rule_and_subject(problem):
    """The rule (the problem without quoted text or numbers) and the subject (the first quoted text)."""
    quoted = re.findall(r'"([^"]*)"', problem)
    head = re.match(r"^(\w+): ", problem)
    subject = quoted[0] if quoted else (head.group(1) if head else "")
    rule = re.sub(r'"[^"]*"', '""', problem)
    rule = re.sub(r"^\w+: ", "", rule)
    rule = re.sub(r"\(lines?[^)]*\)", "", rule)
    rule = re.sub(r"\d+", "N", rule)
    return re.sub(r"\s+", " ", rule).strip()[:60], subject


def load_accepted(store=None):
    """accepted.txt: path TAB rule TAB subject TAB reason TAB date. When the text changes, the finding returns."""
    store = store or ACCEPTED_FILE
    entries = []
    if store.is_file():
        for line in store.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4 and not line.startswith("#"):
                entries.append(parts)
    return entries


_ACCEPTED = None


def accepted(finding):
    global _ACCEPTED
    if SELFTEST:
        return False
    if _ACCEPTED is None:
        _ACCEPTED = {(p, r, s) for p, r, s, *_ in load_accepted()}
    return (finding.path, finding.rule, finding.subject) in _ACCEPTED


FINDING_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+) \[(?P<kind>[^\]]+)\] (?P<problem>.+?)(?: -> .*)?$")


def accept(finding_line, reason, store=None, tool="hunt.py"):
    """Records a judged false positive; the hook and --all stop showing it, for every session and agent."""
    store = store or ACCEPTED_FILE
    m = FINDING_LINE.match(finding_line.strip().lstrip("- "))
    if not m or not reason.strip():
        print(f'Use: --accept "<finding line, as {tool} printed it>" --reason "<why it is not a defect>"')
        return 2
    rule, subject = rule_and_subject(m.group("problem"))
    entry = "\t".join([m.group("path"), rule, subject, reason.strip().replace("\t", " "),
                       datetime.date.today().isoformat()])
    store.parent.mkdir(parents=True, exist_ok=True)
    existing = store.read_text(encoding="utf-8") if store.is_file() else (
        f"# Judged false positives: path, rule, subject, reason, date. Written by {tool} --accept.\n")
    store.write_text(existing.rstrip("\n") + "\n" + entry + "\n", encoding="utf-8")
    print(f"accepted: {m.group('path')} [{rule}] {subject}")
    return 0


def compare_expected(found, expected_file, record=False):
    """Compares findings with the recorded expectation; MISSING = a bad case that escaped, EXTRA = a false positive."""
    got = sorted(found)
    if record:
        expected_file.write_text("\n".join(got) + "\n", encoding="utf-8")
        print(f"{len(got)} finding(s) recorded in {expected_file.parent.name}/{expected_file.name}")
        return 0
    expected = sorted(l for l in expected_file.read_text(encoding="utf-8").splitlines() if l.strip())
    missing = [l for l in expected if l not in got]
    extra = [l for l in got if l not in expected]
    for line in missing:
        print(f"MISSING {line}")
    for line in extra:
        print(f"EXTRA   {line}")
    print(f"selftest: {len(expected) - len(missing)}/{len(expected)} expected, {len(extra)} extra")
    return 1 if missing or extra else 0


# --------------------------------------------------------------------------------- words
def plain(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def words(text):
    return [w for w in plain(text).split() if len(w) >= 3 and w not in C.stop]


def concept(word):
    return C.concept.get(stem(word), stem(word))


def concepts(text):
    return {concept(w) for w in words(text)}


def code_concepts(code):
    found = {concept(t) for t in tokens(code) if len(t) >= 2}
    if re.search(r"\+=\s*1\b|\+\+", code):
        found.add(concept("increment"))
    if re.search(r"-=\s*1\b|--", code):
        found.add(concept("decrement"))
    return found


def tokens(identifier_text):
    """Identifier parts: config_path, ConfigPath and configPath give config, path."""
    out = []
    for ident in IDENT_RE.findall(identifier_text):
        for part in re.split(r"_+", ident):
            out += [p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", part)]
    return out


def is_prose(literal):
    body = literal.strip()
    if len(body.split()) < 2 or NOT_PROSE.search(body):
        return False
    letters = sum(c.isalpha() for c in body)
    return letters / max(len(body), 1) > 0.6 and not re.fullmatch(r"[\w./\\:%{}-]+", body)


# ------------------------------------------------------------------------ reading code
CSS_LINE = re.compile(r"(?:^|[{;\s])[a-z-]+\s*:\s*[^;:]*;|^\s*[.#]?[\w-]+\s*\{")


def literals(line, suffix):
    if suffix == ".py":
        return [m.group(1)[1:-1] for m in PY_STR.finditer(line)]
    if suffix == ".rs":
        return [m.group(1) for m in RS_STR.finditer(line)]
    if suffix in JS:
        out = []
        for m in JS_STR.finditer(line):
            if JS_CODE_ARG.search(line[:m.start()]) or re.fullmatch(r"[\w:/\-\[\]. #>]+", m.group(2)) and "-" in m.group(2):
                continue  # className="flex gap-2", querySelector(".a b"): code
            if CSS_LINE.search(line):
                continue  # font:600 11px "Segoe UI": CSS inside a string
            out.append(re.sub(r"\$\{[^}]*\}", "{x}", m.group(2)))
        return out
    return []


def jsx_lines(lines):
    """JSX: bare text between tags becomes a literal ({"Save"}), measured like quoted texts."""
    out = list(lines)
    for i, line in enumerate(lines):
        line = re.sub(r">([^<>{}\n]*[A-Za-zÀ-ú][^<>{}\n]*)<", lambda m: '>{"' + m.group(1).strip() + '"}<'
                      if m.group(1).strip() else m.group(0), line)
        body = line.strip()
        prev = next((l.strip() for l in reversed(lines[:i]) if l.strip()), "")
        if (body and re.search(r"[A-Za-zÀ-ú]", body) and not re.search(r"[<>{}=;()\"'`]", body)
                and (prev.endswith(">") or prev.startswith('{"'))):
            line = line[:len(line) - len(line.lstrip())] + '{"' + body + '"}'
        out[i] = line
    return out


def strip_literals(line):
    # One pass per quote type: re-applying the regex over "" "" would match the code between two literals.
    line = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', line)
    line = re.sub(r"(?<![\w&])'(?:[^'\\\n]|\\.){3,}'", "''", line)
    return re.sub(r"\x60[^\x60\n]*\x60", '""', line)  # \x60: JS template


def comment_of(line, suffix):
    stripped = line.strip()
    marks = {".py": ("#",), ".sh": ("#",), ".ps1": ("#",), ".rs": ("//",),
             ".bat": ("rem ", "::"), ".cmd": ("rem ", "::")}.get(suffix, ("//", "/*", "{/*", "* ") if suffix in JS else ())
    for mark in marks:
        if stripped.lower().startswith(mark) or (mark == "* " and stripped == "*"):
            text = re.sub(r"\s*\*/\}?$", "", stripped[len(mark):]).lstrip("/!#* ")
            if text.startswith(("noqa", "type:", "pylint", "!", "region", "endregion", "fmt:", "-*-")):
                return None
            return text
    return None


def split_top(args):
    depth, part, parts = 0, "", []
    for c in args:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append(part.strip())
            part = ""
        else:
            part += c
    parts.append(part.strip())
    return [p for p in parts if p]


# -------------------------------------------------------------------------------- files
def added_lines(path):
    """Numbers of the lines new against HEAD; a file outside git counts whole (None)."""
    if not path.is_relative_to(REPO):
        return None
    rel = path.relative_to(REPO).as_posix()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=REPO,
                             capture_output=True).returncode == 0
    if not tracked:
        return None
    diff = subprocess.run(["git", "diff", "-U0", "HEAD", "--", rel], cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout
    lines = set()
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count = int(m.group(1)), int(m.group(2) or "1")
        lines.update(range(start, start + count))
    return lines


def changed_files():
    names = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO, capture_output=True,
                           text=True).stdout.split()
    names += subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO,
                            capture_output=True, text=True).stdout.split()
    return [REPO / n for n in dict.fromkeys(names)]


def is_test(rel, text=""):
    name = Path(rel).name
    return (rel.startswith(("tests/",) + C.test_paths) or "/tests/" in rel or name.startswith("test_")
            or name.endswith(("_test.py", "_tests.rs", "_test.go")) or "/__tests__/" in f"/{rel}"
            or re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", name) is not None)


# A real window, not just an image library: Qt widgets, Tk, wx, Kivy, GPUI, egui, iced, Slint.
UI_IMPORT = re.compile(r"^\s*(from\s+(PySide[26]|PyQt[56])\.QtWidgets|from\s+(PySide[26]|PyQt[56])\s+import\s.*QtWidgets|"
                       r"import\s+tkinter|from\s+tkinter|import\s+wx\b|from\s+kivy|use\s+(gpui|egui|iced|slint)\b)",
                       re.M)


def is_ui(rel, source=""):
    if Path(rel).suffix in JSX:
        return True
    if Path(rel).suffix in JS:  # JS that builds a screen: framework or DOM
        return re.search(r"""from\s+['"](react|preact|vue|svelte|solid-js|lit)\b|\.(textContent|innerText|innerHTML)\s*=|"""
                         r"""createElement\(|\b(alert|confirm|prompt)\(""", source) is not None
    return (bool(C.ui_paths) and rel.startswith(C.ui_paths)) or UI_IMPORT.search(source) is not None


# Text the user does not see: log, print, panic, assert. Errors (Err, raise) reach the screen and count.
LOG_CONTEXT = re.compile(r"(?i)\b(log|logger|logging|print|println!|eprintln!|tracing|debug!|trace!|info!|warn!|"
                         r"panic!|unreachable!|assert\w*!?|todo!|throw)\b|\.(debug|info|warning|exception|expect)\(|console\.")


def ui_text(line):
    return LOG_CONTEXT.search(line) is None


# Screen reader and comparisons: the text does not appear next to the visible label.
OFF_SCREEN = re.compile(r"(?i)accessib|setAccessible|aria_?label|aria-label|\balt=")


def on_screen(line, literal):
    if not ui_text(line) or OFF_SCREEN.search(line):
        return False
    quoted = re.escape(literal)
    return re.search(r"[=!]=\s*[rbf]?[\"'\x60]" + quoted + r"|" + quoted + r"[\"'\x60]\s*[=!]=", line) is None


def relative(path):
    for root in ((Path.cwd(), REPO) if SELFTEST else (REPO, Path.cwd())):
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return path.as_posix()


def ignored_lines(lines):
    """Lines marked as quotation: "tautology: example" on the line, or the block between off and on."""
    skip, off = set(), False
    for number, line in enumerate(lines, 1):
        block = IGNORE_BLOCK.search(line)
        if block:
            off = block.group(1) == "off"
        if off or block or IGNORE_LINE.search(line):
            skip.add(number)
    return skip


def own_cases(path, rel):
    """The skills' own bad-on-purpose cases stay out of every scan but the selftest."""
    return not SELFTEST and (path.resolve().is_relative_to(CASES) or re.search(r"(^|/)\.claude/skills/[^/]+/cases/", rel))
