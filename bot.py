import os
import re
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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


# --- NapStopper & Render Health Check Handler ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Responds with 200 OK to keep Render awake and satisfy NapStopper."""
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot is active")

    def log_message(self, format, *args):
        """Suppress standard HTTP GET noise from logs."""
        return


def run_health_check_server():
    """Runs a lightweight web server on the port assigned by Render."""
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"Health check web server active on port {port}")
    server.serve_forever()


# --- Bot Helper Functions ---
def is_admin(status: ChatMemberStatus) -> bool:
    return status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]


def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        ["in 🟢", "out 🔴"],
        ["/house_status", "/menu"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


# --- Command Handlers ---
async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

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
    await update.message.reply_text(
        "Here is your status control menu:",
        reply_markup=get_main_keyboard(),
    )


async def handle_status_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip().lower()

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


# --- Main Execution Loop ---
def main():
    # Start the HTTP Health Check server in a background thread
    server_thread = threading.Thread(target=run_health_check_server, daemon=True)
    server_thread.start()

    app = Application.builder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", menu_command))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("house_status", house_status))
    app.add_handler(CommandHandler("menu", menu_command))

    status_filter = filters.Regex(re.compile(r"^(in|out|in 🟢|out 🔴)$", re.IGNORECASE))
    app.add_handler(MessageHandler(status_filter, handle_status_text))

    print("Bot is starting...")
    app.run_polling(poll_interval=2.0, timeout=20)


if __name__ == "__main__":
    main()
