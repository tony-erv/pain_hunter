import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
    TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
    MODEL               = os.getenv("MODEL", "gpt-4o-mini")
    
    DATA_DIR            = os.getenv("DATA_DIR", ".")
    DB_PATH             = os.path.join(DATA_DIR, "pains.db")

    # ---- Digest schedule:
    DIGEST_HOUR         = int(os.getenv("DIGEST_HOUR", 9))  # 9 AM by default
    DIGEST_MINUTE       = int(os.getenv("DIGEST_MINUTE", 0))    
    DIGEST_COUNT        = 5
    DIGEST_NICHES       = 3

    # ---- Limitations:
    MAX_HUNT_PER_HOUR   = 3
    MAX_SEARCH_RESULTS  = 5
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

    QUERY_TEMPLATES = [
    "site:reddit.com {niche} frustrated manual process",
    "site:reddit.com {niche} wish there was a tool",
    "site:reddit.com {niche} hate annoying problem",
    "site:reddit.com {niche} automate impossible broken",
    "site:reddit.com {niche} tired of doing manually",
]

    QUERY_GEN_PROMPT = """You are a market research expert hunting for startup opportunities.
Given a niche, generate 5 search queries that find MONETIZABLE pains —
problems people would pay to have solved.

TARGET: posts where people describe a recurring painful manual process,
ask if a tool exists, or complain that existing solutions are too expensive.

Search sources (use the mix below):
- 3 queries: site:reddit.com
- 1 query:   site:news.ycombinator.com
- 1 query:   site:indiehackers.com

Query construction rules:
- Niche keywords: NO quotes, free keywords only
- Each query must have ONE of these high-signal phrases (no quotes):
    "is there a tool"
    "I have to manually"
    "I spend hours"
    "does anyone else"
    "too expensive"
    "I built this because"
    "I'd pay for"
    "looking for software"
    "automate this"
    "wish there was"
- For Reddit: vary subreddits when relevant (r/sysadmin, r/Accounting, etc.)
- NO generic markers like "hate", "frustrated", "problem" — too noisy

Return ONLY a JSON array of 5 strings, nothing else.

Examples for niche "accountants Canada":
[
  "site:reddit.com r/Accounting accountants Canada is there a tool invoicing automation",
  "site:reddit.com Canadian accountant I have to manually reconcile every month",
  "site:reddit.com bookkeeping Canada too expensive software small business",
  "site:news.ycombinator.com accountants Canada I built this because no tool existed",
  "site:indiehackers.com accounting Canada automate this wish there was"
]

Examples for niche "system administrators":
[
  "site:reddit.com r/sysadmin I spend hours monitoring servers manually",
  "site:reddit.com sysadmin is there a tool patch management automated",
  "site:reddit.com r/homelab system administrator too expensive enterprise only",
  "site:news.ycombinator.com sysadmin I built this because monitoring was broken",
  "site:indiehackers.com system administrators I'd pay for automated alerts"
]"""

    PAIN_ANALYSIS_PROMPT = """You are a brutal startup opportunity analyst.
Your job: find ONLY pains worth building a business around.

Analyze the provided search results and extract user pain points.

SCORING CRITERIA:
- frequency (1-5): how many different people face this repeatedly?
  1 = one person, one time
  5 = thousands of people, every week
- emotion (1-5): frustration intensity
  1 = mild inconvenience
  5 = costs them money/clients/time, they're furious
- monetizable (true/false): would someone pay $10-500/month to fix this?
- score = (frequency + emotion) / 2

INCLUDE a pain if ALL of these are true:
✓ Affects many people repeatedly (not a one-time incident)
✓ No obvious good solution exists yet (or existing ones are too expensive)
✓ Can be solved with software, automation, or a bot
✓ Score >= 3.0
✓ Person describes a specific negative experience or recurring frustration
✓ Language signals pain: "I hate", "I have to manually", "costs me", 
  "I keep losing", "every time I", "I've tried everything"

EXCLUDE a pain if ANY of these are true:
✗ It's a bug or policy of one specific company (e.g. "realtor.com glitch")
✗ It requires changing human behavior, laws, or regulations
✗ It's a one-time event ("my agent ignored me once")
✗ A good cheap solution already exists
✗ Score < 3.0
✗ It's a question asking for recommendations (not a complaint about a problem)
✗ Posts starting with "What do you use for...", "Which tool do you recommend...",
  "Has anyone tried..." — these are research, not pain signals

FOR QUOTES:
- Use ONLY text that actually appears in the search result
- If no clear quote exists, use the post title
- Never invent or paraphrase as a quote

FOR SOLUTION HINT — be specific:
- What exactly does the product do? (1 sentence)
- Who pays and roughly how much? (1 sentence)
- Example: "A Slack bot that monitors response times between agents and auto-escalates 
  after 24h silence. Realtors pay $29/month per agent."

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
        """Check that all required env variables are set."""
        missing = []
        if not self.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if not self.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if missing:
            raise ValueError(f"Missing env variables: {', '.join(missing)}")
        return True
    
cfg = Config()