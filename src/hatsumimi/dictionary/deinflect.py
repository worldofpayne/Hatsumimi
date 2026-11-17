"""Turn conjugated Japanese back into dictionary forms.

    書かなかった -> 書かない -> 書く          (past, then negative)
    食べられません -> 食べられる -> 食べる   (polite negative, then passive/potential)

Each candidate carries a set of word types it could be. A rule only applies
when the text ends with its suffix AND the candidate's types allow it, which
is what lets rules chain correctly: the ない of 書かない behaves like an
i-adjective, the られる of 食べられる like an ichidan verb, and so on.

These rules are written for Hatsumimi (not copied from Rikaikun/Yomitan).
"""

from __future__ import annotations

from dataclasses import dataclass

# Word types (bit flags)
INIT = 1 << 0   # the text as found: could be anything
V1 = 1 << 1     # ichidan verb (食べる)
V5 = 1 << 2     # godan verb (書く)
VS = 1 << 3     # する
VK = 1 << 4     # 来る
ADJ = 1 << 5    # i-adjective (高い), also ない / たい endings
TE = 1 << 6     # a te-form (書いて) - never a dictionary form itself
VSN = 1 << 7    # a noun that takes する (勉強)


@dataclass(frozen=True)
class Rule:
    suffix: str
    replace: str
    needs: int      # candidate must have one of these types
    gives: int      # type(s) of the result
    reason: str


@dataclass(frozen=True)
class Candidate:
    text: str
    types: int
    reasons: tuple[str, ...]  # outermost first, e.g. ("past", "negative")


POLITE = [("ます", "polite"), ("ません", "polite negative"), ("ました", "polite past"),
          ("ませんでした", "polite past negative"), ("ましょう", "polite volitional"),
          ("まして", "polite te-form"), ("ませんか", "polite question")]

# Godan rows: dictionary ending, i-stem, a-stem, e-stem, o-stem, te-form, past
GODAN = [
    ("う", "い", "わ", "え", "お", "って", "った"),
    ("く", "き", "か", "け", "こ", "いて", "いた"),
    ("ぐ", "ぎ", "が", "げ", "ご", "いで", "いだ"),
    ("す", "し", "さ", "せ", "そ", "して", "した"),
    ("つ", "ち", "た", "て", "と", "って", "った"),
    ("ぬ", "に", "な", "ね", "の", "んで", "んだ"),
    ("ぶ", "び", "ば", "べ", "ぼ", "んで", "んだ"),
    ("む", "み", "ま", "め", "も", "んで", "んだ"),
    ("る", "り", "ら", "れ", "ろ", "って", "った"),
]


def _rules() -> list[Rule]:
    R: list[Rule] = []
    add = lambda s, r, n, g, why: R.append(Rule(s, r, n, g, why))

    for u, i, a, e, o, te, ta in GODAN:
        for p, why in POLITE:
            add(i + p, u, INIT, V5, why)
        add(a + "ない", u, INIT | ADJ, V5, "negative")
        add(a + "ず", u, INIT, V5, "negative (-zu)")
        add(a + "ずに", u, INIT, V5, "without doing")
        add(a + "ぬ", u, INIT, V5, "negative (-nu)")
        add(ta, u, INIT, V5, "past")
        add(te, u, INIT | TE, V5, "te-form")
        add(ta + "ら", u, INIT, V5, "conditional (-tara)")
        add(ta + "り", u, INIT, V5, "-tari")
        add(e + "る", u, INIT | V1, V5, "potential")
        add(a + "れる", u, INIT | V1, V5, "passive")
        add(a + "せる", u, INIT | V1, V5, "causative")
        add(a + "せられる", u, INIT | V1, V5, "causative passive")
        add(a + "される", u, INIT | V1, V5, "causative passive")
        add(o + "う", u, INIT, V5, "volitional")
        add(e, u, INIT, V5, "imperative")
        add(e + "ば", u, INIT, V5, "conditional (-ba)")
        add(i + "たい", u, INIT | ADJ, V5, "want to")
        add(i + "ながら", u, INIT, V5, "while")
        add(i + "なさい", u, INIT, V5, "polite command")
        add(i + "そう", u, INIT, V5, "looks like")
        add(i + "すぎる", u, INIT | V1, V5, "too much")
        add(i + "やすい", u, INIT | ADJ, V5, "easy to")
        add(i + "にくい", u, INIT | ADJ, V5, "hard to")
    # 行く is irregular: 行って / 行った
    for stem in ("行", "い", "逝"):
        add(stem + "って", stem + "く", INIT | TE, V5, "te-form")
        add(stem + "った", stem + "く", INIT, V5, "past")
        add(stem + "ったら", stem + "く", INIT, V5, "conditional (-tara)")

    # Ichidan: stem + ending -> stem + る
    for p, why in POLITE:
        add(p, "る", INIT, V1, why)
    for s, n, why in [
        ("ない", INIT | ADJ, "negative"), ("ず", INIT, "negative (-zu)"), ("ずに", INIT, "without doing"),
        ("た", INIT, "past"), ("て", INIT | TE, "te-form"), ("たら", INIT, "conditional (-tara)"),
        ("たり", INIT, "-tari"), ("られる", INIT | V1, "passive / potential"),
        ("れる", INIT | V1, "potential (colloquial)"), ("させる", INIT | V1, "causative"),
        ("させられる", INIT | V1, "causative passive"), ("よう", INIT, "volitional"),
        ("ろ", INIT, "imperative"), ("よ", INIT, "imperative"), ("れば", INIT, "conditional (-ba)"),
        ("たい", INIT | ADJ, "want to"), ("ながら", INIT, "while"), ("なさい", INIT, "polite command"),
        ("そう", INIT, "looks like"), ("すぎる", INIT | V1, "too much"),
        ("やすい", INIT | ADJ, "easy to"), ("にくい", INIT | ADJ, "hard to"),
    ]:
        add(s, "る", n, V1, why)

    # Chaining helpers: progressive / completion / preparation, contracted or not
    # (ending, replacement, how the ending itself conjugates, meaning)
    for s, r, kind, why in [
        ("ている", "て", V1, "progressive"), ("てる", "て", V1, "progressive (casual)"),
        ("でいる", "で", V1, "progressive"), ("でる", "で", V1, "progressive (casual)"),
        ("てある", "て", V5, "resulting state"), ("ておく", "て", V5, "do in advance"),
        ("とく", "て", V5, "do in advance (casual)"), ("どく", "で", V5, "do in advance (casual)"),
        ("てしまう", "て", V5, "completion / regret"), ("でしまう", "で", V5, "completion / regret"),
        ("ちゃう", "て", V5, "completion (casual)"), ("じゃう", "で", V5, "completion (casual)"),
        ("てくる", "て", VK, "come to"), ("ていく", "て", V5, "go on"),
        ("てみる", "て", V1, "try"), ("てもらう", "て", V5, "have done"),
        ("てくれる", "て", V1, "do for me"), ("てあげる", "て", V1, "do for someone"),
    ]:
        add(s, r, INIT | kind, TE, why)
    for s, r, why in [("てください", "て", "please"), ("でください", "で", "please"),
                      ("ても", "て", "even if"), ("でも", "で", "even if"),
                      ("ては", "て", "if (-te wa)"), ("ちゃ", "て", "if (-cha)"), ("じゃ", "で", "if (-ja)")]:
        add(s, r, INIT, TE, why)

    # i-adjectives (and the ない / たい endings, which conjugate the same way)
    for s, why, n in [("かった", "past", INIT), ("くない", "negative", INIT | ADJ), ("くて", "te-form", INIT | TE),
                      ("く", "adverb", INIT), ("ければ", "conditional (-ba)", INIT),
                      ("かったら", "conditional (-tara)", INIT), ("さ", "noun (-sa)", INIT),
                      ("そう", "looks like", INIT), ("すぎる", "too much", INIT | V1),
                      ("くなる", "become", INIT | V5), ("かろう", "probably", INIT)]:
        add(s, "い", n, ADJ, why)

    # する and 来る
    for s, why, n in [("します", "polite", INIT), ("しません", "polite negative", INIT),
                      ("しました", "polite past", INIT), ("しませんでした", "polite past negative", INIT),
                      ("しましょう", "polite volitional", INIT), ("しない", "negative", INIT | ADJ),
                      ("した", "past", INIT), ("して", "te-form", INIT | TE), ("しよう", "volitional", INIT),
                      ("すれば", "conditional (-ba)", INIT), ("したら", "conditional (-tara)", INIT),
                      ("される", "passive", INIT | V1), ("させる", "causative", INIT | V1),
                      ("できる", "potential", INIT | V1), ("しろ", "imperative", INIT), ("せよ", "imperative", INIT),
                      ("したい", "want to", INIT | ADJ), ("せず", "negative (-zu)", INIT),
                      ("しながら", "while", INIT), ("しなさい", "polite command", INIT)]:
        add(s, "する", n, VS, why)
    add("する", "", INIT | VS, VSN, "+ suru")
    # 来る: kana forms, and the same with the kanji 来 in place of the first kana
    for s, why, n in [("きます", "polite", INIT), ("きません", "polite negative", INIT),
                      ("きました", "polite past", INIT), ("こない", "negative", INIT | ADJ),
                      ("きた", "past", INIT), ("きて", "te-form", INIT | TE), ("こよう", "volitional", INIT),
                      ("くれば", "conditional (-ba)", INIT), ("きたら", "conditional (-tara)", INIT),
                      ("こられる", "passive / potential", INIT | V1), ("こさせる", "causative", INIT | V1),
                      ("こい", "imperative", INIT), ("きたい", "want to", INIT | ADJ)]:
        add(s, "くる", n, VK, why)
        add("来" + s[1:], "来る", n, VK, why)
    return R


RULES = _rules()
_MAX_DEPTH = 6


def deinflect(text: str) -> list[Candidate]:
    """All plausible dictionary forms of `text`, including `text` itself."""
    out = [Candidate(text, INIT, ())]
    seen = {(text, INIT)}
    i = 0
    while i < len(out):
        c = out[i]
        i += 1
        if len(c.reasons) >= _MAX_DEPTH:
            continue
        for r in RULES:
            if not (c.types & r.needs) or not c.text.endswith(r.suffix):
                continue
            base = c.text[: len(c.text) - len(r.suffix)] + r.replace
            if not base or (base, r.gives) in seen:
                continue
            seen.add((base, r.gives))
            out.append(Candidate(base, r.gives, (r.reason,) + c.reasons))
    return out


def pos_matches(types: int, pos: list[str]) -> bool:
    """Does an entry with these JMdict parts of speech fit the candidate's types?"""
    if types & INIT:
        return True
    for p in pos:
        if types & V1 and p.startswith("v1"):
            return True
        if types & V5 and p.startswith("v5"):
            return True
        if types & VS and p in ("vs-i", "vs-s"):
            return True
        if types & VSN and p.startswith("vs"):
            return True
        if types & VK and p == "vk":
            return True
        if types & ADJ and p in ("adj-i", "adj-ix"):
            return True
    return False
