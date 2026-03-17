import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
    TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
    MODEL               = os.getenv("MODEL", "gpt-4o-mini")
    

