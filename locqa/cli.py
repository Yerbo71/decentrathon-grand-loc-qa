"""locqa — localization QA for Flutter ARB files.

  locqa check --source intl_ru.arb --target intl_kk.arb --glossary glossary.csv --out report.json
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import typer

from . import arb, decide, judge, llm, profiles, report
from .checks import hints as hintmod, rules
from .model import Finding

app = typer.Typer(add_completion=False, help="Localization QA for Flutter ARB files.")

SEV_RANK = {"high": 0, "medium": 1, "low": 2}


def load_glossary(path: Optional[Path]) -> list[dict]:
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def glossary_text(rows: list[dict]) -> str:
    return "\n".join(f"- {r.get('ru')} → {r.get('kk')}" + (f" (нельзя: {r['forbidden_kk']})" if r.get("forbidden_kk") else "")
                     + (f"; {r['comment']}" if r.get("comment") else "") for r in rows)


def check_glossary(ctx: rules.Context, rows: list[dict]) -> list[Finding]:
    """Forbidden renderings from the glossary, only where the source uses the term."""
    out = []
    for r in rows:
        ru_term = (r.get("ru") or "").strip()
        bad = [w.strip() for w in (r.get("forbidden_kk") or "").split(";") if w.strip()]
        if not ru_term or not bad:
            continue
        stem = ru_term.lower()[: max(4, len(ru_term) - 2)]
        for k, s in ctx.source.strings.items():
            t = ctx.target.strings.get(k) or ""
            if stem not in s.lower():
                continue
            words = [w.lower() for w in rules.WORD.findall(t)]
            for b in bad:
                bl = b.lower()
                hit = bl in t.lower() if " " in bl else any(
                    w == bl or (w.startswith(bl) and w[len(bl):] in ctx.profile.case_suffixes) for w in words)
                if hit:
                    out.append(Finding(k, "glossary_violation", "medium", s, t,
                                       f"«{ru_term}» по глоссарию переводится как «{r.get('kk')}», а здесь «{b}»"
                                       + (f" ({r['comment']})" if r.get("comment") else ""),
                                       None, 0.85, detector="rules.glossary"))
    return out


@app.command()
def check(
    source: Path = typer.Option(..., help="Source ARB (template), e.g. intl_ru.arb"),
    target: Path = typer.Option(..., help="Translation ARB to check, e.g. intl_kk.arb"),
    glossary: Optional[Path] = typer.Option(None, help="glossary.csv: ru,kk,en,forbidden_kk,comment"),
    out: Path = typer.Option(Path("report.json"), help="Findings in the schema from the task"),
    html: Optional[Path] = typer.Option(None, help="Human-readable report (default: next to --out)"),
    ref: list[Path] = typer.Option([], help="Other translations used as extra evidence (repeatable): --ref intl_en.arb"),
    profile: Optional[str] = typer.Option(None, help="Language profile (kk, uz, ...); default: target @@locale"),
    use_llm: bool = typer.Option(False, "--llm", help="LLM review of meaning/terms via OpenRouter (needs OPENROUTER_API_KEY)"),
    decide_model: Optional[str] = typer.Option(None, "--decide", help="Decision model that verifies findings, e.g. typesafe/jev-1.14"),
    min_confidence: float = typer.Option(0.6, help="Findings below this go to qa_queue.json instead of the report"),
    fail_on: Optional[str] = typer.Option(None, help="Exit 1 if there are findings of this severity or worse (high|medium|low)"),
    md_summary: Optional[Path] = typer.Option(None, help="Append a Markdown summary (e.g. $GITHUB_STEP_SUMMARY)"),
):
    """Check a translation against the source and write report.json + report.html."""
    src, tgt = arb.load(source), arb.load(target)
    refs = {(r_ := arb.load(p)).locale or p.stem: r_ for p in ref}
    prof = profiles.get(profile or tgt.locale)
    rows = load_glossary(glossary)
    ctx = rules.Context(src, tgt, prof, refs)

    findings = rules.run(ctx) + check_glossary(ctx, rows)
    hint_list = hintmod.run(ctx, rows)
    source_notes, client, llm_reports = [], None, []

    if use_llm or decide_model:
        client = llm.Client()
        if not client.available:
            typer.secho("OPENROUTER_API_KEY не задан: LLM-проверка пропущена, работают только правила", fg="yellow", err=True)
            use_llm, decide_model = False, None
    if use_llm:
        llm_f, llm_reports = judge.run_judge(client, src, tgt, refs, "v3", glossary_text(rows), findings, hint_list)
        findings += llm_f
        source_notes = [n for r in llm_reports for n in r.source_notes]
    if decide_model:
        _verify(client, decide_model, findings, src, tgt, refs)

    findings = _dedupe(findings)
    report_f = [f for f in findings if f.confidence >= min_confidence]
    queue = [f for f in findings if f.confidence < min_confidence]
    report_f.sort(key=lambda f: (SEV_RANK[f.severity], -f.confidence, f.key))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([f.to_report() for f in report_f], ensure_ascii=False, indent=1), encoding="utf-8")
    qa_path = out.with_name("qa_queue.json")
    qa_path.write_text(json.dumps([dict(f.to_report(), detector=f.detector) for f in queue], ensure_ascii=False, indent=1),
                       encoding="utf-8")
    notes_path = out.with_name("source_notes.json")
    notes_path.write_text(json.dumps(source_notes, ensure_ascii=False, indent=1), encoding="utf-8")
    structure = _structure(src, tgt)
    summary = {
        "source": str(source), "target": str(target), "locale": tgt.locale, "profile": prof.code,
        "strings_source": len(src.strings), "strings_target": len(tgt.strings),
        "findings": len(report_f), "qa_queue": len(queue), "source_notes": len(source_notes),
        "by_type": dict(Counter(f.issue_type for f in report_f)), "by_severity": dict(Counter(f.severity for f in report_f)),
        "by_detector": dict(Counter(f.detector.split(".")[0] for f in report_f)),
        "structure": structure,
        "llm": {"judge": use_llm, "decide_model": decide_model,
                "usage": client.usage.as_dict() if client else None,
                "failed_batches": [(r.batch_id, r.error) for r in llm_reports if not r.ok]},
    }
    out.with_name("summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    html_path = html or out.with_suffix(".html")
    report.write_html(html_path, report_f, queue, source_notes, summary)

    if md_summary:
        _write_md(md_summary, target, report_f, queue, summary)
    typer.echo(f"{len(report_f)} findings → {out}  |  QA queue: {len(queue)} → {qa_path}  |  HTML: {html_path}")
    typer.echo("  " + ", ".join(f"{t}: {n}" for t, n in Counter(f.issue_type for f in report_f).most_common()))
    if client:
        u = client.usage.as_dict()
        typer.echo(f"  LLM: {u['calls']} calls ({u['cached_calls']} cached), ${u['cost_usd']}")
    if fail_on and any(SEV_RANK[f.severity] <= SEV_RANK[fail_on] for f in report_f):
        raise typer.Exit(1)


def _write_md(path: Path, target: Path, findings: list[Finding], queue: list[Finding], summary: dict) -> None:
    sev = Counter(f.severity for f in findings)
    lines = [f"### locqa: `{target.name}` — {len(findings)} findings "
             f"(high {sev.get('high', 0)}, medium {sev.get('medium', 0)}, low {sev.get('low', 0)}; QA queue {len(queue)})", "",
             "| type | count |", "|---|---|"]
    lines += [f"| {t} | {n} |" for t, n in Counter(f.issue_type for f in findings).most_common()]
    top = [f for f in findings if f.severity == "high"][:20]
    if top:
        cell = lambda x: (x or "").replace("|", "\\|").replace("\n", " ")[:80]
        lines += ["", "<details><summary>High severity (first 20)</summary>", "", "| key | type | target | evidence |", "|---|---|---|---|"]
        lines += [f"| `{f.key}` | {f.issue_type} | {cell(f.target)} | {cell(f.evidence)} |" for f in top]
        lines += ["", "</details>"]
    st = summary["structure"]
    if st["keys_invalid_for_gen_l10n"] or st["duplicate_keys_with_different_values"]:
        lines += ["", f"Structure: duplicate keys with different values: {len(st['duplicate_keys_with_different_values'])}; "
                      f"keys gen-l10n rejects: {', '.join(st['keys_invalid_for_gen_l10n']) or 'none'}"]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n\n")


def _verify(client, model: str, findings: list[Finding], src, tgt, refs):
    """Second opinion from a decision model: P(real) becomes the finding's confidence; type disagreement is noted."""
    jobs = [(i, decide._state(f, src, tgt, refs), decide.verify_questions(f)) for i, f in enumerate(findings)
            if not f.detector.startswith(("rules.untranslated", "rules.placeholders"))]  # mechanical facts need no vote
    for i, ans, err in decide.run_many(client, model, jobs):
        if ans is None:
            continue
        f = findings[i]
        p = decide.p_true(ans, "is_real")
        t, tp = decide.top_choice(ans, "issue_type")
        if p is not None:
            f.confidence = round((f.confidence + p) / 2, 3)
            f.evidence += f" [проверка {model}: P(ошибка)={p:.2f}"
            if t and t != f.issue_type and t != "none" and (tp or 0) > 0.6:
                f.evidence += f"; модель считает тип {t}"
            f.evidence += "]"


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen, out = {}, []
    for f in findings:
        k = (f.key, f.issue_type)
        if k in seen:
            prev = seen[k]
            if f.confidence > prev.confidence:
                out[out.index(prev)] = f
                seen[k] = f
            continue
        seen[k] = f
        out.append(f)
    return out


def _structure(src: arb.ArbFile, tgt: arb.ArbFile) -> dict:
    """File-level facts that are not per-string issue types but matter for the build."""
    import re
    bad_names = [k for k in src.strings if not re.fullmatch(r"[a-z$][a-zA-Z0-9_$]*", k)]
    return {
        "duplicate_keys_target": {k: len(v) for k, v in tgt.duplicates.items()},
        "duplicate_keys_with_different_values": sorted(k for k, v in tgt.duplicates.items() if len({json.dumps(e.value, ensure_ascii=False) for e in v}) > 1),
        "extra_keys_in_target": sorted(set(tgt.strings) - set(src.strings)),
        "keys_invalid_for_gen_l10n": bad_names,
    }


@app.command()
def suggest(
    report_path: Path = typer.Option(Path("out/kk/report.json"), "--report", help="report.json from `locqa check`"),
    source: Path = typer.Option(..., help="Source ARB"),
    target: Path = typer.Option(..., help="Checked translation ARB"),
    glossary: Optional[Path] = typer.Option(None, help="glossary.csv"),
    ref: list[Path] = typer.Option([], help="Reference translations (repeatable)"),
    out: Path = typer.Option(Path("out/kk/review_queue.json"), help="Review queue for `locqa review`"),
    prefer_model: Optional[str] = typer.Option(None, "--prefer", help="Decision model for P(native prefers fix), e.g. nace-ai/drex-max"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Do not ask the LLM for missing fixes"),
):
    """Propose fixes for findings without one, gate them automatically and build the review queue."""
    import os
    from . import suggest as sg
    src, tgt = arb.load(source), arb.load(target)
    refs = {(r_ := arb.load(p)).locale or p.stem: r_ for p in ref}
    ctx = rules.Context(src, tgt, profiles.get(tgt.locale), refs)
    rows = load_glossary(glossary)
    forbidden = [(r["ru"], w.strip()) for r in rows for w in (r.get("forbidden_kk") or "").split(";") if w.strip()]
    findings = sg.load_findings(report_path)
    client, fixes = llm.Client(), {}
    if not no_llm and client.available:
        fixes = sg.propose_fixes(client, findings, ctx, glossary_text(rows),
                                 model=os.environ.get("LOCQA_FIX_MODEL") or llm.DEFAULT_FIX_MODEL)
    queue = sg.build_queue(findings, fixes, ctx, forbidden)
    if prefer_model and client.available:
        scores = sg.prefer_scores(client, prefer_model, [(c["key"], c["suggestion"]) for c in queue if c["suggestion"]], ctx)
        queue = sg.build_queue(findings, fixes, ctx, forbidden, scores)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8")
    ready = sum(1 for c in queue if c["suggestion"])
    typer.echo(f"{len(queue)} cards → {out}: {ready} with a gated fix, {len(queue) - ready} need the reviewer to write one")
    if client.available:
        typer.echo(f"  LLM: ${client.usage.as_dict()['cost_usd']}")


@app.command()
def review(
    queue: Path = typer.Option(Path("out/kk/review_queue.json"), help="Queue from `locqa suggest`"),
    out: Path = typer.Option(Path("out/kk/review.html"), help="Single-file review page"),
):
    """Render the review page for the native speaker (works offline; progress is kept in the browser)."""
    import hashlib
    from jinja2 import Environment, FileSystemLoader
    data = json.loads(queue.read_text(encoding="utf-8"))
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=False)
    qjson = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = env.get_template("review.html").render(queue_json=qjson, queue_id=hashlib.md5(qjson.encode()).hexdigest()[:8])
    out.write_text(html, encoding="utf-8")
    typer.echo(f"{len(data)} cards → {out} (open in a browser; export decisions.jsonl when done)")


@app.command("apply")
def apply_cmd(
    source: Path = typer.Option(..., help="Source ARB"),
    target: Path = typer.Option(..., help="Original translation ARB"),
    decisions: Path = typer.Option(..., help="decisions.jsonl exported from the review page"),
    out: Path = typer.Option(Path("out/kk/intl_kk_fixed.arb"), help="Corrected ARB"),
):
    """Write the corrected ARB with only human-approved changes, each flagged with x-locqa.reviewed_by_human."""
    from . import apply as ap
    src, tgt = arb.load(source), arb.load(target)
    entries, stats = ap.apply(src, tgt, ap.load_decisions(decisions))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(arb.dumps(entries), encoding="utf-8")
    out.with_suffix(".apply.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    typer.echo(f"{len(stats['changed'])} strings changed → {out}; skipped {len(stats['skipped'])}; "
               f"duplicate keys collapsed: {len(stats['duplicates_collapsed'])}")


@app.command()
def version():
    """Print version."""
    from importlib.metadata import version as v
    typer.echo(v("locqa"))


if __name__ == "__main__":
    app()
