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


def config_file_for(repo, name="tautology"):
    """Per-project config.json: .agents/<name>/config.json if that file exists, else .claude/<name>/config.json."""
    for folder in (".agents", ".claude"):
        cfg = Path(repo) / folder / name / "config.json"
        if cfg.is_file():
            return cfg
    return project_subdir(repo, name) / "config.json"


REPO = find_repo()
HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"                                   # the hunt's own test cases (--selftest)
PROJECT_DIR = project_subdir(REPO, "tautology")          # per-project settings and accepted findings
CONFIG_FILE = config_file_for(REPO, "tautology")
ACCEPTED_FILE = PROJECT_DIR / "accepted.txt"
SELFTEST = False


def use_repo(repo):
    """Switches settings and accepted-store target to repo (needed when hooks launch from plugin bin/)."""
    global REPO, PROJECT_DIR, CONFIG_FILE, ACCEPTED_FILE, _ACCEPTED
    REPO = Path(repo).resolve()
    PROJECT_DIR = project_subdir(REPO, "tautology")
    CONFIG_FILE = config_file_for(REPO, "tautology")
    ACCEPTED_FILE = PROJECT_DIR / "accepted.txt"
    _ACCEPTED = None
    C.load(CONFIG_FILE)

JS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
JSX = {".jsx", ".tsx", ".vue", ".svelte"}
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
        "obvious": [r"click here", r"this (screen|page|section) (shows|lets|lists)", r"(?:pro )?tip\s*:",
                    r"note\s*:"],
        "wordy": [r"allows you to", r"you can ", r"effortlessly", r"seamlessly", r"supercharge your",
                  r"all-in-one", r"unlock the power"],
        "confirmation": [r"delete", r"remove", r"discard", r"undo\w*", r"permanent\w*", r"irreversib\w*",
                         r"can(?:not|'t) be (?:undone|reversed|recovered)"],
        "security": [r"payment\w*", r"billing", r"invoice\w*", r"subscription\w*", r"refund\w*",
                     r"charg\w*", r"terms?", r"privacy", r"guarante\w*", r"warrant\w*",
                     r"purchase\w*", r"order\w*", r"checkout"],
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
        "obvious": [r"dica\s*:", r"nota\s*:", r"clique (aqui|neste|nesse|no bot[ãa]o|em cima)", r"use (este|esse|o) (bot[ãa]o|campo)",
                    r"n?(esta|essa) (tela|se[çc][ãa]o|p[áa]gina|aba|[áa]rea|janela|lista) (mostra|exibe|lista|permite|serve|voc[êe])",
                    r"aqui (você|voce) (pode|vê|ve|encontra)", r"(você|voce) (encontra|vê|verá)",
                    r"basta (clicar|tocar)"],
        "wordy": [r"permite que (você|voce)", r"(você|voce) (pode|poderá|consegue) ", r"[ée] poss[íi]vel ", r"lembre-se",
                  r"note que", r"observa[çc][ãa]o:", r"de forma (f[áa]cil|r[áa]pida|simples)", r"facilmente", r"rapidamente",
                  r"simplesmente", r"por favor", r"tudo em um s[óo] lugar", r"sem complica[çc][õo]es"],
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
                    r"\bnote\b", r"\binfo\s*:"]),
    ("title", True, [r"\bsection\(", r"(?<!::)\btitle\b", r"heading", r"\bheader", r"\blabel\(", r"QLabel", r"chip\(", r"badge"]),
    ("notice", True, [r"\b(progress|notice\w*|status|toast|Err|invalid|blocked|message)\b"]),
]
DEFAULTS = {
    "languages": ["en", "pt"],
    "ui_paths": [],            # files or folders that are UI even without a framework import
    "test_paths": [],          # extra test folders
    "skip_dirs": [],           # extra folders to ignore
    "sensitive_paths": [],     # payment/legal copy: no length finding, listed for review instead
    "synonyms": [],            # extra concept lines: "first-word synonym synonym..."  tautology: example
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
    "hook_scan_answer": False,         # Stop hook: scan the agent's chat answer (opt-in to avoid noisy turns)
    "hook_review_new_ui_texts": False, # Stop hook: list every new UI string (auto-on during debloat-design)
    "hook_bloat": False,               # Stop hook: run length/wordy/density checks (always on in CLI & debloat-design)
    "max_emojis": 0,                   # UI texts with an emoji allowed per screen before it counts as decoration
    "theme_paths": [],         # files or folders where palette/design tokens are defined
    "design": {                # thresholds and token hints for design_audit.py
        "max_container_depth": 3,
        "max_card_nesting": 2,  # bordered boxes inside each other (JSX), checked by the default hook
        "max_cards": 6,
        "max_dividers": 3,
        "max_badges": 6,
        "max_icons": 10,
        "max_header_stack": 2,
        "max_info_hints": 6,
        "max_primary_accents": 1,
        "max_headlines": 1,
        "max_exposed_filters": 3,
        "style_tokens": [],
        "states_file": "",
        "capture_command": "",
    },
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
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw.update(loaded)
            except (OSError, ValueError) as err:
                print(f"tautology config warning ({self.path}): {err}", file=sys.stderr)
        self.raw = raw
        langs = [l for l in raw["languages"] if l in LANG] or DEFAULTS["languages"]
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
        limits = dict(DEFAULTS["limits"])
        limits.update(raw.get("limits") or {})
        self.limits = {k: tuple(v) for k, v in limits.items()}
        self.line_chars, self.tooltip_lines = raw["tooltip_line_chars"], raw["tooltip_lines"]
        self.density, self.descriptive = raw["density"], raw["descriptive_words"]
        self.hook_scan_answer = bool(raw.get("hook_scan_answer", False))
        self.hook_review_new_ui_texts = bool(raw.get("hook_review_new_ui_texts", False))
        self.hook_bloat = bool(raw.get("hook_bloat", False))
        self.theme_paths = tuple(raw["theme_paths"])
        design = dict(DEFAULTS["design"])
        design.update(raw.get("design") or {})
        self.design = design
        extra = raw["kinds"]
        self.kinds = []
        for kind, nocase, patterns in KIND_PATTERNS:
            more = extra.get(kind, []) if nocase or kind != "title" else []
            self.kinds.append((kind, re.compile(("(?i)" if nocase else "") + "|".join(patterns + more))))
        calls = [r"setToolTip\(", r"\.tooltip\("] + raw["tooltip_calls"]
        self.tooltip_call = re.compile("|".join(calls))
        self.hint_raw = re.compile("(?i)" + "|".join([r"tool_?tip", r"-info[\"']"] + raw["tooltip_calls"]))
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
                         r"""data-[\w-]+|for|htmlFor|style|matches|matchMedia|closest)\s*[=(:]?\s*\{?\s*$""")
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


def report_lines(findings):
    """Hook report lines: a fix already printed for an earlier finding is not printed again."""
    seen, out = set(), []
    for f in findings:
        out.append(f"- {f.path}:{f.line} [{f.kind}] {f.problem}" + ("" if f.fix in seen else f" -> {f.fix}"))
        seen.add(f.fix)
    return out


# ----------------------------------------------------------------------------- AI visual tics
# A tic finding asks for a rebuild, not a deletion: the report adds the screen's own vocabulary and the
# file is remembered, so the lines that fix it are checked for hardcoded styles.
TIC_PROBLEM = re.compile(
    r"carry a helper text|decorative emoji|decorative badges|decorative status dot|: footnote \"|"
    r"tooltip or info icon|cards nested|dividers/separators|badges/chips|info_hint badges|boxed cards|"
    r"competing primary accents|stacked filter controls|exposed filter/sort dropdowns|"
    r"fixed-width filter/sort trigger|inner boxed card inside a modal|overloaded hierarchy|"
    r"composed hierarchy|decorative (?:gradient/)?glassmorphism|gradient text|pill soup|pulsing status"
)
DESIGN_LAYOUT_PROBLEM = re.compile(
    r"cards nested|decorative badges|decorative status dot|dividers/separators|badges/chips|"
    r"info_hint badges|boxed cards|competing primary accents|stacked filter controls|"
    r"exposed filter/sort dropdowns|fixed-width filter/sort trigger|inner boxed card inside a modal|"
    r"overloaded hierarchy|composed hierarchy|explicit spacer boxes|stacked text blocks|"
    r"repeated inline style pattern|decorative (?:gradient/)?glassmorphism|gradient text|pill soup|pulsing status|"
    r"competing HEADLINE"
)
TICS = []  # paths with a tic reported in this hook run (hunt and design audit share this module)


def note_tics(findings, state=None, session=None):
    paths = sorted({f.path for f in findings if TIC_PROBLEM.search(f.problem)})
    TICS.extend(p for p in paths if p not in TICS)
    if paths and state is not None:
        with (Path(state) / f"{session}.tics").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(paths) + "\n")


def tic_files(state, session):
    marker = Path(state) / f"{session}.tics"
    return set(marker.read_text(encoding="utf-8").splitlines()) - {""} if marker.is_file() else set()


VOCAB = [
    re.compile(r"\b(?:p|palette|theme|colors|tokens)\.(?:[a-z]\w*)(?:\.\w+)?"),  # GPUI palette, theme objects
    re.compile(r"\b(?:visual|kit|theme|ui)::\w+"),                               # Rust component modules
    re.compile(r"var\(--[\w-]+\)"),                                               # CSS custom properties
    re.compile(r"\b(?:bg|text|border|ring|fill)-(?:primary|secondary|muted|accent|destructive|success|warning|danger|"
               r"info|foreground|background|card|popover|surface|subtle)(?:-[\w/]+)?\b"),  # semantic Tailwind
    re.compile(r"<(?:[A-Z]\w*(?:Icon|Text|Badge|Chip|Tag|Heading|Label|Stack))\b"),          # component kit
]
VOCAB_SKIP = re.compile(r"^(?:p|theme|tokens)\.(?:clone|into|get|map|len|to_\w+)$")


def vocabulary(paths, root=None, limit=10):
    """The tokens and components the edited screens and their siblings already use, most used first."""
    base = Path(root or REPO)
    files = []
    for raw in paths:
        path = (base / raw) if not Path(raw).is_absolute() else Path(raw)
        if path.is_file():
            files.append(path)
            files += sorted(p for p in path.parent.glob(f"*{path.suffix}") if p != path)[:6]
    count = Counter()
    for path in dict.fromkeys(files):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in VOCAB:
            count.update(m.group(0).lstrip("<") for m in pattern.finditer(text))
    return [name for name, _ in count.most_common(limit * 2) if not VOCAB_SKIP.match(name)][:limit]


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
QUOTED_FINDING_RE = re.compile(
    r"^(?:block of |chain |local UI helper |near-duplicate component |new component |pleonasm |doubled word |new UI text |"
    r"comment restates the code: |title ends with a period: |tooltip of ~|"
    r"sentence said \d+x: |same idea twice: |passage repeated in different paragraphs: |"
    r"hardcoded (?:(?:CSS |inline (?:CSS |QSS )?)?color literal|inline (?:CSS |QSS )?color|Tailwind (?:arbitrary color|font-size|radius)) |"
    r"stock Tailwind color )"
    r"|(?:\brepeats the text on line \d+|\bsays the same as |\bexplains the obvious: |\bis wordy: |: footnote .+ under the content|\(limit \d+[^)]*\): )"
)


def _git_exclude_file(repo):
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True, text=True)
    if top.returncode != 0 or not top.stdout.strip():
        return None
    out = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=repo, capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    exclude = Path(out.stdout.strip())
    return exclude if exclude.is_absolute() else (Path(repo) / exclude).resolve()


def _repo_rel(path, repo):
    try:
        return Path(path).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return None


def ensure_git_ignored(path, repo=None):
    """Adds a tool-created path to local .git/info/exclude when inside a git repo and not yet ignored."""
    base = Path(repo or REPO).resolve()
    rel = _repo_rel(path, base)
    if not rel:
        return
    if subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=base, capture_output=True).returncode == 0:
        return
    exclude = _git_exclude_file(base)
    if not exclude:
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude.read_text(encoding="utf-8", errors="replace").splitlines() if exclude.is_file() else []
    if rel not in lines:
        text = "\n".join(lines).rstrip("\n")
        exclude.write_text((text + "\n" if text else "") + rel + "\n", encoding="utf-8")


def _remove_git_exclude(rel, repo):
    exclude = _git_exclude_file(repo)
    if not rel or not exclude or not exclude.is_file():
        return
    lines = exclude.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = [line for line in lines if line != rel]
    if len(kept) != len(lines):
        exclude.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")


def _cleanup_accepted_store(store, repo):
    rel = _repo_rel(store, repo)
    if store.is_file():
        store.unlink()
    _remove_git_exclude(rel, repo)
    for folder in (store.parent, store.parent.parent):
        if folder.is_dir() and folder.name in ("tautology", "modular", ".claude", ".agents"):
            try:
                if not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                break


def accept_command(script, reason="<reason>"):
    """CLI hint to record a false positive; pipes via stdin (--accept -) on Windows so PowerShell keeps quotes."""
    exe = Path(sys.executable).as_posix()
    script_path = Path(script).as_posix()
    q = lambda s: f'"{s}"' if " " in s else s
    if sys.platform == "win32":
        runner = f"& {q(exe)}" if " " in exe else exe
        return f"'<finding>' | {runner} {q(script_path)} --accept - --reason \"{reason}\""
    return f'{q(exe)} {q(script_path)} --accept "<finding>" --reason "{reason}"'


def accept(finding_line, reason, store=None, tool="hunt.py", repo=None):
    """Records a judged false positive; auto-ignores accepted.txt locally via .git/info/exclude."""
    global _ACCEPTED
    store = store or ACCEPTED_FILE
    base = Path(repo or REPO).resolve()
    raw = finding_line.strip()
    if raw == "-":
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        raw = next((line.strip() for line in sys.stdin.read().splitlines() if line.strip()), "")
    cleaned = re.sub(r"^(?:-\s+|\d+\.\s+)", "", raw)
    m = FINDING_LINE.match(cleaned)
    if not m or not reason.strip():
        if sys.platform == "win32":
            print(f"Use: '<finding line, as {tool} printed it>' | ... --accept - --reason \"<why it is not a defect>\"")
        else:
            print(f'Use: --accept "<finding line, as {tool} printed it>" (or --accept - via stdin) --reason "<why it is not a defect>"')
        return 2
    problem = m.group("problem")
    if '"' not in problem and QUOTED_FINDING_RE.search(problem):
        print("Error: internal double quotes in finding line were stripped by the shell; pipe the line into --accept - via stdin.")
        return 2
    rule, subject = rule_and_subject(problem)
    target_path = m.group("path")
    owner_tool = "design_audit.py" if (m.group("kind") == "design" and DESIGN_LAYOUT_PROBLEM.search(problem)) else tool
    entry = "\t".join([target_path, rule, subject, reason.strip().replace("\t", " "),
                       datetime.date.today().isoformat(), owner_tool])
    store.parent.mkdir(parents=True, exist_ok=True)
    header = f"# Judged false positives: path, rule, subject, reason, date, tool. Written by {tool} --accept."
    if store.is_file():
        raw_lines = store.read_text(encoding="utf-8").splitlines()
        out_lines, replaced = [], False
        for line in raw_lines:
            parts = line.split("\t")
            if len(parts) >= 4 and not line.startswith("#") and (parts[0], parts[1], parts[2]) == (target_path, rule, subject):
                if not replaced:
                    out_lines.append(entry)
                    replaced = True
                continue
            out_lines.append(line)
        if not replaced:
            out_lines.append(entry)
        store.write_text("\n".join(out_lines).rstrip("\n") + "\n", encoding="utf-8")
    else:
        store.write_text(header + "\n" + entry + "\n", encoding="utf-8")
    _ACCEPTED = None
    ensure_git_ignored(store, repo=base)
    print(f"accepted: {target_path} [{rule}] {subject}")
    return 0


def prune_accepted(scanned_paths, raw_findings, tool, store=None, repo=None):
    """Drops resolved or deleted-file entries from accepted.txt; removes the file and its ignore entry when empty."""
    global _ACCEPTED
    if SELFTEST:
        return
    store = store or ACCEPTED_FILE
    if not store.is_file():
        return
    base = Path(repo or REPO).resolve()
    entries = load_accepted(store)
    if not entries:
        _ACCEPTED = None
        _cleanup_accepted_store(store, base)
        return
    scanned = set(scanned_paths)
    live = {(f.path, f.rule, f.subject) for f in raw_findings}
    modular_store = store.parent.name == "modular"
    kept = []
    for parts in entries:
        entry_path, entry_rule, entry_subject = parts[0], parts[1], parts[2]
        if not (base / entry_path).is_file():
            continue
        owner = parts[5] if len(parts) >= 6 else (tool if modular_store else "")
        if entry_path in scanned and owner == tool and (entry_path, entry_rule, entry_subject) not in live:
            continue
        kept.append(parts)
    if len(kept) == len(entries):
        ensure_git_ignored(store, repo=base)
        return
    _ACCEPTED = None
    if not kept:
        _cleanup_accepted_store(store, base)
        return
    header = f"# Judged false positives: path, rule, subject, reason, date, tool. Written by {tool} --accept.\n"
    store.write_text(header + "\n".join("\t".join(p) for p in kept) + "\n", encoding="utf-8")
    ensure_git_ignored(store, repo=base)


def prune_paths(paths, raw_fn, tool, store=None, repo=None):
    """Scans whole files in paths only when an accepted.txt store exists, pruning resolved findings."""
    store = store or ACCEPTED_FILE
    if SELFTEST or not store.is_file():
        return
    base = Path(repo or REPO).resolve()
    raw_findings = []
    scanned = []
    for raw in paths:
        path = Path(raw).resolve()
        rel = _repo_rel(path, base) or relative(path)
        scanned.append(rel)
        if path.is_file():
            raw_findings += raw_fn(path)
    prune_accepted(scanned, raw_findings, tool, store=store, repo=base)


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
_TW_TOKEN = re.compile(
    r"^(?:flex|grid|block|inline|hidden|relative|absolute|fixed|sticky|contents|table|"
    r"(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|w|h|min-w|max-w|min-h|max-h|size|gap|space-[xy]|"
    r"text|font|leading|tracking|bg|from|via|to|border|divide|ring|outline|rounded|shadow|opacity|"
    r"items|justify|content|self|place|overflow|cursor|select|pointer-events|z|inset|top|right|bottom|left|"
    r"col-span|row-span|grid-cols|grid-rows|shrink|grow|basis|order|transition|duration|ease|animate|backdrop|"
    r"fill|stroke|sr-only|truncate|whitespace|break|line-clamp)-|(?:hover|focus|active|disabled|dark|sm|md|lg|xl|2xl|group-hover):)"
)


def _is_css_or_tw_string(val):
    """True when a JS string literal looks like CSS classes/selectors or a kebab-id, not UI prose."""
    if "-" not in val or not re.fullmatch(r"[\w:/\-\[\]. #>]+", val):
        return False
    parts = val.split()
    if len(parts) == 1:
        return True  # single kebab token: "btn-primary", "bg-card", "foo-bar"
    if val[0] in ".#" or ">" in val:
        return True  # CSS selector: ".a > .b-c"
    if re.match(r"^[A-ZÀ-Ý]", val):
        return False  # capitalized UI prose with a hyphen: "Auto-save enabled", "Enter your e-mail address"
    tw_hits = sum(1 for tok in parts if _TW_TOKEN.match(tok))
    kebab_hits = sum(1 for tok in parts if "-" in tok or ":" in tok)
    return tw_hits >= 1 or kebab_hits >= 2


def literals(line, suffix):
    if suffix == ".py":
        return [m.group(1)[1:-1] for m in PY_STR.finditer(line)]
    if suffix == ".rs":
        return [m.group(1) for m in RS_STR.finditer(line)]
    if suffix in JS:
        out = []
        for m in JS_STR.finditer(line):
            if JS_CODE_ARG.search(line[:m.start()]) or _is_css_or_tw_string(m.group(2)):
                continue  # className="flex gap-2", querySelector(".a b"): code
            if CSS_LINE.search(line):
                continue  # font:600 11px "Segoe UI": CSS inside a string
            out.append(re.sub(r"\$\{[^}]*\}", "{x}", m.group(2)))
        return out
    return []


_JSX_OPEN_TAG = re.compile(r"<([A-Za-z][\w.-]*)(?=[\s/>]|$)")
_JSX_BEFORE_OK = re.compile(r"(?:^|[\s(\[{=,:?>&|!;]|\b(?:return|case|yield|await))$")


def _step_comment_str(text, i, in_str, in_block, escape, allow_str=True):
    """Advances one step through JS/JSX comment or string state.
    Returns (action, next_i, in_str, in_block, escape) where action is 'continue', 'eol', or 'code'."""
    n = len(text)
    ch = text[i]
    if in_block:
        if ch == "*" and i + 1 < n and text[i + 1] == "/":
            return "continue", i + 2, in_str, False, escape
        return "continue", i + 1, in_str, True, escape
    if in_str:
        if escape:
            return "continue", i + 1, in_str, False, False
        if ch == "\\":
            return "continue", i + 1, in_str, False, True
        if ch == in_str:
            return "continue", i + 1, None, False, False
        return "continue", i + 1, in_str, False, False
    if ch == "/" and i + 1 < n:
        if text[i + 1] == "/":
            return "eol", i, None, False, False
        if text[i + 1] == "*":
            return "continue", i + 2, None, True, False
    if allow_str and ch in "\"'`":
        return "continue", i + 1, ch, False, False
    return "code", i, None, False, False


def _strip_line_comment(line, in_block=False):
    """Strips a trailing // comment outside quotes and block comments; returns (cleaned, in_block)."""
    in_str, escape, i, n = None, False, 0, len(line)
    while i < n:
        action, i, in_str, in_block, escape = _step_comment_str(line, i, in_str, in_block, escape)
        if action == "eol":
            return line[:i].rstrip(), False
        if action == "code":
            i += 1
    return line, in_block


def _scan_jsx_open_state(line):
    """Scans a line and returns (tag_open, in_str, in_block, brace_depth) at end of line."""
    tag_open, in_str, in_block, brace_depth, escape = False, None, False, 0, False
    i, n = 0, len(line)
    while i < n:
        action, i, in_str, in_block, escape = _step_comment_str(
            line, i, in_str, in_block, escape, allow_str=(tag_open or brace_depth > 0),
        )
        if action == "eol":
            break
        if action == "continue":
            continue
        ch = line[i]
        if tag_open:
            if ch == "{":
                brace_depth += 1
            elif ch == "}" and brace_depth > 0:
                brace_depth -= 1
            elif brace_depth == 0:
                if ch == "=" and i + 1 < n and line[i + 1] == ">":
                    tag_open = False  # TS generic arrow <T>(...) =>
                    i += 2
                    continue
                if ch == ">":
                    tag_open = False
            i += 1
            continue
        if ch == "<" and not line[i:].startswith(("</", "<!--", "<>")):
            m = _JSX_OPEN_TAG.match(line, i)
            if m and _JSX_BEFORE_OK.search(line[:i]):
                tag_open = True
                i = m.end()
                continue
        i += 1
    return tag_open, in_str, in_block, brace_depth


def normalize_jsx_tags(lines):
    """Joins multi-line JSX opening tags (<Tag\\n  className="..."\\n>) onto their opening line,
    replacing absorbed continuation lines with "" to preserve exact 1-to-1 line numbers."""
    src = list(lines)
    n = len(src)
    out = list(src)
    i = 0
    while i < n:
        line = out[i]
        tag_open, in_str, in_block, brace_depth = _scan_jsx_open_state(line)
        if not tag_open:
            i += 1
            continue
        head_clean, blk = _strip_line_comment(line)
        parts = [head_clean.rstrip()]
        close_j, close_pos = None, None
        max_j = min(n, i + 41)
        for j in range(i + 1, max_j):
            cur = out[j]
            escape = False
            k, m_len = 0, len(cur)
            found_close = None
            while k < m_len:
                action, k, in_str, in_block, escape = _step_comment_str(cur, k, in_str, in_block, escape)
                if action == "eol":
                    break
                if action == "continue":
                    continue
                ch = cur[k]
                if ch == "{":
                    brace_depth += 1
                elif ch == "}" and brace_depth > 0:
                    brace_depth -= 1
                elif brace_depth == 0:
                    if ch == "=" and k + 1 < m_len and cur[k + 1] == ">":
                        found_close = -1  # TS generic arrow, abort
                        break
                    if ch == ">":
                        found_close = k
                        break
                k += 1
            if found_close == -1:
                break
            if found_close is not None:
                close_j, close_pos = j, found_close
                prefix, _ = _strip_line_comment(cur[:close_pos + 1], blk)
                piece = prefix.strip()
                if piece:
                    parts.append(piece)
                break
            cleaned, blk = _strip_line_comment(cur, blk)
            piece = cleaned.strip()
            if piece:
                parts.append(piece)
        if close_j is None:
            i += 1
            continue
        joined = " ".join(parts)
        joined = re.sub(r"\s+(/?)>", lambda m: (" />" if m.group(1) else ">"), joined)
        remainder = out[close_j][close_pos + 1:]
        for k in range(i + 1, close_j):
            out[k] = ""
        if not remainder.strip():
            out[i] = joined
            out[close_j] = ""
            i = close_j + 1
        elif _scan_jsx_open_state(remainder)[0]:
            out[i] = joined
            out[close_j] = remainder
            i = close_j
        else:
            out[i] = joined + remainder
            out[close_j] = ""
            i = close_j + 1
    return out


def _strip_braces(text):
    """Replaces balanced {...} interpolations inside JSX text with {x}."""
    out, depth = [], 0
    for ch in text:
        if ch == "{":
            if depth == 0:
                out.append("{x}")
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _jsx_text_sub(match):
    raw = match.group(1)
    if "{" in raw:
        stripped = _strip_braces(raw).strip()
        # Require actual readable prose outside {...}, not pure JS expressions like >{items.map(...)}<
        outside = re.sub(r"\{x\}", " ", stripped).strip()
        if not outside or not re.search(r"[A-Za-zÀ-ú]", outside):
            return match.group(0)
        escaped = stripped.replace("\\", "\\\\").replace('"', '\\"')
        return '>{"' + escaped + '"}<'
    body = raw.strip()
    if not body:
        return match.group(0)
    escaped = body.replace("\\", "\\\\").replace('"', '\\"')
    return '>{"' + escaped + '"}<'


def jsx_lines(lines, normalized=False):
    """JSX: normalizes multi-line opening tags and turns bare text between tags into quoted literals."""
    out = list(lines) if normalized else normalize_jsx_tags(lines)
    for i, line in enumerate(out):
        if not line:
            continue
        line = re.sub(r">([^<>]*[A-Za-zÀ-ú][^<>]*)<", _jsx_text_sub, line)
        body = line.strip()
        prev = next((l.strip() for l in reversed(out[:i]) if l.strip()), "")
        if (body and re.search(r"[A-Za-zÀ-ú]", body)
                and not re.search(r"[<>{}=;\"`]|=>|\w\(", body)
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
             ".bat": ("rem ", "::"), ".cmd": ("rem ", "::")}.get(suffix, ("//", "/*", "{/*", "* ", "<!--") if suffix in JS else ())
    for mark in marks:
        if stripped.lower().startswith(mark) or (mark == "* " and stripped == "*"):
            text = re.sub(r"\s*(?:\*/\}?|-->)$", "", stripped[len(mark):]).lstrip("/!#* ")
            if text.startswith(("noqa", "type:", "pylint", "!", "region", "endregion", "fmt:", "-*-")):
                return None
            return text
    return None


def split_top(args):
    """Splits a top-level comma-separated argument list, respecting nested brackets and quoted strings."""
    depth, part, parts = 0, "", []
    in_str, escape = None, False
    n = len(args)
    for i, c in enumerate(args):
        if in_str:
            part += c
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == in_str:
                in_str = None
            continue
        if c in "\"`":
            in_str = c
            part += c
            continue
        if c == "'" and not (i > 0 and args[i - 1] in "&<") and not re.match(r"'[a-z_]\w*(?:\s+[A-Z_]|\s*[,>+)])", args[i:]):
            in_str = "'"
            part += c
            continue
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
def head_source(path):
    """Contents of a tracked file at HEAD, or None when untracked or outside the repository."""
    if not path.is_relative_to(REPO):
        return None
    rel = path.relative_to(REPO).as_posix()
    res = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=REPO, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return res.stdout if res.returncode == 0 else None


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


SKILL_NON_TEST = re.compile(r"(^|/)(?:\.(?:claude|agents)/)?skills/[^/]+/test_(?:rules|audit)\.py$")


def is_test(rel, text=""):
    if SKILL_NON_TEST.search(rel):
        return False
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


def rust_test_start(lines):
    """Line of the inline `#[cfg(test)] mod tests { ... }`. `mod tests;` lives in another file and a
    gated `use` or helper is one item: neither starts the test part of this file."""
    for number, line in enumerate(lines, 1):
        if line.strip() == "#[cfg(test)]":
            following = next((l.strip() for l in lines[number:number + 3] if l.strip()), "")
            if re.match(r"(?:pub(?:\([^)]*\))?\s+)?mod\s+\w+\s*\{", following):
                return number
    return None


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


_OWN_CASES_RE = re.compile(r"(^|/)(?:\.(?:claude|agents)/)?skills/[^/]+/cases/")


def own_cases(path, rel):
    """The skills' own bad-on-purpose cases stay out of every scan but the selftest."""
    return not SELFTEST and (
        bool(_OWN_CASES_RE.search(rel))
        or path.resolve().is_relative_to(CASES)
    )
