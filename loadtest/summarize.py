import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "results" / "series"
PREFIXES = tuple(sys.argv[2:])


def k6_row(label: str) -> dict | None:
    path = DIR / f"{label}-summary.json"
    if not path.exists():
        return None
    m = json.loads(path.read_text(encoding="utf-8"))["metrics"]

    def p(name: str, q: str) -> float | None:
        return m.get(name, {}).get(q)

    failed = m["http_req_failed"]
    return {
        "rps": m["http_reqs"]["rate"],
        "p50": p("http_req_duration", "med"),
        "p95": p("http_req_duration", "p(95)"),
        "p99": p("http_req_duration", "p(99)"),
        "join_p95": p("http_req_duration{name:join}", "p(95)"),
        "me_p95": p("http_req_duration{name:me}", "p(95)"),
        "errors_pct": failed["value"] * 100,
    }


def system_row(label: str) -> dict:
    path = DIR / f"{label}-system.csv"
    out = {"db_active_max": None, "db_idle_in_tx_max": None, "cpu_max": None, "cpu_avg": None}
    if not path.exists():
        return out
    rows = list(csv.DictReader(path.open(encoding="utf-8")))

    def col(name: str) -> list[float]:
        values = []
        for r in rows:
            try:
                values.append(float(r[name]))
            except (ValueError, TypeError, KeyError):
                pass
        return values

    out["db_active_max"] = max(col("db_active"), default=None)
    out["db_idle_in_tx_max"] = max(col("db_idle_in_tx"), default=None)
    cpu = col("web_cpu_percent")
    out["cpu_max"] = max(cpu, default=None)
    out["cpu_avg"] = statistics.mean(cpu) if cpu else None
    return out


def fmt(values: list[float], digits: int = 0) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    med = statistics.median(values)
    return f"{med:.{digits}f} ({min(values):.{digits}f}-{max(values):.{digits}f})"


groups: dict[str, list[dict]] = defaultdict(list)
for summary in sorted(DIR.glob("*-summary.json")):
    label = summary.name.removesuffix("-summary.json")
    group = label.rsplit("-", 1)[0] if label.rsplit("-", 1)[-1].isdigit() else label
    if PREFIXES and not group.startswith(PREFIXES):
        continue
    row = k6_row(label)
    if row:
        groups[group].append({**row, **system_row(label)})

print(
    "| группа | прогонов | RPS | p50, мс | p95, мс | p99, мс | `me` p95 | `join` p95 "
    "| ошибки, % | CPU max, % | БД active max | БД idle in tx max |"
)
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for group, rows in sorted(groups.items()):
    cells = [
        fmt([r["rps"] for r in rows], 1),
        fmt([r["p50"] for r in rows]),
        fmt([r["p95"] for r in rows]),
        fmt([r["p99"] for r in rows]),
        fmt([r["me_p95"] for r in rows]),
        fmt([r["join_p95"] for r in rows]),
        fmt([r["errors_pct"] for r in rows], 2),
        fmt([r["cpu_max"] for r in rows]),
        fmt([r["db_active_max"] for r in rows]),
        fmt([r["db_idle_in_tx_max"] for r in rows]),
    ]
    print(f"| {group} | {len(rows)} | " + " | ".join(cells) + " |")
print("\nЯчейка: медиана (минимум-максимум) по повторам.")
