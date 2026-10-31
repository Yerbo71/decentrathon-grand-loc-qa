"""ARB parsing that keeps what a plain json.load loses: order, repeated @@comment sections, duplicate keys."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Entry:
    key: str
    value: object
    section: str | None
    line: int
    index: int


@dataclass
class ArbFile:
    path: str
    locale: str | None
    entries: list[Entry]
    strings: dict[str, str] = field(default_factory=dict)  # effective value: last occurrence wins, like jsonDecode
    meta: dict[str, dict] = field(default_factory=dict)
    duplicates: dict[str, list[Entry]] = field(default_factory=dict)

    def section_of(self, key: str) -> str | None:
        e = self.entry(key)
        return e.section if e else None

    def entry(self, key: str) -> Entry | None:
        return self._by_key.get(key)

    def neighbors(self, key: str, radius: int = 2) -> list[Entry]:
        e = self.entry(key)
        if not e:
            return []
        lo, hi = max(0, e.index - radius), e.index + radius + 1
        return [x for x in self.entries[lo:hi] if x.key != key and isinstance(x.value, str) and not x.key.startswith("@")]

    def __post_init__(self):
        self._by_key: dict[str, Entry] = {}
        occ: dict[str, list[Entry]] = {}
        for e in self.entries:
            if e.key == "@@comment" or e.key.startswith("@@"):
                continue
            if e.key.startswith("@") and isinstance(e.value, dict):
                self.meta[e.key[1:]] = e.value
                continue
            occ.setdefault(e.key, []).append(e)
            if isinstance(e.value, str):
                self.strings[e.key] = e.value
                self._by_key[e.key] = e
        self.duplicates = {k: v for k, v in occ.items() if len(v) > 1}


def _line_index(text: str, keys: list[str]) -> list[int]:
    """Line number of each top-level key occurrence, scanning forward so duplicates map to distinct lines."""
    lines, pos = [], 0
    for k in keys:
        m = re.compile(r'"' + re.escape(json.dumps(k, ensure_ascii=False)[1:-1]) + r'"\s*:').search(text, pos)
        if m:
            pos = m.end()
            lines.append(text.count("\n", 0, m.start()) + 1)
        else:
            lines.append(0)
    return lines


def parse(text: str, path: str = "<string>") -> ArbFile:
    pairs = json.loads(text.lstrip("\ufeff"), object_pairs_hook=lambda p: p)
    if not isinstance(pairs, list):
        raise ValueError(f"{path}: top level of an ARB file must be an object")
    lines = _line_index(text, [k for k, _ in pairs])
    entries, section = [], None
    for i, ((k, v), ln) in enumerate(zip(pairs, lines)):
        if k == "@@comment" and isinstance(v, str):
            section = v
        entries.append(Entry(k, _to_obj(v), section, ln, i))
    locale = next((e.value for e in entries if e.key == "@@locale" and isinstance(e.value, str)), None)
    return ArbFile(path, locale, entries)


def load(path: str | Path) -> ArbFile:
    p = Path(path)
    return parse(p.read_text(encoding="utf-8"), str(p))


def _to_obj(v):
    if isinstance(v, list) and all(isinstance(x, tuple) and len(x) == 2 for x in v):
        return {k: _to_obj(x) for k, x in v}
    return v


_PUA = re.compile("[\ue000-\uf8ff]")


def dumps(entries: list[tuple[str, object]]) -> str:
    """Serialize ordered (key, value) pairs; repeated @@comment keys are allowed and kept."""
    out = ["{"]
    for i, (k, v) in enumerate(entries):
        val = json.dumps(v, ensure_ascii=False, indent=2).replace("\n", "\n  ")
        val = _PUA.sub(lambda m: f"\\u{ord(m.group()):04x}", val)
        out.append(f"  {json.dumps(k, ensure_ascii=False)}: {val}{',' if i < len(entries) - 1 else ''}")
    out.append("}")
    return "\n".join(out) + "\n"
