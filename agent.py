import json
import logging
import time
from openai import OpenAI
from ddgs import DDGS
from config import cfg
from database import is_seen, mark_seen
from scraper import enrich_results

logger = logging.getLogger(__name__)
client = OpenAI(api_key=cfg.OPENAI_API_KEY)

# Step 1: Generate search queries for the niche

def generate_queries(niche: str) -> list[str]:
    """
    Use LLM to generate specific search queries for the given niche.
    """
    logger.info("Generating queries for niche: %s", niche)
    try:
        resp = client.chat.completions.create(
            model=cfg.MODEL,
            messages=[
                {"role": "system", "content": cfg.QUERY_GEN_PROMPT},
                {"role": "user",   "content": f"Niche: {niche}"}
            ],
            temperature=0.7,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()
        queries = json.loads(raw)
        logger.info("Generated %d queries", len(queries))
        return queries[:5]
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Query gen failed: %s — using templates", e)
        # Fallback to static templates if LLM fails or returns invalid JSON
        return [t.format(niche=niche) for t in cfg.QUERY_TEMPLATES]
    
# Step 2: Search and extract pains

def search_all(queries: list[str]) -> list[dict]:
    """
    For each query, search DuckDuckGo and collect results that are not seen before.
    """
    pool = []
    seen_in_batch = set()  # to avoid duplicates within the same batch of queries

    for query in queries:
        logger.info("Searching: %s", query)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(
                    query,
                    max_results=cfg.MAX_SEARCH_RESULTS
                ))
            for r in results:
                url = r.get("href", "")
                if url and url not in seen_in_batch and not is_seen(url):
                    pool.append(r)
                    seen_in_batch.add(url)
            time.sleep(1)  # be nice to search engines and avoid rate limits
        except Exception as e:
            logger.warning("Search failed for query '%s': %s", query, e)
            continue

    logger.info("Search pool: %d fresh results", len(pool))
    return pool

# Step 3: Analyze pains with LLM

def analyze_pains(pool: list[dict], niche: str) -> list[dict]:
    """
    Use LLM to analyze search results and extract pains with scores.
    """
    if not pool:
        logger.warning("Empty pool — nothing to analyze")
        return []

    # Prepare context for LLM: we will give it the top 15 results with title, URL and snippet
    context = ""
    for i, r in enumerate(pool[:15], 1):  # limit to top 15 results for context
        context += (
            f"[{i}] {r.get('title', '')}\n"
            f"URL: {r.get('href', '')}\n"
            f"Snippet: {r.get('body', '')[:300]}\n\n"
        )

    logger.info("Analyzing %d results for niche: %s", len(pool[:15]), niche)
    try:
        resp = client.chat.completions.create(
            model=cfg.MODEL,
            messages=[
                {"role": "system", "content": cfg.PAIN_ANALYSIS_PROMPT},
                {"role": "user", "content":
                    f"Niche: {niche}\n\nSearch results:\n{context}"}
            ],
            temperature=0.2,  # low temperature for more factual analysis
            max_tokens=1500,
        )
        raw = resp.choices[0].message.content.strip()
        logger.debug("Raw LLM response: %s", raw[:500])  # log first 500 chars for debugging

        # Delete markdown code block if LLM wrapped the JSON in ```json ... ``` for better formatting
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        pains = json.loads(raw)

        # Add niche to each pain for later filtering and stats
        for p in pains:
            p.setdefault("niche", niche)

        # Sort pains by score (frequency + emotion) for better prioritization
        pains.sort(key=lambda x: x.get("score", 0), reverse=True)
        logger.info("Found %d pains", len(pains))
        return pains

    except (json.JSONDecodeError, Exception) as e:
        logger.error("Analysis failed: %s", e)
        return []
    
# Main function to run the agent for a given niche:

def hunt_pains(niche: str, count: int = 5) -> list[dict]:
    """
    Full cycle: generate queries, search, analyze and return top pains for the niche.

    Args:
        niche: description, example "accountants Canada"
        count: how many pains to return (default 5)

    Returns:
        List of lists: title, quote, source, niche,
        frequency, emotion, monetizable, score
    """
    logger.info("=== hunt_pains started: '%s' ===", niche)

    # Step 1 — generate queries
    queries = generate_queries(niche)

    # Step 2 — search and collect results
    pool = search_all(queries)
    pool = enrich_results(pool)  # add title and snippet if missing, for better analysis

    if not pool:
        logger.warning("No results found for '%s'", niche)
        return []

    # Step 3 — analyze pains with LLM
    pains = analyze_pains(pool, niche)

    # Mark all URLs in the pool as seen to avoid future duplicates, even if they didn't yield pains
    for r in pool:
        if url := r.get("href"):
            mark_seen(url)

    result = pains[:count]
    logger.info("=== hunt_pains done: %d pains returned ===", len(result))
    return result


# Formatting for Telegram message:

def score_to_stars(score: float) -> str:
    """Convert a score (1.0 to 5.0) to a star rating string."""
    stars = round(score)
    return "⭐" * stars + "☆" * (5 - stars)

def format_pain(pain: dict, index: int = 1) -> str:
    score       = pain.get("score", 0)
    stars       = score_to_stars(score)
    money       = "✅ Решается кодом" if pain.get("monetizable") else "❌ Сложно монетизировать"
    freq        = "⭐" * pain.get("frequency", 3)
    emotion     = "🔥" * pain.get("emotion", 3)
    source      = pain.get("source", "")
    quote       = pain.get("quote", "")
    niche       = pain.get("niche", "")
    title       = pain.get("title", "No title")
    root_cause  = pain.get("root_cause", "")
    solution    = pain.get("solution_hint", "")

    text = (
        f"🔴 <b>Pain #{index} — {title}</b>\n"
        f"Ниша: {niche}\n\n"
        f'💬 <i>"{quote}"</i>\n\n'
        f"📊 Рейтинг: {stars}\n"
        f"   Частота: {freq}\n"
        f"   Эмоции:  {emotion}\n"
        f"   {money}\n"
    )

    if root_cause:
        text += f"\n🧠 <b>Почему существует:</b> {root_cause}\n"

    if solution:
        text += f"💡 <b>Как решить:</b> {solution}\n"

    if source:
        text += f'\n🔗 <a href="{source}">Источник</a>'

    return text

def format_digest(pains: list[dict]) -> list[str]:
    """
    Format a list of pains into a Telegram message digest.
    """
    if not pains:
        return ["😔 No fresh pain points found. Try another niche or check back tomorrow."]
    messages = ["🎯 Pain Hunter — Search Results\n"
                 f"Found {len(pains)} opportunities\n"
                 "——————————————————"]
    for i, pain in enumerate(pains, 1):
        messages.append(format_pain(pain, i))
    return messages