"""Validate ARB files with the real `flutter gen-l10n`.

Usage: python tools/l10n_check/validate.py <template ru.arb> <target.arb>...

Keys that gen-l10n rejects as Dart method names (e.g. `UNeedHelp`) already exist in the studio's source
file, so they are dropped from the throwaway copies and reported instead of failing the whole run.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLUTTER = os.environ.get("FLUTTER") or shutil.which("flutter") or r"C:\tools\flutter\bin\flutter.bat"


def valid_name(k: str) -> bool:
    return bool(k) and k[0] != "_" and not re.search(r"[^a-zA-Z_$\d]", k) and not k[0].isupper() and not k[0].isdigit()


def sanitized_copy(src: Path, dst: Path, drop: set[str]) -> str:
    pairs = json.loads(src.read_text(encoding="utf-8"), object_pairs_hook=lambda p: p)
    keep = [(k, v) for k, v in pairs if k.lstrip("@") not in drop]
    body = ",\n".join(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(_obj(v), ensure_ascii=False)}" for k, v in keep)
    dst.write_text("{\n" + body + "\n}\n", encoding="utf-8")
    return dict(pairs).get("@@locale")


def _obj(v):
    if isinstance(v, list) and all(isinstance(x, tuple) for x in v):
        return {k: _obj(x) for k, x in v}
    return v


def main(template: str, *targets: str) -> int:
    pairs = json.loads(Path(template).read_text(encoding="utf-8"), object_pairs_hook=lambda p: p)
    drop = {k for k, _ in pairs if not k.startswith("@") and not valid_name(k)}
    if drop:
        print(f"skipping {len(drop)} key(s) that gen-l10n rejects in the template itself: {sorted(drop)}")
    out = HERE / "lib" / "l10n"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    sanitized_copy(Path(template), out / "intl_ru.arb", drop)
    for t in targets:
        tmp = out / "tmp.arb"
        loc = sanitized_copy(Path(t), tmp, drop)
        tmp.rename(out / f"intl_{loc}.arb")
    r = subprocess.run([FLUTTER, "gen-l10n"], cwd=HERE, capture_output=True, text=True, encoding="utf-8", shell=os.name == "nt")
    print((r.stdout + r.stderr).strip())
    print("gen-l10n OK" if r.returncode == 0 else f"gen-l10n FAILED (exit {r.returncode})")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
