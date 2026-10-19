"""Estadisticas descriptivas de shipments.csv: productos, zonas y agrupacion por cluster."""
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime

path = sys.argv[1]
rows = list(csv.DictReader(open(path, encoding="utf-8")))
print("envios", len(rows))
print("producto", Counter(r["product"] for r in rows))
print("zona", Counter(r["zone"] for r in rows))
print("metal", sum(r["is_metal"] == "true" for r in rows))
by_cluster = defaultdict(list)
for r in rows:
    by_cluster[r["delivery_cluster_id"]].append(datetime.fromisoformat(r["available_at"]))
sizes = Counter(len(v) for v in by_cluster.values())
print("clusters", len(by_cluster), "tamanos", sorted(sizes.items())[:15])
gaps = []
for times in by_cluster.values():
    times.sort()
    gaps.extend((b - a).total_seconds() / 60 for a, b in zip(times, times[1:]))
gaps.sort()
if gaps:
    for p in (0.1, 0.25, 0.5, 0.75, 0.9):
        print(f"gap p{int(p*100)} min", round(gaps[int(p * (len(gaps) - 1))], 1))
    print("gaps<=45min", sum(g <= 45 for g in gaps), "de", len(gaps))
hours = Counter(datetime.fromisoformat(r["available_at"]).hour for r in rows)
print("por hora", sorted(hours.items()))
days = Counter(datetime.fromisoformat(r["available_at"]).date() for r in rows)
print("max/dia", max(days.values()), "min/dia", min(days.values()))
