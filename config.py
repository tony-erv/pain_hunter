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

    QUERY_GEN_PROMPT = """You are a market research expert.
Given a niche, generate 5 search queries to find real user complaints on Reddit.

Rules:
- Use these sources (mix them):
  site:reddit.com — general frustrations
  site:news.ycombinator.com — tech founders and developers
  site:indiehackers.com — bootstrapped founders
- 3 queries on reddit.com, 1 on news.ycombinator.com, 1 on indiehackers.com
- Do NOT use site:quora.com (poor results)
- Keep niche as free keywords, no quotes around them
- Add ONE pain marker per query (no quotes):
  frustrated, annoying, automate, hate, manual, problem,
  tired, wish, impossible, broken, sucks
- Vary subreddits when relevant (e.g. r/sysadmin, r/accounting)
- Return ONLY a JSON array of 5 strings, nothing else

Examples for niche "accountants Canada":
[
  "site:reddit.com accountants Canada frustrated manual process",
  "site:reddit.com Canadian accountant tax software problem",
  "site:reddit.com r/Accounting Canada wish automation tool",
  "site:reddit.com bookkeeping Canada annoying client invoices",
  "site:reddit.com CPA Canada hate manual data entry 2024"
]"""

    PAIN_ANALYSIS_PROMPT = """You are a startup opportunity analyst.
Analyze these Reddit search results and extract user pain points.

For each pain found, score it:
- frequency (1-5): how often is this type of problem mentioned?
- emotion (1-5): frustration level (5 = very angry/frustrated)
- monetizable (true/false): can this be solved with software/SaaS/bot?
- score: average of frequency and emotion (e.g. freq=4, emotion=3 → score=3.5)

IMPORTANT:
- Extract pain points even from general discussions if they contain complaints
- A post title like "My rant on CPA" or "frustrated with tax software" IS a pain
- If snippet is short, infer the pain from the title and context
- Include pains with score >= 2.0 (be generous)
- Return empty array [] ONLY if results are completely irrelevant (cooking, sex, etc.)

Return ONLY valid JSON array, no markdown, no explanation:
[
  {
    "title": "short pain title (max 8 words)",
    "quote": "best quote from title+snippet (max 150 chars)",
    "source": "url",
    "niche": "niche name",
    "frequency": 3,
    "emotion": 4,
    "monetizable": true,
    "root_cause": "why this pain exists (1 sentence)",
    "solution_hint": "how to solve with code/SaaS/bot (1-2 sentences)"
    "score": 3.5
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