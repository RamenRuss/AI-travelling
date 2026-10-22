"""Прогон запросов через НАСТОЯЩЕГО агента и запись метрик (качество оценивается вручную).

    python -m eval.run_eval --queries eval/queries.csv --label v1
    python -m eval.run_eval --queries eval/queries.csv --label v2 --model openrouter/qwen/qwen3.8-27b --limit 5

Метрики (precision / recall / F1 / accuracy): python -m eval.metrics eval/results_v1.csv

Нужен SPIRIT_API_KEY. Файл запросов: CSV с колонкой `query` (остальные колонки копируются в результат)
или .txt, один запрос на строку. Результат: eval/results_<label>.csv
"""
import argparse
import csv
import sys
import time
from pathlib import Path

from app import config
from app.agent.llm import run_chat


def load_queries(path):
    if path.endswith(".csv"):
        text = Path(path).read_text(encoding="utf-8-sig")
        first = text.split("\n", 1)[0]
        delim = ";" if first.count(";") > first.count(",") else ","
        rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
        if not rows or "query" not in rows[0]:
            sys.exit("В CSV нужна колонка `query`")
        return [r for r in rows if (r.get("query") or "").strip()]
    return [{"query": l.strip()} for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("--label", default="v1", help="Название версии для таблицы сравнения")
    ap.add_argument("--model", default=None, help="Подменить модель на время прогона")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if args.model:
        config.MODEL = args.model
    if not config.API_KEY:
        sys.exit("Не задан SPIRIT_API_KEY (см. .env.example)")

    rows = load_queries(args.queries)[: args.limit]
    out_path = args.out or str(Path(__file__).parent / f"results_{args.label}.csv")
    extra = [k for k in rows[0] if k != "query"]
    fields = extra + ["query", "label", "model", "answer", "clarify", "tools_used", "tokens", "cost_usd", "time_sec", "error"]
    n_err = tot_tokens = tot_time = tot_cost = 0

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, r in enumerate(rows, 1):
            q = r["query"].strip()
            rec = {k: r.get(k, "") for k in extra}
            rec.update(query=q, label=args.label, model=config.MODEL, answer="", clarify="", tools_used="",
                       tokens=0, cost_usd=0, time_sec=0, error="")
            t0 = time.time()
            try:
                res = run_chat([{"role": "user", "content": q}])
                rec.update(answer=res["answer"], clarify=int(bool(res.get("clarify"))), tools_used=", ".join(res["tools_used"]),
                           tokens=res.get("tokens", 0), cost_usd=round(res.get("cost", 0), 6))
            except Exception as e:  # noqa: BLE001
                rec["error"] = str(e)
                n_err += 1
            rec["time_sec"] = round(time.time() - t0, 2)
            tot_tokens += rec["tokens"]; tot_time += rec["time_sec"]; tot_cost += rec["cost_usd"]
            w.writerow(rec); f.flush()  # прогон оборвётся — результаты не потеряются
            print(f"[{i}/{len(rows)}] {rec['time_sec']}с {rec['tokens']} ток. {'ОШИБКА' if rec['error'] else 'ok'}  {q[:60]}")

    n = len(rows)
    print(f"\nГотово: {out_path}")
    print(f"Запросов: {n}, ошибок: {n_err}, среднее время: {tot_time / n:.1f} с, "
          f"средние токены: {tot_tokens / n:.0f}, стоимость: ${tot_cost:.4f}")


if __name__ == "__main__":
    main()
