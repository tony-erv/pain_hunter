import logging
import asyncio
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    ContextTypes, Application
)
from telegram.constants import ParseMode, ChatAction
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import cfg
from database import (
    init_db, save_user, save_pain,
    get_saved_pains, get_stats,
    get_all_user_ids, can_hunt,
    increment_hunt, cleanup_old_hashes
)
from agent import hunt_pains, format_digest
import random

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# -- Telegram Bot Handlers

async def send_pains(chat_id: int, pains: list, context: ContextTypes.DEFAULT_TYPE):
    """Send the list of pains to the user in a nicely formatted way."""
    messages = format_digest(pains)
    for msg in messages:
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        await asyncio.sleep(0.3)  # pause between messages to avoid flooding

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command: greet the user and explain how the bot works."""
    user = update.effective_user
    save_user(user.id, user.username or "", user.first_name or "")
    await update.message.reply_text(
        f"👋 Hey {user.first_name}!\n\n"
        "🔴 Pain Hunter — I find real user frustrations\n"
        "from Reddit and other sites, so you can build the right thing.\n\n"
        "Commands:\n"
        "/hunt [niche] — search for pains\n"
        "/digest — get today's digest now\n"
        "/saved — your saved pains\n"
        "/stats — your stats\n\n"
        "Example:\n"
        "/hunt accountants Canada\n"
        "/hunt freelance designers\n"
        "/hunt small business owners\n\n"
        "Every morning at 9:00 UTC I'll send you fresh pains automatically. 🎯",
        parse_mode=ParseMode.HTML
    )        

async def cmd_hunt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /hunt command: parse niche, run the agent, and send results."""
    user = update.effective_user
    save_user(user.id, user.username or "", user.first_name or "")

    # Parse niche from command arguments
    niche = " ".join(context.args).strip() if context.args else ""
    if not niche:
        await update.message.reply_text(
            "Please specify a niche:\n"
            "/hunt accountants Canada\n"
            "/hunt freelance designers",
            parse_mode=ParseMode.HTML
        )
        return

    # Rate limit check
    ok, wait = can_hunt(user.id)
    if not ok:
        await update.message.reply_text(
            f"⏳ Limit reached. Try again in {wait} min.\n"
            f"(max {cfg.MAX_HUNT_PER_HOUR} hunts per hour)"
        )
        return

    # Inform the user that the hunt has started and show typing action
    wait_msg = await update.message.reply_text(
        f"🔍 Hunting pains in: {niche}\n"
        "This takes ~30-60 seconds...",
        parse_mode=ParseMode.HTML
    )
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        # Call the blocking hunt_pains function in a separate thread to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        pains = await loop.run_in_executor(None, hunt_pains, niche, cfg.DIGEST_COUNT)

        # Save found pains to the database for this user
        for pain in pains:
            save_pain(user.id, pain)

        increment_hunt(user.id)

        # Delete the waiting message before sending results
        await wait_msg.delete()

        await send_pains(update.effective_chat.id, pains, context)

    except Exception as e:
        logger.error("hunt error: %s", e)
        await wait_msg.edit_text("❌ Something went wrong. Try again later.")

async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /digest command: send today's digest to the user."""
    user = update.effective_user
    save_user(user.id, user.username or "", user.first_name or "")
    msg = await update.message.reply_text("⏳ Generating digest...")
    await _send_digest_to_user(user.id, context)
    await msg.delete()

async def cmd_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /saved command: show the user's saved pains."""
    user_id = update.effective_user.id
    pains = get_saved_pains(user_id, limit=10)
    if not pains:
        await update.message.reply_text(
            "📭 No saved pains yet.\nUse /hunt to find some!"
        )
        return
    text = f"💾 Your last {len(pains)} pains:\n\n"
    for i, p in enumerate(pains, 1):
        stars = "⭐" * round(p["score"])
        money = "✅" if p["monetizable"] else "❌"
        text += (
            f"{i}. {p['title']} {money}\n"
            f"   {stars} · {p['niche']}\n\n"
        )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /stats command: show the user's stats and top niches."""
    user_id = update.effective_user.id
    s = get_stats(user_id)
    top = "\n".join(
        f"  • {r['niche']} ({r['cnt']})"
        for r in s["top_niches"]
    ) or "  No data yet"
    await update.message.reply_text(
        f"📊 Your stats:\n\n"
        f"Total pains found: {s['total']}\n"
        f"High potential (⭐4+): {s['high_potential']}\n\n"
        f"Top niches:\n{top}",
        parse_mode=ParseMode.HTML
    )

# -- Scheduler for daily digest

async def _send_digest_to_user(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Internal function to generate and send the daily digest to a specific user."""
    niches = random.sample(cfg.DEFAULT_NICHES, cfg.DIGEST_NICHES)
    all_pains = []
    for niche in niches:
        loop = asyncio.get_event_loop()
        pains = await loop.run_in_executor(None, hunt_pains, niche, 3)
        all_pains.extend(pains)
        for p in pains:
            save_pain(user_id, p)

    # Top-5 pains across all niches, sorted by score
    top = sorted(all_pains, key=lambda x: x.get("score", 0), reverse=True)[:cfg.DIGEST_COUNT]

    await context.bot.send_message(
        chat_id=user_id,
        text="🌅 Daily Pain Digest\nFresh opportunities from Reddit:",
        parse_mode=ParseMode.HTML
    )
    await send_pains(user_id, top, context)

async def daily_digest(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job to send the daily digest to all users. It selects random niches,"""
    user_ids = get_all_user_ids()
    logger.info("Daily digest: sending to %d users", len(user_ids))
    cleanup_old_hashes()
    for uid in user_ids:
        try:
            await _send_digest_to_user(uid, context)
        except Exception as e:
            logger.error("Digest failed for user %d: %s", uid, e)

# -- Main function to start the bot and scheduler

def main():
    init_db()
    app = ApplicationBuilder().token(cfg.TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("hunt",   cmd_hunt))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("saved",  cmd_saved))
    app.add_handler(CommandHandler("stats",  cmd_stats))

    # Scheduler for daily digest at specified hour and minute
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        daily_digest,
        trigger="cron",
        hour=cfg.DIGEST_HOUR,
        minute=cfg.DIGEST_MINUTE,
        kwargs={"context": None}   #  context will be passed by the job_queue when the job runs
    )

    # Start the scheduler and the bot
    app.job_queue.run_daily(
        daily_digest,
        time=__import__("datetime").time(
            hour=cfg.DIGEST_HOUR,
            minute=cfg.DIGEST_MINUTE
        )
    )

    logger.info("Bot started. Digest at %02d:%02d UTC",
                cfg.DIGEST_HOUR, cfg.DIGEST_MINUTE)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()