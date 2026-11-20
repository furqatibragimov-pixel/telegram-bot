import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- CONFIG ----------
# We will set this in Render's dashboard later. 
# For now, it will look for an environment variable called BOT_TOKEN.
TOKEN = os.environ.get("BOT_TOKEN")
# ----------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ---------- RENDER WEB SERVER KEEP-ALIVE ----------
# Render needs a web server to bind to a port so it doesn't shut down your app.
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()
# ----------------------------------------------

# ---------- COMMAND HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Hello {name}! 👋\n"
        f"I'm your first bot.\n\n"
        f"Try these commands:\n"
        f"/help — see what I can do\n"
        f"/menu — show buttons\n"
        f"/about — who made me"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Here's what I can do:\n"
        "• /start — greet you\n"
        "• /help — show this message\n"
        "• /menu — show interactive buttons\n"
        "• /about — info about me\n\n"
        "You can also send me any text and I'll echo it back."
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("I'm a beginner bot built with Python 🐍")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("👍 Say hi", callback_data="hi"),
            InlineKeyboardButton("📅 Today's date", callback_data="date"),
        ],
        [InlineKeyboardButton("❌ Close", callback_data="close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Pick an option:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    from datetime import date

    if query.data == "hi":
        await query.edit_message_text("Hi there! 😄")
    elif query.data == "date":
        await query.edit_message_text(f"Today is {date.today().isoformat()}")
    elif query.data == "close":
        await query.edit_message_text("Closed. Send /menu to reopen.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"You said: {update.message.text}")

# ---------- MAIN ----------
def main():
    # Start the dummy web server in the background
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    print("✅ Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()