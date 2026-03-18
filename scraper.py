import httpx
import logging
import time

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "PainHunter/1.0 (research tool; contact: tony.ervalson@gmail.com)",
    "Accept": "application/json",
}

def fetch_reddit_post(url: str) -> str:
    """
    Fetch full post text + top comments from a Reddit URL via the JSON API.
    Returns an enriched string that replaces the short DDG snippet.
    """
    if "reddit.com" not in url:
        return ""

    try:
        json_url = url.split("?")[0].rstrip("/") + "/.json?limit=15"

        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(json_url, headers=HEADERS)

        if resp.status_code != 200:
            logger.warning("Reddit JSON API returned %d for %s", resp.status_code, url)
            return ""

        data = resp.json()

        # ── Post data ──────────────────────────────────────────────────────
        post        = data[0]["data"]["children"][0]["data"]
        title       = post.get("title", "")
        body        = post.get("selftext", "")[:2000]   # was 1500, now 2000
        score       = post.get("score", 0)
        n_comments  = post.get("num_comments", 0)
        subreddit   = post.get("subreddit_name_prefixed", "")

        # ── Top comments ───────────────────────────────────────────────────
        comments = []
        try:
            for c in data[1]["data"]["children"][:8]:   # was 5, now top-8
                text = c.get("data", {}).get("body", "")
                if text and text not in ("[deleted]", "[removed]"):
                    comments.append(text[:400])           # was 300, now 400
        except Exception:
            pass

        result = (
            f"POST [{subreddit}]: {title}\n"
            f"Score: {score} | Comments: {n_comments}\n"
            f"Body: {body}\n\n"
            "TOP COMMENTS:\n" +
            "\n---\n".join(comments)
        )
        logger.info("Fetched %d chars from %s", len(result), url)
        return result

    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return ""


def enrich_results(pool: list[dict]) -> list[dict]:
    """
    Fetch full text + comments for ALL Reddit posts in the pool.

    Previously capped at 8 posts — removed that limit.
    Non-Reddit items (HN, etc.) are left as-is; their body was already
    set during the search step.
    """
    reddit_posts = [r for r in pool if "reddit.com/r/" in r.get("href", "")]

    logger.info("Enriching %d Reddit posts…", len(reddit_posts))

    for r in reddit_posts:
        full_text = fetch_reddit_post(r["href"])
        if full_text:
            r["body"] = full_text
        time.sleep(0.6)   # polite crawl delay

    return pool