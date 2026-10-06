"""Street names as police lists write them, and how to compare them with the map.

Lists shorten names: "Avda. Padre Isla", "Pº Salamanca", "Fdez. Ladreda",
"José M. Suárez G.". ``expand`` writes the abbreviations out ("Avenida",
"Paseo", "Fernández"); ``same_street`` then compares a written name with a
mapped one, word by word, letting an initial stand for a word ("José M. Suárez
G." is "José María Suárez González") and ignoring the small words and the
street type, which lists drop or change ("Pendón de Baeza" is mapped as "Calle
Pendón de Baeza").
"""

from __future__ import annotations

import re

from .streets import STREET_TYPES, fold

# A street type at the start of a name. "Pº" also comes as "P°" and "Po.".
_TYPES = [
    (r"Avda?\.?|Av\.?", "Avenida"),
    (r"C/", "Calle"),
    (r"Cno\.?", "Camino"),
    (r"Ctra\.?", "Carretera"),
    (r"Gta\.?|Glta\.?", "Glorieta"),
    (r"P[º°]\.?|Po\.|Pso\.?", "Paseo"),
    (r"Pza\.?|Pl\.", "Plaza"),
    (r"Rda\.?", "Ronda"),
    (r"Trav\.?|Trva\.?", "Travesía"),
]
_LEADING_TYPE = [(re.compile(rf"^(?:{p})(?:\s+|(?<=/)|(?<=\.)(?=\S))", re.I), f) for p, f in _TYPES]

# A shortened word anywhere in a name, keyed by its folded form, with or without the dot.
WORDS = {
    "dr": "Doctor",
    "dra": "Doctora",
    "fdez": "Fernández",
    "fco": "Francisco",
    "gral": "General",
    "glez": "González",
    "gzlez": "González",
    "hdez": "Hernández",
    "hnos": "Hermanos",
    "ing": "Ingeniero",
    "mtnez": "Martínez",
    "ntra": "Nuestra",
    "pdte": "Presidente",
    "prof": "Profesor",
    "rguez": "Rodríguez",
    "sra": "Señora",
    "sta": "Santa",
    "sto": "Santo",
}
_WORD = re.compile(r"\b(\w+)\.?(?=\s|$)")
# Words a list may drop or add without naming another street.
SMALL_WORDS = {"de", "del", "la", "las", "los", "el", "y", "d"}


def expand(text: str) -> str:
    """'Pº Salamanca' -> 'Paseo Salamanca'; 'Fdez. Ladreda.' -> 'Fernández Ladreda'."""
    text = re.sub(r"[\s\xa0]+", " ", text).strip(" .,;")
    for pattern, full in _LEADING_TYPE:
        text, n = pattern.subn(full + " ", text, count=1)
        if n:
            break

    def word(m: re.Match[str]) -> str:
        return WORDS.get(fold(m.group(1)), m.group(0))

    text = _WORD.sub(word, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:]


def split(name: str) -> tuple[str | None, tuple[str, ...]]:
    """('avenida', ('padre', 'isla')) for 'Avenida del Padre Isla': the street
    type, if the name starts with one, and the other words but the small ones."""
    words = fold(name).split()
    kind = words[0] if words and words[0] in STREET_TYPES else None
    rest = words[1:] if kind else words
    return kind, tuple(w for w in rest if w not in SMALL_WORDS)


def same_words(written: tuple[str, ...], mapped: tuple[str, ...]) -> bool:
    """Word by word, an initial matching any word that starts with it; at least
    one word must match in full, so 'M. G.' matches nothing."""
    if not written or len(written) != len(mapped):
        return False
    full = False
    for a, b in zip(written, mapped, strict=True):
        if a == b:
            full = True
        elif not ((len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))):
            return False
    return full


def same_street(written: str, mapped: str) -> bool:
    """Whether a list's street name could be a mapped name, street type aside."""
    return same_words(split(written)[1], split(mapped)[1])
