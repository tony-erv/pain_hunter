import logging
logging.basicConfig(level=logging.INFO, 
                    format="%(asctime)s %(levelname)s %(message)s")

from database import init_db
from agent import generate_queries, search_all, hunt_pains, format_digest

init_db()


print("\n=== ТЕСТ 3: Полный hunt_pains ===")
pains = hunt_pains("cybersecurity", count=3)
messages = format_digest(pains)
for msg in messages:
    print("\n" + msg)
    print("---")

