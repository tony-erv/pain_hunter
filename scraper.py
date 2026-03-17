import httpx
import logging
import time

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "PainHunter/1.0 (research tool; contact: tony.ervalson@gmail.com)",
    "Accept": "application/json"
}

def fetch_reddit_post(url: str) -> str:
    """
    Fetches the Reddit post and its top comments from the given URL.
    """
    if "reddit.com" not in url:
        return ""

    try:
        # Reddit JSON API — add .json to URL
        json_url = url.split("?")[0].rstrip("/") + "/.json?limit=10"

        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(json_url, headers=HEADERS)

        if resp.status_code != 200:
            return ""

        data = resp.json()

        # Get post data
        post   = data[0]["data"]["children"][0]["data"]
        title  = post.get("title", "")
        body   = post.get("selftext", "")[:1500]  # первые 1500 символов
        score  = post.get("score", 0)
        n_comments = post.get("num_comments", 0)

        # Get top comments
        comments = []
        try:
            for c in data[1]["data"]["children"][:5]:
                text = c.get("data", {}).get("body", "")
                if text and text != "[deleted]":
                    comments.append(text[:300])
        except:
            pass

        result = (
            f"POST: {title}\n"
            f"Score: {score} | Comments: {n_comments}\n"
            f"Body: {body}\n\n"
            f"TOP COMMENTS:\n" +
            "\n---\n".join(comments)
        )
        logger.info("Fetched %d chars from %s", len(result), url)
        return result

    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return ""

def enrich_results(pool: list[dict]) -> list[dict]:
    """
    For each Reddit post in the pool, fetch the full text and top comments, and replace the snippet with this enriched content.
    """
    reddit_posts = [
        r for r in pool
        if "reddit.com/r/" in r.get("href", "")
    ][:8]

    for r in reddit_posts:
        full_text = fetch_reddit_post(r["href"])
        if full_text:
            r["body"] = full_text   
        time.sleep(0.5)           

    return pool