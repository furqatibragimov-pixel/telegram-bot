import logging
import os
import threading
import sqlite3
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ---------- CONFIG & WEB SERVER ----------
TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# ---------- DATABASE SETUP ----------
# This creates a file called tasks.db next to your bot.py
conn = sqlite3.connect('tasks.db', check_same_thread=False)
cursor = conn.cursor()

# Create the table if it doesn't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_name TEXT,
        task_time TEXT,
        last_completed DATE
    )
''')
conn.commit()

# ---------- CONVERSATION STATES ----------
ASK_TASK_NAME, ASK_TASK_TIME = range(2)

# ---------- COMMAND HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I am your Planner Bot. 📅\n\n"
        "Commands:\n"
        "/add - Add a new daily task (e.g., Drink medicine at 08:00)\n"
        "/list - See your tasks and mark them done\n"
        "/delete - Delete a task\n"
    )

# --- ADD TASK CONVERSATION ---
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What is the task? (e.g., Drink medicine, Go to gym)\n\nSend /cancel to stop.")
    return ASK_TASK_NAME

async def add_task_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['task_name'] = update.message.text
    await update.message.reply_text("At what time should I remind you? Please use 24-hour format HH:MM (e.g., 08:00 or 20:30).")
    return ASK_TASK_TIME

async def add_task_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text
    try:
        # Check if the user typed a valid time
        datetime.datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await update.message.reply_text("Invalid format. Please use HH:MM (like 08:00 or 20:30).")
        return ASK_TASK_TIME

    task_name = context.user_data['task_name']
    user_id = update.effective_user.id

    # Save to database
    cursor.execute("INSERT INTO tasks (user_id, task_name, task_time, last_completed) VALUES (?, ?, ?, ?)",
                   (user_id, task_name, time_str, "2000-01-01"))
    conn.commit()

    await update.message.reply_text(f"✅ Saved! I will remind you to '{task_name}' every day at {time_str}.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END

# --- LIST TASKS ---
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("SELECT id, task_name, task_time, last_completed FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()

    if not tasks:
        await update.message.reply_text("You have no tasks. Use /add to create one.")
        return

    today = str(datetime.date.today())
    keyboard = []
    
    message = "📋 **Your Daily Tasks:**\n\n"
    for t in tasks:
        task_id, name, time_str, last_completed = t
        status = "✅" if last_completed == today else "⏳"
        message += f"{status} {time_str} - {name}\n"
        
        if last_completed != today:
            keyboard.append([InlineKeyboardButton(f"Mark '{name}' as Done", callback_data=f"done_{task_id}")])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

# --- MARK AS DONE ---
async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    task_id = query.data.split("_")[1]
    today = str(datetime.date.today())
    
    cursor.execute("UPDATE tasks SET last_completed = ? WHERE id = ?", (today, task_id))
    conn.commit()
    
    await query.edit_message_text("🎉 Great job! Task marked as completed for today.")

# --- DELETE TASK ---
async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("SELECT id, task_name FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()

    if not tasks:
        await update.message.reply_text("You have no tasks to delete.")
        return

    keyboard = [[InlineKeyboardButton(f"❌ {t[1]}", callback_data=f"del_{t[0]}")] for t in tasks]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Which task do you want to delete?", reply_markup=reply_markup)

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = query.data.split("_")[1]
    
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    await query.edit_message_text("🗑️ Task deleted.")

# ---------- THE SCHEDULER (REMINDERS) ----------
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    # This function runs every minute in the background
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")
    today = str(now.date())

    # Find tasks that match the current time and haven't been done today
    cursor.execute("SELECT id, user_id, task_name FROM tasks WHERE task_time = ? AND last_completed != ?", (current_time, today))
    due_tasks = cursor.fetchall()

    for task in due_tasks:
        task_id, user_id, task_name = task
        try:
            keyboard = [[InlineKeyboardButton("✅ Mark as Done", callback_data=f"done_{task_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=user_id, 
                text=f"🔔 **Reminder:** It's time for: **{task_name}**!",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Failed to send reminder: {e}")

# ---------- MAIN ----------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    # Add Task Conversation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_task_start)],
        states={
            ASK_TASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_name)],
            ASK_TASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("delete", delete_task))
    app.add_handler(CallbackQueryHandler(mark_done, pattern="^done_"))
    app.add_handler(CallbackQueryHandler(confirm_delete, pattern="^del_"))

    # Setup the scheduler to run every 60 seconds
    job_queue = app.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)

    print("✅ Bot is running with Database & Scheduler.")
    app.run_polling()

if __name__ == "__main__":
    main()
