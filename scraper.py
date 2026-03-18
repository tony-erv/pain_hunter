import httpx
import logging
import time
import re
import json

logger = logging.getLogger(__name__)

# ── Shared browser-like headers ───────────────────────────────────────────────
# Both G2 and Capterra run Cloudflare — plain API User-Agents get 403.
# A realistic browser UA + Accept headers is the minimum to get HTML back.

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

HEADERS_API = {
    "User-Agent": "PainHunter/1.0 (research tool; contact: tony.ervalson@gmail.com)",
    "Accept": "application/json",
}


# ── Reddit ────────────────────────────────────────────────────────────────────

def fetch_reddit_post(url: str) -> str:
    """
    Fetch full post text + top comments from a Reddit URL via the JSON API.
    Returns an enriched string that replaces the short snippet.
    """
    if "reddit.com" not in url:
        return ""

    try:
        json_url = url.split("?")[0].rstrip("/") + "/.json?limit=15"

        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(json_url, headers=HEADERS_API)

        if resp.status_code != 200:
            logger.warning("Reddit JSON API returned %d for %s", resp.status_code, url)
            return ""

        data = resp.json()

        post       = data[0]["data"]["children"][0]["data"]
        title      = post.get("title", "")
        body       = post.get("selftext", "")[:2000]
        score      = post.get("score", 0)
        n_comments = post.get("num_comments", 0)
        subreddit  = post.get("subreddit_name_prefixed", "")

        comments = []
        try:
            for c in data[1]["data"]["children"][:8]:
                text = c.get("data", {}).get("body", "")
                if text and text not in ("[deleted]", "[removed]"):
                    comments.append(text[:400])
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


# ── G2 ────────────────────────────────────────────────────────────────────────
#
# Strategy:
#   1. Search G2 for products matching the niche keyword
#      GET https://www.g2.com/search?query={niche}
#      → parse product slugs from href="/products/{slug}/reviews"
#
#   2. For each slug, fetch the reviews page
#      GET https://www.g2.com/products/{slug}/reviews
#      → extract JSON-LD (<script type="application/ld+json"> with @type Review)
#      → if JSON-LD absent, fall back to HTML class parsing
#
#   3. Return only reviews that have a non-empty "cons" / "dislike" section
#      — that's the pain signal we care about.
#
# Known limitations:
#   - Cloudflare may block in datacenter IPs; degrade gracefully
#   - JS-rendered pagination isn't accessible — we only get page 1
#   - G2 throttles aggressively; sleep between requests

def _g2_slugs_for_niche(niche: str, max_products: int = 3) -> list[str]:
    """
    Search G2 for software products related to the niche.
    Returns a list of product slugs (e.g. ['hubspot-crm', 'pipedrive']).
    """
    try:
        resp = httpx.get(
            "https://www.g2.com/search",
            params={"query": niche},
            headers=HEADERS_BROWSER,
            timeout=12,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.warning("G2 search returned HTTP %d for '%s'", resp.status_code, niche)
            return []

        # Product review links look like: href="/products/hubspot-crm/reviews"
        slugs = re.findall(r'href="/products/([^"/]+)/reviews"', resp.text)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique = []
        for s in slugs:
            if s not in seen:
                seen.add(s)
                unique.append(s)

        logger.info("G2 search '%s': found slugs %s", niche, unique[:max_products])
        return unique[:max_products]

    except Exception as e:
        logger.warning("G2 slug search failed: %s", e)
        return []


def _parse_g2_reviews_page(html: str, product_slug: str) -> list[dict]:
    """
    Extract review cons/dislikes from a G2 reviews HTML page.

    Two extraction paths (in order of preference):
      A) JSON-LD schema:  <script type="application/ld+json"> with Review objects
         → reviewBody contains the full review; we split on "What I Dislike:"
      B) HTML data attributes / class names:
         → <div data-rnr-role="cons"> or <p class="*dislike*"> patterns
    """
    reviews = []

    # ── Path A: JSON-LD ──────────────────────────────────────────────────────
    jsonld_blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    for block in jsonld_blocks:
        try:
            data = json.loads(block)
            # Can be a single object or an array
            items = data if isinstance(data, list) else [data]
            for item in items:
                # Handle @graph wrapper
                if item.get("@type") == "ItemList" or "@graph" in item:
                    items = item.get("@graph", items)
                    continue
                if item.get("@type") != "Review":
                    continue

                body = item.get("reviewBody", "")
                rating = item.get("reviewRating", {}).get("ratingValue", 0)

                # Extract the "dislike" section — G2 separates pros/cons in reviewBody
                cons = _extract_cons_from_review_body(body)
                if not cons:
                    continue

                reviews.append({
                    "title":       item.get("name", f"G2 review of {product_slug}"),
                    "href":        f"https://www.g2.com/products/{product_slug}/reviews",
                    "body":        cons,
                    "score":       0,
                    "num_comments": 0,
                    "source_type": "g2",
                    "rating":      float(rating) if rating else 0.0,
                })
        except (json.JSONDecodeError, Exception):
            continue

    if reviews:
        logger.info("G2 JSON-LD: extracted %d cons-reviews from %s", len(reviews), product_slug)
        return reviews

    # ── Path B: HTML patterns ────────────────────────────────────────────────
    # G2 review cards use data attributes for cons sections.
    # Pattern 1: <div data-rnr-role="cons"> or similar
    cons_blocks = re.findall(
        r'data-rnr-role=["\']cons["\'][^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    # Pattern 2: Any element with "dislike" in class name
    if not cons_blocks:
        cons_blocks = re.findall(
            r'class=["\'][^"\']*dislike[^"\']*["\'][^>]*>(.*?)</(?:div|p|span)>',
            html, re.DOTALL
        )
    # Pattern 3: Text following "What I Dislike" heading
    if not cons_blocks:
        cons_blocks = re.findall(
            r'What I Dislike[^<]*</[^>]+>\s*<[^>]+>([^<]{40,500})',
            html, re.DOTALL
        )

    for block in cons_blocks:
        text = re.sub(r'<[^>]+>', ' ', block).strip()
        text = re.sub(r'\s+', ' ', text)
        if len(text) < 40:
            continue
        reviews.append({
            "title":        f"G2 dislike: {product_slug}",
            "href":         f"https://www.g2.com/products/{product_slug}/reviews",
            "body":         text[:600],
            "score":        0,
            "num_comments": 0,
            "source_type":  "g2",
            "rating":       0.0,
        })

    logger.info("G2 HTML: extracted %d cons-reviews from %s", len(reviews), product_slug)
    return reviews


def _extract_cons_from_review_body(body: str) -> str:
    """
    G2 reviewBody format:
      'What do you like best? ... What do you dislike? ... Recommendations...'
    Extract only the "dislike" section.
    """
    # Various separator patterns G2 uses
    patterns = [
        r'[Ww]hat do you dislike\?[^:]*?:\s*(.*?)(?:[Ww]hat problems|[Rr]ecommendations|$)',
        r'[Ww]hat I [Dd]islike:?\s*(.*?)(?:[Ww]hat [Pp]roblems|[Rr]ecommendations|$)',
        r'[Cc]ons:?\s*(.*?)(?:[Pp]ros:|[Ww]hat I [Ll]ike|$)',
        r'[Dd]islikes?:?\s*(.*?)(?:[Ll]ikes?:|[Pp]ros:|$)',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.DOTALL | re.IGNORECASE)
        if m:
            text = m.group(1).strip()[:600]
            if len(text) > 30:
                return text
    return ""


def fetch_g2_reviews(niche: str, max_products: int = 3) -> list[dict]:
    """
    Main entry point: find top products for niche on G2 and return
    their negative reviews as pain signal items.
    """
    all_reviews: list[dict] = []
    slugs = _g2_slugs_for_niche(niche, max_products)

    for slug in slugs:
        try:
            resp = httpx.get(
                f"https://www.g2.com/products/{slug}/reviews",
                params={"sort": "most_helpful", "filters[review_type]": "verified"},
                headers=HEADERS_BROWSER,
                timeout=12,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                reviews = _parse_g2_reviews_page(resp.text, slug)
                all_reviews.extend(reviews)
                logger.info("G2 [%s]: %d reviews", slug, len(reviews))
            else:
                logger.warning("G2 reviews page %s returned HTTP %d", slug, resp.status_code)
        except Exception as e:
            logger.warning("G2 fetch failed for %s: %s", slug, e)

        time.sleep(1.5)   # G2 throttles hard — be polite

    return all_reviews


# ── Capterra ──────────────────────────────────────────────────────────────────
#
# Strategy:
#   1. Search Capterra directory
#      GET https://www.capterra.com/search/#q={niche}&type=software
#      → BUT this is JS-rendered — doesn't work with httpx
#
#   Alternative (more reliable):
#      GET https://www.capterra.com/reviews/{niche-slug}/
#      → Their review listing pages are mostly server-side rendered
#      → Extract review cons sections
#
#   2. Parse review pages:
#      → <div class="review-body"> containing pros/cons subsections
#      → <p data-testid="cons"> or similar test-id attributes
#      → Structured <ul class="cons-list"> items
#
# Capterra niche slugs: "crm-software", "project-management-software", etc.

def _niche_to_capterra_slug(niche: str) -> str:
    """
    Convert a human niche description to a Capterra category URL slug.
    E.g. 'real estate agents' → 'real-estate-agency-software'
    This is a best-effort mapping — Capterra uses specific category slugs.
    """
    # Clean and lowercase
    slug = niche.lower().strip()
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'[^a-z0-9-]', '', slug)

    # Common suffix patterns Capterra uses
    if not slug.endswith('-software'):
        slug += '-software'

    return slug


def _capterra_search_products(niche: str, max_products: int = 3) -> list[str]:
    """
    Find Capterra product review page URLs for a niche.
    Uses Capterra's category listing page which is SSR (unlike their SPA search).
    Returns list of full review page URLs.
    """
    category_slug = _niche_to_capterra_slug(niche)
    try:
        resp = httpx.get(
            f"https://www.capterra.com/{category_slug}/",
            headers=HEADERS_BROWSER,
            timeout=12,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.warning(
                "Capterra category '%s' returned HTTP %d", category_slug, resp.status_code
            )
            return []

        # Product links look like: href="/p/12345/product-name/"
        # or: href="/reviews/12345/product-name/"
        urls = re.findall(r'href="(/(?:p|reviews)/\d+/[^/"]+/?)"', resp.text)
        seen: set[str] = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append("https://www.capterra.com" + u)
        logger.info("Capterra category '%s': found %d products", category_slug, len(unique))
        return unique[:max_products]

    except Exception as e:
        logger.warning("Capterra product search failed: %s", e)
        return []


def _parse_capterra_reviews_page(html: str, source_url: str) -> list[dict]:
    """
    Extract cons/negative content from a Capterra product review page.

    Capterra's review HTML structure (as of 2024-2025):
      <div class="review-body">
        <div class="pros">...</div>
        <div class="cons">...</div>
      </div>

    They also embed structured JSON with pros/cons fields.
    """
    reviews = []

    # ── Path A: JSON embedded data ────────────────────────────────────────────
    # Capterra sometimes embeds window.__INITIAL_STATE__ or similar
    json_matches = re.findall(
        r'window\.__(?:INITIAL_STATE|PAGE_DATA|APP_DATA)__\s*=\s*(\{.*?\});',
        html, re.DOTALL
    )
    for match in json_matches[:2]:
        try:
            data = json.loads(match)
            # Recursively search for cons/dislikes fields
            cons_texts = _extract_nested_cons(data)
            for text in cons_texts[:10]:
                if len(text) > 40:
                    reviews.append({
                        "title":        "Capterra user dislike",
                        "href":         source_url,
                        "body":         text[:600],
                        "score":        0,
                        "num_comments": 0,
                        "source_type":  "capterra",
                        "rating":       0.0,
                    })
        except (json.JSONDecodeError, Exception):
            continue

    if reviews:
        return reviews

    # ── Path B: HTML class/attribute patterns ─────────────────────────────────
    # Pattern 1: <div class="cons"> or <div class="cons-section">
    cons_divs = re.findall(
        r'class=["\'][^"\']*\bcons\b[^"\']*["\'][^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    # Pattern 2: data-testid with cons
    if not cons_divs:
        cons_divs = re.findall(
            r'data-testid=["\'][^"\']*cons[^"\']*["\'][^>]*>(.*?)</(?:div|p)>',
            html, re.DOTALL
        )
    # Pattern 3: "Cons:" heading followed by text
    if not cons_divs:
        cons_divs = re.findall(
            r'(?:Cons|Dislikes?|What could be improved)[:\s]*</[^>]+>\s*<[^>]+>([^<]{40,600})',
            html
        )
    # Pattern 4: Look for review sections with negative ratings
    if not cons_divs:
        # Reviews with 1-3 star ratings often have the whole text as pain signal
        low_rated = re.findall(
            r'(?:rating|stars?)["\s:=]*["\']?[123]["\']?[^>]*>.*?<[^>]+class=["\'][^"\']*review[^"\']*["\'][^>]*>(.*?)</(?:article|section|div)>',
            html, re.DOTALL
        )
        cons_divs = low_rated[:5]

    for block in cons_divs[:10]:
        text = re.sub(r'<[^>]+>', ' ', block).strip()
        text = re.sub(r'\s+', ' ', text)
        if len(text) < 40:
            continue
        reviews.append({
            "title":        "Capterra user dislike",
            "href":         source_url,
            "body":         text[:600],
            "score":        0,
            "num_comments": 0,
            "source_type":  "capterra",
            "rating":       0.0,
        })

    logger.info("Capterra HTML: %d cons-entries from %s", len(reviews), source_url)
    return reviews


def _extract_nested_cons(data: object, depth: int = 0) -> list[str]:
    """
    Recursively walk a JSON object looking for cons/dislike text values.
    """
    if depth > 6:
        return []
    results = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() in ("cons", "dislikes", "dislike", "what_i_dislike",
                             "negatives", "negative", "improvement_areas"):
                if isinstance(v, str) and len(v) > 30:
                    results.append(v)
                elif isinstance(v, list):
                    results.extend(s for s in v if isinstance(s, str) and len(s) > 30)
            else:
                results.extend(_extract_nested_cons(v, depth + 1))
    elif isinstance(data, list):
        for item in data:
            results.extend(_extract_nested_cons(item, depth + 1))
    return results


def fetch_capterra_reviews(niche: str, max_products: int = 3) -> list[dict]:
    """
    Main entry point: find products for niche on Capterra and return
    their negative review sections as pain signal items.
    """
    all_reviews: list[dict] = []
    product_urls = _capterra_search_products(niche, max_products)

    for url in product_urls:
        # Ensure we hit the reviews tab
        review_url = url.rstrip("/") + "#reviews" if "/p/" in url else url
        try:
            resp = httpx.get(
                review_url,
                headers=HEADERS_BROWSER,
                timeout=12,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                reviews = _parse_capterra_reviews_page(resp.text, review_url)
                all_reviews.extend(reviews)
            else:
                logger.warning("Capterra %s returned HTTP %d", review_url, resp.status_code)
        except Exception as e:
            logger.warning("Capterra fetch failed for %s: %s", url, e)

        time.sleep(1.2)

    logger.info("Capterra total: %d reviews for niche '%s'", len(all_reviews), niche)
    return all_reviews


# ── Main enrichment pipeline ──────────────────────────────────────────────────

def enrich_results(pool: list[dict]) -> list[dict]:
    """
    Enrich Reddit posts in the pool with full text + comments.
    Non-Reddit items (HN, G2, Capterra) are left as-is —
    their body was already set during the search/fetch step.
    """
    reddit_posts = [r for r in pool if "reddit.com/r/" in r.get("href", "")]
    logger.info("Enriching %d Reddit posts…", len(reddit_posts))

    for r in reddit_posts:
        full_text = fetch_reddit_post(r["href"])
        if full_text:
            r["body"] = full_text
        time.sleep(0.6)

    return pool