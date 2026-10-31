"""Send the exact requests of a simulated cycle to a real model and store answers like the simulator did.

  python tools/sim/replay.py <from_cycle> <to_cycle> [--model anthropic/claude-sonnet-5.5]
Then: python tools/sim/sim.py evaluate <to_cycle>   (or sim_fix.py for fix cycles)
"""
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from locqa import llm  # noqa: E402

a = sys.argv[1:]
src, dst = ROOT / "out" / "sim" / a[0], ROOT / "out" / "sim" / a[1]
model = a[a.index("--model") + 1] if "--model" in a else None
(dst / "responses").mkdir(parents=True, exist_ok=True)
shutil.copytree(src / "requests", dst / "requests", dirs_exist_ok=True)
shutil.copy(src / "meta.json", dst / "meta.json")
client = llm.Client(model=model)


def one(p: Path):
    req = json.loads(p.read_text(encoding="utf-8"))
    sys_msg, user_msg = req["messages"][0]["content"], req["messages"][1]["content"]
    try:
        data = client.chat_json(sys_msg, user_msg, max_tokens=8000)
        (dst / "responses" / f"{p.stem}.txt").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p.stem, "ok"
    except Exception as e:  # noqa: BLE001
        return p.stem, f"ERROR {str(e)[:200]}"


with ThreadPoolExecutor(5) as ex:
    for name, status in ex.map(one, sorted((dst / "requests").glob("*.json"))):
        print(name, status)
print(json.dumps(client.usage.as_dict(), ensure_ascii=False))
