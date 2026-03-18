import json
import logging
import re
import time
import httpx
from openai import OpenAI
from config import cfg
from database import is_seen, mark_seen
from scraper import enrich_results, fetch_g2_reviews, fetch_capterra_reviews

logger = logging.getLogger(__name__)
client = OpenAI(api_key=cfg.OPENAI_API_KEY)

HEADERS = {
    "User-Agent": "PainHunter/1.0 (research tool; contact: tony.ervalson@gmail.com)",
    "Accept": "application/json",
}

# Titles matching these patterns are spam/noise — skip before hitting the LLM.
# Add patterns here as new spam types are discovered in logs.
_TITLE_BLOCKLIST = re.compile(
    r"industry news recap|week of \w+ \d+|"
    r"weekly digest|weekly roundup|weekly wrap|"
    r"this week('s| in)|top stories|"
    r"hiring|we('re| are) hiring|job opening|job post",
    re.IGNORECASE,
)

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

def search_reddit(query: str, subreddit: str = "", limit: int = 20) -> list[dict]:
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
            title = d.get("title", "")
            if _TITLE_BLOCKLIST.search(title):
                continue   # spam / news digest — skip before touching LLM
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




def search_producthunt(query: str, limit: int = 8) -> list[dict]:
    """
    Search Product Hunt via DDG site: search.
    Targets comment signals: missing features, competitor comparisons, "would be better if".
    """
    results: list[dict] = []
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            # Two queries: "Ask PH" posts (user problems) + product comments (missing features)
            hits = []
            for q in [
                f'site:producthunt.com "{query}" "manually" OR "I built" OR "switched from" OR "missing"',
                f'site:producthunt.com "{query}" "would be better" OR "wish it" OR "cant" OR "no way to"',
            ]:
                hits += list(ddgs.text(q, max_results=limit // 2))
        kept = 0
        for h in hits:
            body = h.get("body", "")
            href = h.get("href", "")
            # Skip pure product listing pages without review/comment content
            if href.endswith("/upcoming") or "?ref=" in href:
                continue
            if len(body) < 60:
                continue
            results.append({
                "title":        h.get("title", ""),
                "href":         href,
                "body":         body[:500],
                "score":        0,
                "num_comments": 0,
                "source_type":  "producthunt",
            })
            kept += 1
        logger.info("Product Hunt: %d results for '%s'", kept, query)
    except Exception as e:
        logger.warning("Product Hunt search failed: %s", e)
    return results


def search_amazon_reviews(niche: str, limit: int = 8) -> list[dict]:
    """
    Find negative Amazon reviews via DDG + direct page fetch.
    
    DDG path: targets amazon.com/product-reviews pages with negative signals.
    Direct fetch: appends filterByStar=critical to get 1-3 star reviews,
    then extracts review body text via data-hook attribute.
    """
    import re as _re
    results: list[dict] = []

    # DDG: find Amazon review pages with negative signals
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            # Use /gp/customer-reviews/ path — more specific than /product-reviews
            # Add "stars:1-3" equivalent signals and exclude app/movie noise
            hits = list(ddgs.text(
                f'site:amazon.com "{niche}" book OR software review "does not" OR "missing" OR "wish it" OR "manually" -app -movie -film',
                max_results=limit,
            ))
        kept = 0
        for h in hits:
            body = h.get("body", "")
            href = h.get("href", "")
            # Skip non-review Amazon pages (search results, product pages)
            if not any(p in href for p in ["/product-reviews/", "/customer-reviews/", "/dp/"]):
                continue
            if len(body) < 60:
                continue
            results.append({
                "title":        h.get("title", ""),
                "href":         href,
                "body":         body[:500],
                "score":        0,
                "num_comments": 0,
                "source_type":  "amazon",
            })
            kept += 1
        logger.info("Amazon DDG: %d results for '%s'", kept, niche)
    except Exception as e:
        logger.warning("Amazon DDG search failed: %s", e)
        return results

    # Direct fetch: enrich with full review text from critical reviews page
    data_hook_re = _re.compile(
        r'data-hook="review-body"[^>]*>[^<]*<span[^>]*>(.*?)</span>',
        _re.DOTALL,
    )
    tag_re = _re.compile(r"<[^>]+>")

    for r in results:
        href = r.get("href", "")
        if "amazon.com/product-reviews" not in href:
            continue
        try:
            base = href.split("?")[0]
            review_url = base + "?filterByStar=critical&sortBy=recent"
            resp = httpx.get(
                review_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=10,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                texts = data_hook_re.findall(resp.text)
                if texts:
                    cleaned = " | ".join(
                        tag_re.sub(" ", t).strip()[:300]
                        for t in texts[:3]
                    )
                    if len(cleaned) > 40:
                        r["body"] = cleaned
            time.sleep(1.0)
        except Exception:
            pass

    return results


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

    # Build keyword frequency map from ALL pool items (not just top 20)
    # This gives LLM real data for the frequency dimension instead of guessing.
    import re as _re
    pain_keywords = [
        "manually", "spreadsheet", "hours", "every week", "every month",
        "broken", "missing", "no tool", "wish there was", "looking for",
        "automate", "tedious", "nightmare", "killing me", "waste of time",
    ]
    keyword_counts: dict[str, int] = {}
    for r in pool:
        text = (r.get("title", "") + " " + r.get("body", "")).lower()
        for kw in pain_keywords:
            if kw in text:
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

    # Include keyword frequency summary in context so LLM can calibrate
    freq_summary = ", ".join(
        f'"{kw}" x{cnt}'
        for kw, cnt in sorted(keyword_counts.items(), key=lambda x: -x[1])
        if cnt > 0
    )
    freq_header = (
        f"KEYWORD FREQUENCY ACROSS ALL {len(pool)} ITEMS IN POOL:\n"
        f"{freq_summary or 'none'}\n"
        f"Use these counts to calibrate frequency scores — "
        f"a keyword appearing 1-2x = freq 2, 3-5x = freq 3, 6-10x = freq 4, 10+x = freq 5\n\n"
    )

    context = freq_header
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

        # Deduplicate: skip pains whose source URL already appeared
        # and pains with near-identical titles (same topic, different post)
        seen_urls: set[str] = set()
        seen_title_words: list[set] = []
        deduped = []
        for p in pains:
            url = p.get("source", "")
            title_words = set(p.get("title", "").lower().split()) - {"a","the","is","of","and","to","in","for","with"}
            # Skip if same URL
            if url and url in seen_urls:
                logger.info("  DEDUP (same URL): %s", p.get("title","")[:60])
                continue
            # Skip if title overlaps >60% with an already-kept pain
            is_dup = False
            for kept_words in seen_title_words:
                if len(title_words) > 0:
                    overlap = len(title_words & kept_words) / len(title_words)
                    if overlap > 0.6:
                        logger.info("  DEDUP (similar title): %s", p.get("title","")[:60])
                        is_dup = True
                        break
            if is_dup:
                continue
            deduped.append(p)
            if url:
                seen_urls.add(url)
            seen_title_words.append(title_words)

        logger.info("Found %d pains after dedup (was %d)", len(deduped), len(pains))
        return deduped

    except (json.JSONDecodeError, Exception) as e:
        logger.error("Analysis failed: %s", e)
        return []


# ── Main pipeline ─────────────────────────────────────────────────────────────

def search_review_sites(niche: str) -> list[dict]:
    """
    Get negative reviews from G2 and Capterra via DuckDuckGo search snippets.

    WHY SNIPPETS INSTEAD OF DIRECT SCRAPING:
    Both G2 and Capterra run Cloudflare WAF that blocks datacenter IPs with 403.
    However, DDG search results include rich snippets from their pages —
    often containing the exact cons/dislike text — without needing to fetch
    the actual page. This gives us the signal without the block.

    Searches used:
      - 'site:g2.com "{niche}" "what I dislike"'
      - 'site:capterra.com "{niche}" software reviews'
    """
    from ddgs import DDGS   # imported here to keep it optional — if not installed, degrades gracefully

    pool: list[dict] = []
    seen_in_batch: set[str] = set()

    # Target review-specific subpaths — avoids category/listing pages
    # "manually" co-occurrence narrows to workflow pain signals
    queries = [
        (f'site:g2.com/reviews {niche} dislike manually',        "g2"),
        (f'site:g2.com/reviews {niche} "what I dislike"',         "g2"),
        (f'site:capterra.com/reviews {niche} cons manually',      "capterra"),
    ]

    for query, source_type in queries:
        logger.info("Review search [%s]: %s", source_type, query)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
            for r in results:
                url  = r.get("href", "")
                body = r.get("body", "")
                if not url or len(body) < 40:
                    continue
                if url in seen_in_batch or is_seen(url):
                    continue
                pool.append({
                    "title":        r.get("title", ""),
                    "href":         url,
                    "body":         body[:600],
                    "score":        0,
                    "num_comments": 0,
                    "source_type":  source_type,
                })
                seen_in_batch.add(url)
            time.sleep(1.0)
        except Exception as e:
            logger.warning("Review search failed [%s]: %s", source_type, e)
            continue

    logger.info("Review sites total: %d fresh items", len(pool))
    return pool


def hunt_pains(niche: str, count: int = 5) -> list[dict]:
    """
    Full pipeline:
      1. Generate queries with specific subreddits
      2. Search Reddit (subreddit-targeted) + HN
      3. Enrich Reddit posts (full text + top comments)
      4. Search Product Hunt (DDG site: + comment signals)
      5. Search Amazon negative reviews (DDG + direct fetch)
      6. Merge all sources → quick-filter → full analysis
      7. Mark all seen URLs
    """
    logger.info("=== hunt_pains started: '%s' ===", niche)

    queries      = generate_queries(niche)
    reddit_pool  = search_all(queries)
    reddit_pool  = enrich_results(reddit_pool)

    # Product Hunt + Amazon: additional pain signal sources
    # Product Hunt: DDG ignores site: operator for PH — returns unrelated pages.
    # Disabled until a direct API approach is found.
    # ph_pool = search_producthunt(niche)
    amazon_pool = search_amazon_reviews(niche)

    pool = reddit_pool + amazon_pool
    logger.info(
        "Pool: %d items (reddit/hn=%d, amazon=%d)",
        len(pool), len(reddit_pool), len(amazon_pool),
    )

    pool = quick_filter(pool)

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