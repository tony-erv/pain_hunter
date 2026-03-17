import logging
logging.basicConfig(level=logging.INFO, 
                    format="%(asctime)s %(levelname)s %(message)s")

from database import init_db
from agent import generate_queries, search_all, hunt_pains, format_digest

init_db()

print("\n=== ТЕСТ 1: Генерация запросов ===")
queries = generate_queries("network engineers")
for q in queries:
    print(" •", q)

print("\n=== ТЕСТ 2: Поиск ===")
pool = search_all(queries[:2])
print(f"Найдено результатов: {len(pool)}")
for r in pool[:3]:
    print(f" • {r['title'][:60]}")

print("\n=== ТЕСТ 3: Полный hunt_pains ===")
pains = hunt_pains("freelance", count=3)
messages = format_digest(pains)
for msg in messages:
    print("\n" + msg)
    print("---")

