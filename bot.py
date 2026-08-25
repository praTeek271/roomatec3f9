import os
import re
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

# Load environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not TOKEN or not MONGO_URI:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or MONGO_URI environment variables.")

# Initialize MongoDB
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["roommate_db"]
members_col = db["members"]

# Permanent floating quick-tap keyboard
QUICK_KEYBOARD = [["In 🟢", "Out 🔴"]]
quick_reply_markup = ReplyKeyboardMarkup(QUICK_KEYBOARD, resize_keyboard=True, persistent=True)

def get_all_members():
    """Retrieves all registered roommates from MongoDB."""
    return list(members_col.find({}, {"_id": 0}))

def is_house_locked():
    """Returns True if all registered members are 'out'."""
    members = get_all_members()
    if not members:
        return False
    return all(m.get("status") == "out" for m in members)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registers the sender in MongoDB."""
    user = update.effective_user
    user_id = str(user.id)
    
    existing = members_col.find_one({"user_id": user_id})
    username = f"@{user.username}" if user.username else user.first_name
    
    if not existing:
        members_col.insert_one({
            "user_id": user_id,
            "username": username,
            "name": user.first_name,
            "status": "in"
        })
        await update.message.reply_text(
            f"✅ {user.first_name} added to the roommate list (Status: in).",
            reply_markup=quick_reply_markup
        )
    else:
        await update.message.reply_text(
            f"⚠️ {user.first_name}, you are already registered.",
            reply_markup=quick_reply_markup
        )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Brings back the floating quick-tap buttons."""
    await update.message.reply_text(
        "Tap below to quickly update your status:",
        reply_markup=quick_reply_markup
    )

async def unregister(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only command to remove a user from MongoDB."""
    chat_id = update.effective_chat.id
    sender_id = update.effective_user.id
    
    # Check if sender is group Admin or Owner
    if update.effective_chat.type in ["group", "supergroup"]:
        member = await context.bot.get_chat_member(chat_id, sender_id)
        if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("🚫 Access denied: Only group admins can unregister users.")
            return

    if not context.args:
        await update.message.reply_text("Please provide a username. Usage: /unregister @username")
        return
        
    target_raw = context.args[0].strip().lstrip("@")
    
    # Case-insensitive search for target username
    target = members_col.find_one({"username": {"$regex": f"^@{target_raw}$", "$options": "i"}})
    
    if not target:
        await update.message.reply_text(f"❌ User @{target_raw} not found in the registered list.")
        return

    was_locked = is_house_locked()
    deleted_name = target["name"]
    
    members_col.delete_one({"user_id": target["user_id"]})
    await update.message.reply_text(f"🗑️ {deleted_name} (@{target_raw}) removed from the roommate list.")

    # Check if removing this user locked the house
    if not was_locked and get_all_members() and is_house_locked():
        await update.message.reply_text("🔒 house locked")

async def house_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the house status and roommate list."""
    members = get_all_members()
    
    if not members:
        await update.message.reply_text("No roommates registered. Use /register first.")
        return
        
    locked = is_house_locked()
    state_text = "LOCKED 🔒" if locked else "UNLOCKED 🔓"
    
    msg = f"<b>House Status:</b> {state_text}\n\n"
    for m in members:
        status_icon = "🟢" if m['status'] == "in" else "🔴"
        msg += f"• {m['name']}: {m['status'].upper()} {status_icon}\n"
        
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=quick_reply_markup)

async def handle_status_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Listens strictly for exact 'in' or 'out' status triggers."""
    user = update.effective_user
    user_id = str(user.id)
    
    member = members_col.find_one({"user_id": user_id})
    if not member:
        return
        
    clean_text = update.message.text.strip().lower()
    was_locked = is_house_locked()
    
    if clean_text in ["in", "in 🟢"]:
        members_col.update_one({"user_id": user_id}, {"$set": {"status": "in"}})
        try:
            await update.message.set_reaction(reaction="👍")
        except Exception:
            pass
            
    elif clean_text in ["out", "out 🔴"]:
        members_col.update_one({"user_id": user_id}, {"$set": {"status": "out"}})
        try:
            await update.message.set_reaction(reaction="👍")
        except Exception:
            pass
            
        if not was_locked and is_house_locked():
            await update.message.reply_text("🔒 house locked")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("house_status", house_status))
    app.add_handler(CommandHandler("menu", menu_command))
    
    # Strict regex: matches only exact "in", "out", "In 🟢", or "Out 🔴"
    status_filter = filters.Regex(re.compile(r"^(in|out|in 🟢|out 🔴)$", re.IGNORECASE))
    app.add_handler(MessageHandler(status_filter, handle_status_text))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
