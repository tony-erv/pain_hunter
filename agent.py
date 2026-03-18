import json
import logging
import time
import httpx
from openai import OpenAI
from config import cfg
from database import is_seen, mark_seen
from scraper import enrich_results

logger = logging.getLogger(__name__)
client = OpenAI(api_key=cfg.OPENAI_API_KEY)

HEADERS = {
    "User-Agent": "PainHunter/1.0 (research tool; contact: tony.ervalson@gmail.com)",
    "Accept": "application/json",
}

# ── Step 1: Generate queries ──────────────────────────────────────────────────

def generate_queries(niche: str) -> list[dict]:
    """
    Ask LLM to return:
      - 3 Reddit queries, each with a specific subreddit to search within
      - 2 HN queries (plain keywords)
    Falls back to static templates if LLM fails.
    """
    logger.info("Generating queries for niche: %s", niche)
    try:
        resp = client.chat.completions.create(
            model=cfg.MODEL,
            messages=[
                {"role": "system", "content": cfg.QUERY_GEN_PROMPT},
                {"role": "user",   "content": f"Niche: {niche}"},
            ],
            temperature=0.7,
            max_tokens=500,
        )
        raw = resp.choices[0].message.content.strip()
        queries = json.loads(raw)
        logger.info("Generated %d queries: %s", len(queries), queries)
        return queries[:5]
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Query gen failed: %s — using fallback templates", e)
        return [t | {"query": t["query"].format(niche=niche)} for t in cfg.QUERY_TEMPLATES]


# ── Step 2: Search ────────────────────────────────────────────────────────────

def search_reddit(query: str, subreddit: str = "", limit: int = 15) -> list[dict]:
    """
    Search Reddit within a specific subreddit when provided.
    - If subreddit is given: hits /r/SUBREDDIT/search.json — zero off-topic noise
    - If no subreddit: falls back to global search (more noise, use sparingly)
    Skips true ghost posts (no upvotes AND no comments).
    """
    try:
        if subreddit:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {
                "q": query,
                "restrict_sr": "1",   # stay within the subreddit
                "sort": "relevance",
                "limit": limit,
                "t": "year",
            }
            logger.info("Reddit [r/%s]: %s", subreddit, query)
        else:
            url = "https://www.reddit.com/search.json"
            params = {"q": query, "sort": "relevance", "limit": limit, "t": "year"}
            logger.info("Reddit [global]: %s", query)

        resp = httpx.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=10,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.warning("Reddit search returned HTTP %d for %s", resp.status_code, url)
            return []

        posts = resp.json()["data"]["children"]
        results = []
        for p in posts:
            d = p["data"]
            if d.get("score", 0) < 2 and d.get("num_comments", 0) < 1:
                continue
            results.append({
                "title":        d.get("title", ""),
                "href":         f"https://www.reddit.com{d.get('permalink', '')}",
                "body":         d.get("selftext", "")[:500],
                "score":        d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "subreddit":    d.get("subreddit_name_prefixed", ""),
                "source_type":  "reddit",
            })
        logger.info(
            "Reddit [%s]: %d results after pre-filter",
            subreddit or "global", len(results),
        )
        return results
    except Exception as e:
        logger.warning("Reddit search failed: %s", e)
        return []


def search_hn(query: str, limit: int = 8) -> list[dict]:
    """
    Search Hacker News via the Algolia API — free, no key needed, very accurate.
    """
    try:
        resp = httpx.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": query, "tags": "story", "hitsPerPage": limit},
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        results = []
        for h in resp.json().get("hits", []):
            hn_url = (
                h.get("url")
                or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            )
            results.append({
                "title":        h.get("title", ""),
                "href":         hn_url,
                "body":         (h.get("story_text") or "")[:500],
                "score":        h.get("points", 0),
                "num_comments": h.get("num_comments", 0),
                "source_type":  "hn",
            })
        logger.info("HN: %d results for '%s'", len(results), query)
        return results
    except Exception as e:
        logger.warning("HN search failed: %s", e)
        return []


def search_all(queries: list[dict]) -> list[dict]:
    """
    Route each query to the right search engine.
    Reddit queries carry an optional 'subreddit' field for targeted search.
    queries: [{"query": str, "source": "reddit"|"hn", "subreddit": str}, ...]
    """
    pool: list[dict] = []
    seen_in_batch: set[str] = set()

    for q in queries:
        query     = q.get("query", "")
        source    = q.get("source", "reddit")
        subreddit = q.get("subreddit", "")

        if source == "reddit":
            results = search_reddit(query, subreddit=subreddit)
        else:
            results = search_hn(query)

        for r in results:
            url = r.get("href", "")
            if url and url not in seen_in_batch and not is_seen(url):
                pool.append(r)
                seen_in_batch.add(url)

        time.sleep(0.8)

    logger.info("Search pool: %d fresh unique results", len(pool))
    return pool


# ── Step 3: Two-stage LLM analysis ───────────────────────────────────────────

def quick_filter(pool: list[dict]) -> list[dict]:
    """
    Stage 1 — cheap trash-removal pass.
    Removes obvious non-pain content (news, jobs, tutorials).
    Logs discarded titles for tuning.
    Target pass rate: 50-70%.
    """
    if not pool:
        return []

    lines = []
    for i, r in enumerate(pool):
        lines.append(
            f"[{i}] {r.get('title', '')}\n"
            f"Body: {r.get('body', '')[:500]}\n"
            f"Engagement: {r.get('score', 0)} upvotes, {r.get('num_comments', 0)} comments"
        )

    prompt = cfg.PAIN_FILTER_PROMPT + "\n\n" + "\n\n".join(lines)

    try:
        resp = client.chat.completions.create(
            model=cfg.MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        kept_indices = set(
            i for i in json.loads(raw)
            if isinstance(i, int) and i < len(pool)
        )
        filtered  = [pool[i] for i in range(len(pool)) if i in kept_indices]
        discarded = [pool[i] for i in range(len(pool)) if i not in kept_indices]

        if discarded:
            logger.info("Quick filter DISCARDED %d items:", len(discarded))
            for r in discarded:
                logger.info(
                    "  DISC [%s] score=%s cmts=%s | %s",
                    r.get("source_type", "?"),
                    r.get("score", 0),
                    r.get("num_comments", 0),
                    r.get("title", "")[:80],
                )

        logger.info(
            "Quick filter: %d/%d passed (%d discarded)",
            len(filtered), len(pool), len(discarded),
        )
        return filtered
    except Exception as e:
        logger.warning("Quick filter failed: %s — passing entire pool to analysis", e)
        return pool


def analyze_pains(pool: list[dict], niche: str) -> list[dict]:
    """
    Stage 2 — full scoring on pre-filtered results.
    Logs all candidates returned by LLM for tuning.
    """
    if not pool:
        logger.warning("Empty pool — nothing to analyze")
        return []

    context = ""
    for i, r in enumerate(pool[:20], 1):
        context += (
            f"[{i}] {r.get('title', '')}\n"
            f"URL: {r.get('href', '')}\n"
            f"Source: {r.get('source_type', 'unknown')} | "
            f"Score: {r.get('score', 0)} | Comments: {r.get('num_comments', 0)}\n"
            f"Body: {r.get('body', '')[:600]}\n\n"
        )

    logger.info("Full analysis: %d results for niche '%s'", min(len(pool), 20), niche)
    try:
        resp = client.chat.completions.create(
            model=cfg.MODEL,
            messages=[
                {"role": "system", "content": cfg.PAIN_ANALYSIS_PROMPT},
                {"role": "user",   "content": f"Niche: {niche}\n\nSearch results:\n{context}"},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        raw = resp.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        pains = json.loads(raw)

        logger.info("LLM returned %d pain candidates:", len(pains))
        for p in pains:
            logger.info(
                "  PAIN score=%.1f freq=%s emo=%s mon=%s | %s",
                p.get("score", 0),
                p.get("frequency", "?"),
                p.get("emotion", "?"),
                p.get("monetizable", "?"),
                p.get("title", "")[:60],
            )

        for p in pains:
            p.setdefault("niche", niche)
        pains.sort(key=lambda x: x.get("score", 0), reverse=True)
        logger.info("Found %d pains after sorting", len(pains))
        return pains

    except (json.JSONDecodeError, Exception) as e:
        logger.error("Analysis failed: %s", e)
        return []


# ── Main pipeline ─────────────────────────────────────────────────────────────

def hunt_pains(niche: str, count: int = 5) -> list[dict]:
    """
    Full pipeline:
      1. Generate queries with specific subreddits (eliminates off-topic noise)
      2. Search Reddit within target subreddits + HN
      3. Enrich Reddit posts (full text + top comments)
      4. Quick-filter: remove obvious trash
      5. Full pain analysis with scoring
      6. Mark all seen URLs
    """
    logger.info("=== hunt_pains started: '%s' ===", niche)

    queries = generate_queries(niche)
    pool    = search_all(queries)
    pool    = enrich_results(pool)
    pool    = quick_filter(pool)

    if not pool:
        logger.warning("No results survived filtering for '%s'", niche)
        return []

    pains = analyze_pains(pool, niche)

    for r in pool:
        if url := r.get("href"):
            mark_seen(url)

    result = pains[:count]
    logger.info("=== hunt_pains done: %d pains returned ===", len(result))
    return result


# ── Formatting ────────────────────────────────────────────────────────────────

def score_to_stars(score: float) -> str:
    stars = round(score)
    return "⭐" * stars + "☆" * (5 - stars)


def format_pain(pain: dict, index: int = 1) -> str:
    score      = pain.get("score", 0)
    stars      = score_to_stars(score)
    money      = "✅ Решается кодом" if pain.get("monetizable") else "❌ Сложно монетизировать"
    freq       = "⭐" * pain.get("frequency", 3)
    emotion    = "🔥" * pain.get("emotion", 3)
    source     = pain.get("source", "")
    quote      = pain.get("quote", "")
    niche      = pain.get("niche", "")
    title      = pain.get("title", "No title")
    root_cause = pain.get("root_cause", "")
    solution   = pain.get("solution_hint", "")

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
    if not pains:
        return ["😔 No fresh pain points found. Try another niche or check back tomorrow."]
    messages = [
        f"🎯 Pain Hunter — Search Results\n"
        f"Found {len(pains)} opportunities\n"
        "——————————————————"
    ]
    for i, pain in enumerate(pains, 1):
        messages.append(format_pain(pain, i))
    return messages