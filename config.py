import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
    TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
    MODEL               = os.getenv("MODEL", "gpt-4o-mini")

    DATA_DIR            = os.getenv("DATA_DIR", ".")
    DB_PATH             = os.path.join(DATA_DIR, "pains.db")

    # ── Digest schedule ───────────────────────────────────────────────────────
    DIGEST_HOUR         = int(os.getenv("DIGEST_HOUR", 9))
    DIGEST_MINUTE       = int(os.getenv("DIGEST_MINUTE", 0))
    DIGEST_COUNT        = 5
    DIGEST_NICHES       = 3

    # ── Limits ────────────────────────────────────────────────────────────────
    MAX_HUNT_PER_HOUR   = 3
    MAX_SEARCH_RESULTS  = 15
    HASH_TTL_DAYS       = 30

    DEFAULT_NICHES = [
        "software developers",
        "system administrators",
        "small business owners",
        "freelance designers",
        "content creators",
        "accountants",
        "project managers",
        "startup founders",
        "e-commerce sellers",
        "HR professionals",
        "data analysts",
        "remote workers",
    ]

    # Fallback templates used when LLM query generation fails
    QUERY_TEMPLATES = [
        {"query": "{niche} I have to manually",     "source": "reddit", "subreddit": ""},
        {"query": "{niche} wish there was a tool",  "source": "reddit", "subreddit": ""},
        {"query": "{niche} too expensive automate", "source": "reddit", "subreddit": ""},
        {"query": "{niche} I built this because",   "source": "hn",     "subreddit": ""},
        {"query": "{niche} looking for software",   "source": "hn",     "subreddit": ""},
    ]

    # ─────────────────────────────────────────────────────────────────────────
    # QUERY_GEN_PROMPT
    #
    # KEY CHANGE: each Reddit query now carries a specific "subreddit" field.
    # We search within that subreddit directly (/r/SUB/search.json?restrict_sr=1)
    # instead of global Reddit search — this eliminates AITA/relationship drama
    # and other off-topic noise completely.
    # ─────────────────────────────────────────────────────────────────────────
    QUERY_GEN_PROMPT = """You are a market research expert hunting for startup opportunities.
Given a niche, generate 5 search queries that surface MONETIZABLE pains —
problems people would pay to have solved with software or automation.

OUTPUT FORMAT — return exactly 5 objects:
- 3 Reddit queries, each targeting a SPECIFIC relevant subreddit
- 2 Hacker News queries (no subreddit field needed)

Each Reddit object must have:
  "query"     — plain keywords, NO site: operators, NO quotes
  "source"    — "reddit"
  "subreddit" — exact subreddit name without r/ prefix (e.g. "realestate", "sysadmin")

Each HN object must have:
  "query"     — plain keywords
  "source"    — "hn"

SUBREDDIT SELECTION RULES:
- Pick subreddits where the niche's professionals actually post work questions
- Use the most active/relevant professional community
- Examples by niche:
    realtors/real estate → realestate, realtors, RealEstate
    accountants          → Accounting, taxpros, bookkeeping
    sysadmins            → sysadmin, devops, homelab
    developers           → ExperiencedDevs, webdev, programming
    freelancers          → freelance, digitalnomad
    e-commerce sellers   → ecommerce, FulfillmentByAmazon, dropship
    startup founders     → startups, Entrepreneur, SaaS
    HR professionals     → humanresources, recruiting
    project managers     → projectmanagement, agile
    data analysts        → dataengineering, BusinessIntelligence

QUERY CONSTRUCTION — PRIORITY ORDER:
Prefer queries that find WORKFLOW and TOOL pain over emotional venting.
Use these high-signal phrases (pick different ones for each query):

  TIER 1 — strongest monetization signal (use these first):
    "is there a tool"
    "I have to manually"
    "I spend hours"
    "automate this"
    "looking for software"
    "I'd pay for"
    "I built this because"
    "wish there was"

  TIER 2 — ok if Tier 1 already covered:
    "does anyone else"
    "too expensive"

- Vary the pain angle — don't repeat the same phrase twice
- Focus on RECURRING workflow problems, not one-time events or emotional venting

Return ONLY a valid JSON array of 5 objects, nothing else.

Example for niche "realtors":
[
  {"query": "realtor is there a tool automate lead follow-up CRM", "source": "reddit", "subreddit": "realtors"},
  {"query": "real estate agent I have to manually update listings MLS", "source": "reddit", "subreddit": "realestate"},
  {"query": "realtor I spend hours on paperwork wish there was automation", "source": "reddit", "subreddit": "RealEstate"},
  {"query": "real estate agent I built this because no CRM worked for me", "source": "hn"},
  {"query": "realtor software I'd pay for automated listing updates", "source": "hn"}
]

Example for niche "accountants Canada":
[
  {"query": "accountant Canada is there a tool automate reconciliation", "source": "reddit", "subreddit": "Accounting"},
  {"query": "Canadian bookkeeper I have to manually enter invoices every month", "source": "reddit", "subreddit": "bookkeeping"},
  {"query": "accounting Canada I spend hours on tax prep wish there was software", "source": "reddit", "subreddit": "taxpros"},
  {"query": "accountants Canada I built this because no tool handled edge cases", "source": "hn"},
  {"query": "accounting Canada I'd pay for automated reconciliation tool", "source": "hn"}
]"""

    # ─────────────────────────────────────────────────────────────────────────
    # PAIN_FILTER_PROMPT  (Stage 1 — cheap pre-filter)
    # PURPOSE: remove only obvious garbage. Be PERMISSIVE.
    # Target pass rate: 50-70%.
    # ─────────────────────────────────────────────────────────────────────────
    PAIN_FILTER_PROMPT = """You are a PERMISSIVE pre-filter for a startup research pipeline.

Your only job: remove OBVIOUS non-pain content so the expensive analysis step
doesn't waste tokens on it. When in doubt — KEEP the item.

DISCARD only if it CLEARLY matches one of these:
✗ Pure news article or press release (no user complaints)
✗ Job posting or hiring announcement
✗ Tutorial, how-to guide, or documentation with no frustration expressed
✗ Product launch announcement with no user reactions
✗ Completely empty body AND a neutral/informational title
✗ Written in third person describing OTHER people's problems as a pitch or overview
  (e.g. "Many accountants spend hours doing X" — this is a sales/marketing post,
   not a personal pain signal. Real pain uses first person: "I spend hours doing X")
✗ Founder/builder validation post asking if others would pay
  (e.g. "Would you pay $10/month for X?", "I'm building X, would you use it?",
   "Is there demand for X?" — these are market research, not pain signals)

ALWAYS KEEP — even if it looks like a question:
✓ "Is there a tool that does X?" — this signals a missing solution
✓ "Does anyone else struggle with X?" — shared frustration
✓ "I built this because X didn't exist" — validated pain
✓ "How do you handle X?" when X sounds painful or manual
✓ Any post where someone describes spending time on a manual process
✓ Any complaint about price, complexity, or lack of automation
✓ Ambiguous posts — if unsure, KEEP

Return ONLY a JSON array of integer indices of the items to KEEP.
Example: [0, 1, 3, 4, 6, 7]
Return [] only if everything is obvious non-pain garbage.
No explanation, no markdown — only the JSON array."""

    # ─────────────────────────────────────────────────────────────────────────
    # PAIN_ANALYSIS_PROMPT  (Stage 2 — full scoring)
    # Fixes vs previous:
    #   1. Added explicit WHO PAYS check before setting monetizable=true
    #   2. Strengthened one-specific-company exclusion rule with examples
    # ─────────────────────────────────────────────────────────────────────────
    PAIN_ANALYSIS_PROMPT = """You are a brutal startup opportunity analyst.
Your job: find ONLY pains worth building a business around.

Analyze the provided search results and extract user pain points.

SCORING — use the full 1-5 range. Most pains are 2-3. Reserve 4-5 for exceptional cases.

FREQUENCY (1-5): how many different people face this repeatedly?
  1 = one person mentioned it, unclear if others have it
  2 = a few people in niche communities, once in a while
  3 = clearly recurring across multiple posts/comments, dozens of people
  4 = hundreds of people, mentioned regularly in professional communities
  5 = ONLY if you see multiple posts + comments confirming it's universal in the niche

  Calibration examples:
  freq=2: "my specific client's CRM doesn't integrate with X"
  freq=3: "I spend 2hrs/week copying data between two tools" (1 post, relatable)
  freq=4: "manually reconciling invoices" — multiple posts, many upvotes, comments say "same here"
  freq=5: "every FBA seller deals with lost inventory claims" — thread after thread, thousands affected

EMOTION (1-5): how intense is the frustration in the actual text?
  1 = neutral mention, no frustration expressed ("it would be nice if...")
  2 = mild annoyance ("it's a bit annoying that...")
  3 = clear frustration ("I hate having to do this every week")
  4 = significant anger or financial impact ("this cost me a client", "I spent $500 fixing this")
  5 = ONLY if: explicit financial loss + fury + recurring ("I'm losing $2k/month", "I've tried everything")

  Calibration examples:
  emo=2: "wish there was a better way to do X"
  emo=3: "I have to manually do X every single day, it's exhausting"
  emo=4: "this broken process cost me a client last week"
  emo=5: "I've lost $3000 this month because of this, tried 4 tools, nothing works"

score = (frequency + emotion) / 2
monetizable (true/false): would the SUFFERER pay $10-500/month to fix this?

MONETIZABLE CHECK — answer all three before setting monetizable=true:
  1. WHO experiences this pain? (e.g. buyer, seller, agent, freelancer, manager)
  2. WHO would actually pay for the fix? (the person with budget AND incentive)
  3. Are they the same person?
  → If the sufferer and the payer are different people: monetizable = false
  → If the solution would require building a whole new marketplace/platform: monetizable = false

  Examples:
  ✗ Airbnb GUEST frustrated by cancellations — guest suffers, but guests don't
    pay $X/month SaaS subscriptions to protect against rare cancellations.
  ✗ Hotel CUSTOMER frustrated by prices — not a SaaS opportunity.
  ✓ Airbnb HOST frustrated by manually coordinating cleaners after each checkout —
    HOST suffers AND would pay $30/month for automation.
  ✓ Agent frustrated by manually following up every lead — AGENT pays. monetizable = true.

INCLUDE a pain if ALL of these are true:
✓ Affects many people repeatedly (not a one-time incident)
✓ No obvious good solution exists yet (or existing ones are too expensive)
✓ Can be solved with software, automation, or a bot
✓ Score >= 3.0
✓ Person describes a specific negative experience or recurring frustration
✓ Language signals pain: "I hate", "I have to manually", "costs me",
  "I keep losing", "every time I", "I've tried everything"
✓ The person who suffers is also the one who would pay for a fix

EXCLUDE a pain if ANY of these are true:
✗ Complaint about ONE specific named company, product, or person
  Bad: "Hali Estates took my $5000 and delivered nothing"
  Bad: "Zillow's algorithm shows wrong prices"
  Bad: "my agent ghosted me"
  These are individual bad actors, not systemic software problems.
  Good: "every lead gen service I try overpromises and underdelivers" — systemic
✗ It requires changing human behavior, laws, or regulations
✗ It's a one-time event, not a recurring problem
✗ A good cheap solution already exists — before including, ask yourself:
  "Can someone solve this today with a $20/month tool or a quick Google search?"
  Bad: "translations are expensive" — DeepL, Lokalise, agencies exist
  Bad: "can't track email opens" — Mailtrack, HubSpot free tier exist
  Bad: "hard to schedule meetings" — Calendly free tier exists
  Good: "our 3PL doesn't expose an API so I hand-copy 200 orders daily" — no good solution
✗ Score < 3.0
✗ Pure recommendation request with no frustration expressed
✗ Founder validation post: "Would you pay for X?", "I'm building X, would you use it?"
  — these describe a solution, not a personal pain experience
✗ The person who suffers would not be the one paying for a fix

FOR QUOTES:
- Use ONLY text that actually appears in the search result
- If no clear quote exists, use the post title
- Never invent or paraphrase as a quote

FOR SOLUTION HINT — be specific:
- What exactly does the product do? (1 sentence)
- Who pays and roughly how much? (1 sentence, must match the person who suffers)
- Example: "A dashboard that tracks lead response SLAs and auto-escalates
  silent leads after 1 hour. Real estate brokers pay $49/month per agent seat."

Return ONLY valid JSON array, no markdown, no explanation.
Return [] if no pains meet the criteria above.

[
  {
    "title": "short pain title (max 8 words)",
    "quote": "exact text from the source (max 150 chars)",
    "source": "url",
    "niche": "niche name",
    "frequency": 4,
    "emotion": 5,
    "monetizable": true,
    "root_cause": "one sentence: why this problem exists structurally",
    "solution_hint": "specific product idea + who pays + price range",
    "score": 4.5
  }
]"""

    def validate(self):
        missing = []
        if not self.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if not self.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if missing:
            raise ValueError(f"Missing env variables: {', '.join(missing)}")
        return True

cfg = Config()