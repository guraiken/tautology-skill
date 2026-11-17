"""The agent's answer: a sentence or passage repeated across paragraphs, a recap of what was said."""
import re
from collections import Counter, defaultdict

import core
from core import *  # noqa: F403
from copy_rules import text_checks


def scan_message(text, label="answer"):
    """Repetition in the agent's answer: sentences and 8+ word passages said twice."""
    out, prose, fence = [], [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if not fence and not line.lstrip().startswith("|"):
            prose.append(line)
    body = "\n".join(prose)
    for number, line in enumerate(prose, 1):
        # A quotation in quotes or backticks (a finding, an app text) is not the agent speaking.
        text_checks(label, number, re.sub(r'`[^`]*`|"[^"]*"|“[^”]*”', " ", line), out)
    sentences = Counter(plain(s) for s in re.split(r"(?<=[.!?])\s+|\n+", body) if len(plain(s).split()) >= 6)
    for sentence, count in sentences.items():
        if count >= 2:
            out.append(Finding(label, 0, "answer", f'sentence said {count}x: "{sentence[:70]}"', "say it once"))
    said = []  # (paragraph, sentence, concepts)
    for index, paragraph in enumerate(p for p in re.split(r"\n\s*\n", body) if p.strip()):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", paragraph):
            ideas = concepts(sentence)
            if len(ideas) >= 6:
                said.append((index, sentence.strip(), ideas))
    paraphrased = []
    for i, (p1, s1, c1) in enumerate(said):
        for p2, s2, c2 in said[i + 1:]:
            if p1 != p2 and plain(s1) != plain(s2) and len(c1 & c2) / min(len(c1), len(c2)) >= 0.8:
                out.append(Finding(label, 0, "answer", f'same idea twice: "{s1[:50]}" / "{s2[:50]}"',
                                   "keep the clearer one"))
                paraphrased += [plain(s1), plain(s2)]
    paragraphs = [plain(p).split() for p in re.split(r"\n\s*\n", body) if p.strip()]
    owners = defaultdict(set)
    for index, para in enumerate(paragraphs):
        for i in range(len(para) - 7):
            owners[" ".join(para[i:i + 8])].add(index)
    repeated = [s for s, where in owners.items() if len(where) >= 2 and not s.isdigit()]
    reported = []
    for shingle in repeated:
        inside_sentence = (any(shingle in s for s, c in sentences.items() if c >= 2)
                           or any(shingle in s for s in paraphrased))
        if not inside_sentence and not any(shingle[:30] in r or r[:30] in shingle for r in reported):
            reported.append(shingle)
    for shingle in reported[:3]:
        out.append(Finding(label, 0, "answer", f'passage repeated in different paragraphs: "{shingle}"',
                           "keep one occurrence"))
    return out

