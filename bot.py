import os
import re
import logging
from dotenv import load_dotenv
from pymongo import MongoClient
from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.request import HTTPXRequest

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not TOKEN or not MONGO_URI:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or MONGO_URI in environment variables!")

# MongoDB Connection
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["house_bot"]
users_col = db["users"]
status_col = db["status"]


def is_admin(status: ChatMemberStatus) -> bool:
    """Helper to check if a user is an admin or owner."""
    return status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Generates the persistent custom keyboard menu."""
    keyboard = [
        ["in 🟢", "out 🔴"],
        ["/house_status", "/menu"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registers a user to the database."""
    user = update.effective_user
    chat = update.effective_chat

    # If used in a group chat, ensure only admins can run register
    if chat.type in ["group", "supergroup"]:
        member = await chat.get_member(user.id)
        if not is_admin(member.status):
            await update.message.reply_text("❌ Only group admins can register members.")
            return

    users_col.update_one(
        {"user_id": user.id},
        {"$set": {"name": user.first_name, "username": user.username}},
        upsert=True,
    )
    await update.message.reply_text(
        f"✅ Registered {user.first_name}! You can now use the status menu.",
        reply_markup=get_main_keyboard(),
    )


async def unregister(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unregisters a user from the database."""
    user = update.effective_user
    chat = update.effective_chat

    if chat.type in ["group", "supergroup"]:
        member = await chat.get_member(user.id)
        if not is_admin(member.status):
            await update.message.reply_text("❌ Only group admins can unregister members.")
            return

    result = users_col.delete_one({"user_id": user.id})
    status_col.delete_one({"user_id": user.id})

    if result.deleted_count > 0:
        await update.message.reply_text(f"🗑️ Unregistered {user.first_name}.")
    else:
        await update.message.reply_text(f"⚠️ {user.first_name} was not registered.")


async def house_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays current house status for all registered users."""
    users = list(users_col.find())
    if not users:
        await update.message.reply_text("No users registered yet. Use /register to join.")
        return

    statuses = {s["user_id"]: s.get("status", "out") for s in status_col.find()}

    msg = "<b>🏡 House Status:</b>\n\n"
    for u in users:
        u_status = statuses.get(u["user_id"], "out")
        badge = "🟢 IN" if u_status.lower() == "in" else "🔴 OUT"
        msg += f"• {u['name']}: {badge}\n"

    await update.message.reply_text(msg, parse_mode="HTML")


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Brings up the status menu."""
    await update.message.reply_text(
        "Here is your status control menu:",
        reply_markup=get_main_keyboard(),
    )


async def handle_status_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles status changes via button clicks or text commands."""
    user = update.effective_user
    text = update.message.text.strip().lower()

    # Verify registration
    is_registered = users_col.find_one({"user_id": user.id})
    if not is_registered:
        await update.message.reply_text("⚠️ You need to /register first before setting your status.")
        return

    new_status = "in" if "in" in text else "out"

    status_col.update_one(
        {"user_id": user.id},
        {"$set": {"status": new_status, "name": user.first_name}},
        upsert=True,
    )

    badge = "🟢 IN" if new_status == "in" else "🔴 OUT"
    await update.message.reply_text(f"Updated status for {user.first_name} to {badge}")


def main():
    # Pass proxy parameter explicitly via HTTPXRequest
    request_config = HTTPXRequest(
        proxy="http://proxy.server:3128",
        connect_timeout=30.0,
        read_timeout=30.0,
    )

    app = (
        Application.builder()
        .token(TOKEN)
        .request(request_config)
        .get_updates_request(request_config)
        .build()
    )

    # Command Handlers
    app.add_handler(CommandHandler("start", menu_command))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("house_status", house_status))
    app.add_handler(CommandHandler("menu", menu_command))

    # Regex Filter for exact button text matching
    status_filter = filters.Regex(re.compile(r"^(in|out|in 🟢|out 🔴)$", re.IGNORECASE))
    app.add_handler(MessageHandler(status_filter, handle_status_text))

    print("Bot is starting...")
    app.run_polling(poll_interval=2.0, timeout=20)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
