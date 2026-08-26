import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pymongo import MongoClient
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Environment variables matching Render settings
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
PORT = int(os.getenv("PORT", 8080))

if not TOKEN or not MONGO_URI:
  logger.error(
      "Missing TELEGRAM_BOT_TOKEN or MONGO_URI environment variables."
  )
  sys.exit(1)

# MongoDB setup
client = MongoClient(MONGO_URI)
db = client["house_bot"]
users_col = db["users"]
status_col = db["status"]


# Simple HTTP Server for Render Web Service Health Checks
class HealthCheckHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.send_header("Content-type", "text/plain")
    self.end_headers()
    self.wfile.write(b"OK")

  def log_message(self, format, *args):
    return  # Silence HTTP access logs


def run_health_check_server():
  server_address = ("", PORT)
  httpd = HTTPServer(server_address, HealthCheckHandler)
  logger.info(f"Health check server running on port {PORT}")
  httpd.serve_forever()


# Persistent Custom Telegram Keyboard
KEYBOARD = ReplyKeyboardMarkup(
    [["in 🟢", "out 🔴"], ["/house_status", "/menu"]],
    resize_keyboard=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      "👋 Welcome to Maximus - House Manager Bot!\n\n"
      "Use /register to join the household roster.\n"
      "Tap the status buttons below to update your location.",
      reply_markup=KEYBOARD,
  )


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  chat = update.effective_chat

  if chat.type in ["group", "supergroup"]:
    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ["administrator", "creator"]:
      await update.message.reply_text(
          "⚠️ Only group admins can register members."
      )
      return

  users_col.update_one(
      {"user_id": user.id},
      {
          "$set": {
              "user_id": user.id,
              "name": user.first_name,
              "username": user.username,
          }
      },
      upsert=True,
  )

  if not status_col.find_one({"user_id": user.id}):
    status_col.insert_one(
        {"user_id": user.id, "name": user.first_name, "status": "out"}
    )

  await update.message.reply_text(
      f"✅ Registered <b>{user.first_name}</b> in the household roster!",
      parse_mode="HTML",
      reply_markup=KEYBOARD,
  )


async def unregister(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  chat = update.effective_chat

  if chat.type in ["group", "supergroup"]:
    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ["administrator", "creator"]:
      await update.message.reply_text(
          "⚠️ Only group admins can unregister members."
      )
      return

  users_col.delete_one({"user_id": user.id})
  status_col.delete_one({"user_id": user.id})

  await update.message.reply_text(
      f"🗑️ Unregistered {user.first_name} from the household roster."
  )


async def handle_status_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  text = update.message.text.strip().lower()

  is_registered = users_col.find_one({"user_id": user.id})
  if not is_registered:
    await update.message.reply_text(
        "⚠️ You need to /register first before setting your status."
    )
    return

  new_status = "in" if "in" in text else "out"

  status_col.update_one(
      {"user_id": user.id},
      {"$set": {"status": new_status, "name": user.first_name}},
      upsert=True,
  )

  # Correct Telegram reaction syntax (pass emoji inside a list)
  try:
    await update.message.set_reaction(reaction=["👍"])
  except Exception as e:
    logger.warning(f"Reaction failed: {e}")
    await update.message.reply_text("👍")


async def house_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
  users = list(users_col.find())
  if not users:
    await update.message.reply_text(
        "No users registered yet. Use /register to join."
    )
    return

  statuses = {s["user_id"]: s.get("status", "out") for s in status_col.find()}

  any_in = any(status.lower() == "in" for status in statuses.values())

  if not any_in:
    await update.message.reply_text(
        "🔒 <b>house locked</b>", parse_mode="HTML"
    )
    return

  msg = "🏡 <b>House Status:</b>\n\n"
  for u in users:
    u_status = statuses.get(u["user_id"], "out")
    badge = "🟢 IN" if u_status.lower() == "in" else "🔴 OUT"
    msg += f"• {u['name']}: {badge}\n"

  await update.message.reply_text(msg, parse_mode="HTML")


def main():
  health_thread = threading.Thread(target=run_health_check_server, daemon=True)
  health_thread.start()

  app = Application.builder().token(TOKEN).build()

  app.add_handler(CommandHandler(["start", "menu"], start))
  app.add_handler(CommandHandler("register", register))
  app.add_handler(CommandHandler("unregister", unregister))
  app.add_handler(CommandHandler("house_status", house_status))

  app.add_handler(
      MessageHandler(
          filters.Regex(re.compile(r"^(in 🟢|out 🔴|in|out)$", re.IGNORECASE)),
          handle_status_text,
      )
  )

  logger.info("Starting Maximus Telegram Bot...")
  app.run_polling()


if __name__ == "__main__":
  main()

