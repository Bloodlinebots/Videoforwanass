import os
import logging
import random
import string
import asyncio
from dotenv import load_dotenv
from pymongo import MongoClient
from telegram import Update, ChatMemberAdministrator
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# Load .env vars
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
BOT_USERNAME = os.getenv("BOT_USERNAME")  # e.g. yourbotname without @

# Logging
logging.basicConfig(level=logging.INFO)

# MongoDB setup
client = MongoClient(MONGO_URI)
db = client["video_bot"]
channels = db["channels"]
sessions = db["sessions"]
links = db["links"]

# Token Generator
def generate_token(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# /connect
async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        return await update.message.reply_text("❌ Usage: /connect -100xxxxxxxxx")
    channel_id = int(context.args[0])
    try:
        member = await context.bot.get_chat_member(channel_id, context.bot.id)
        if isinstance(member, ChatMemberAdministrator):
            channels.update_one({"channel_id": channel_id}, {"$set": {"admin_id": user.id}}, upsert=True)
            await update.message.reply_text(f"✅ Connected to channel `{channel_id}`!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Bot is not admin in the channel.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: `{e}`", parse_mode="Markdown")

# /set <count>
async def set_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("❌ Usage: /set 1000")
    try:
        count = int(context.args[0])
        sessions.update_one(
            {"admin_id": user_id},
            {"$set": {"step": "awaiting_msg", "total": count, "videos": []}},
            upsert=True
        )
        await update.message.reply_text("✅ Okay! Now send the message to display to users.")
    except ValueError:
        await update.message.reply_text("❌ Invalid number.")

# Handle message / video upload steps
async def capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = sessions.find_one({"admin_id": user_id})

    if not session:
        return

    if session.get("step") == "awaiting_msg":
        text = update.message.text or update.message.caption or "No message"
        sessions.update_one({"admin_id": user_id}, {"$set": {"msg_text": text, "step": "collecting_videos"}})
        await update.message.reply_text(f"✅ Message saved. Now send {session['total']} videos.")

    elif session.get("step") == "collecting_videos":
        if update.message.video:
            video_id = update.message.video.file_id
            videos = session.get("videos", [])
            videos.append(video_id)
            total = session["total"]

            sessions.update_one({"admin_id": user_id}, {"$set": {"videos": videos}})

            if len(videos) == total:
                # All videos done – generate token
                token = generate_token()
                links.insert_one({
                    "token": token,
                    "message": session["msg_text"],
                    "videos": videos
                })
                sessions.delete_one({"admin_id": user_id})

                link = f"https://t.me/{BOT_USERNAME}?start={token}"
                await update.message.reply_text(f"✅ All videos received!\nHere is your link:\n\n{link}")
            else:
                await update.message.reply_text(f"📥 Received {len(videos)}/{total} videos.")
        else:
            await update.message.reply_text("❌ Please send a video.")

# /start or /start <token>
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        token = context.args[0]
        data = links.find_one({"token": token})
        if not data:
            return await update.message.reply_text("❌ Invalid or expired link.")
        
        # Send message
        await update.message.reply_text(data["message"])
        # Send videos 1 by 1 with delay
        for vid in data["videos"]:
            try:
                await context.bot.send_video(chat_id=update.effective_chat.id, video=vid)
                await asyncio.sleep(3)
            except Exception as e:
                logging.error(f"❌ Error sending video: {e}")
        await update.message.reply_text("✅ All videos sent.")
    else:
        await update.message.reply_text("👋 Welcome! Please use a valid start link.")

# Run the bot
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("connect", connect))
    app.add_handler(CommandHandler("set", set_count))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, capture_message))

    print("🤖 Bot running...")
    app.run_polling()
