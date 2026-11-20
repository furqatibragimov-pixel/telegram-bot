import logging
import os
import threading
import sqlite3
import datetime
from zoneinfo import ZoneInfo
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
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

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
conn = sqlite3.connect('tasks.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_name TEXT,
        task_time TEXT,
        task_days TEXT,
        last_completed DATE,
        last_reminded DATETIME
    )
''')
# Safely add the new column if upgrading from the old version
try:
    cursor.execute("ALTER TABLE tasks ADD COLUMN task_days TEXT DEFAULT '0,1,2,3,4,5,6'")
except sqlite3.OperationalError:
    pass 
conn.commit()

# ---------- CONVERSATION STATES ----------
ASK_TASK_NAME, ASK_TASK_TIME, ASK_TASK_DAYS = range(3)

# ---------- HELPER FUNCTIONS ----------
def parse_days(text):
    text = text.lower().strip()
    if "daily" in text or "everyday" in text or "every day" in text:
        return "0,1,2,3,4,5,6"
    if "weekday" in text:
        return "0,1,2,3,4"
    if "weekend" in text:
        return "5,6"
    
    days_map = {
        "mon": 0, "monday": 0,
        "tue": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6
    }
    selected_days = set()
    for word in text.replace(",", " ").split():
        if word in days_map:
            selected_days.add(str(days_map[word]))
            
    if not selected_days:
        return None
    return ",".join(sorted(list(selected_days)))

def format_days(days_str):
    days_arr = days_str.split(",")
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if len(days_arr) == 7: return "Daily"
    if days_str == "0,1,2,3,4": return "Weekdays"
    if days_str == "5,6": return "Weekends"
    return ", ".join([names[int(d)] for d in days_arr if d.isdigit() and int(d) < 7])

# ---------- COMMAND HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I am your Planner Bot. 📅\n\n"
        "Commands:\n"
        "/add - Add a new task\n"
        "/list - See today's tasks\n"
        "/delete - Delete a task\n"
    )

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What is the task? (e.g., Go to gym)\n\nSend /cancel to stop.")
    return ASK_TASK_NAME

async def add_task_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['task_name'] = update.message.text
    await update.message.reply_text("At what time? Please use 24-hour format HH:MM (e.g., 08:00 or 20:30).")
    return ASK_TASK_TIME

async def add_task_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text
    try:
        datetime.datetime.strptime(time_str, "%H:%M")
        context.user_data['task_time'] = time_str
        await update.message.reply_text("Which days?\nType: 'daily', 'weekdays', 'weekends', or specific days like 'mon, wed, fri'.")
        return ASK_TASK_DAYS
    except ValueError:
        await update.message.reply_text("Invalid format. Please use HH:MM (like 08:00 or 20:30).")
        return ASK_TASK_TIME

async def add_task_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days_str = parse_days(update.message.text)
    if not days_str:
        await update.message.reply_text("I didn't understand. Please type 'daily', 'weekdays', or 'mon, wed, fri'.")
        return ASK_TASK_DAYS

    task_name = context.user_data['task_name']
    task_time = context.user_data['task_time']
    user_id = update.effective_user.id

    cursor.execute("INSERT INTO tasks (user_id, task_name, task_time, task_days, last_completed, last_reminded) VALUES (?, ?, ?, ?, ?, ?)",
                   (user_id, task_name, task_time, days_str, "2000-01-01", None))
    conn.commit()

    readable_days = format_days(days_str)
    await update.message.reply_text(f"✅ Saved!\n📌 Task: {task_name}\n⏰ Time: {task_time}\n📅 Days: {readable_days}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END

# --- LIST TASKS ---
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = datetime.datetime.now(TASHKENT_TZ)
    today_str = str(today.date())
    today_weekday = str(today.weekday())

    cursor.execute("SELECT id, task_name, task_time, task_days, last_completed FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()

    # Filter tasks for today's weekday
    todays_tasks = [t for t in tasks if today_weekday in t[3].split(",")]

    if not todays_tasks:
        await update.message.reply_text("You have no tasks scheduled for today. 🎉")
        return

    keyboard = []
    message = "📋 **Your Tasks for Today:**\n\n"
    
    for t in todays_tasks:
        task_id, name, time_str, days, last_completed = t
        status = "✅" if last_completed == today_str else "⏳"
        message += f"{status} {time_str} - {name}\n"
        
        if last_completed != today_str:
            keyboard.append([InlineKeyboardButton(f"Mark '{name}' as Done", callback_data=f"done_{task_id}")])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

# --- MARK AS DONE ---
async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    task_id = query.data.split("_")[1]
    today = str(datetime.datetime.now(TASHKENT_TZ).date())
    
    cursor.execute("UPDATE tasks SET last_completed = ?, last_reminded = NULL WHERE id = ?", (today, task_id))
    conn.commit()
    
    await query.edit_message_text("🎉 Great job! Task completed. I will stop reminding you today.")

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

# ---------- THE SCHEDULER (NAGGING REMINDERS) ----------
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(TASHKENT_TZ)
    today_date = str(now.date())
    today_weekday = str(now.weekday())
    current_time = now.strftime("%H:%M")

    # Get tasks that are NOT completed today
    cursor.execute("SELECT id, user_id, task_name, task_time, task_days, last_reminded FROM tasks WHERE last_completed != ?", (today_date,))
    due_tasks = cursor.fetchall()

    for task in due_tasks:
        task_id, user_id, task_name, task_time, task_days, last_reminded_str = task
        
        # 1. Check if today is the correct day for this task
        if today_weekday not in task_days.split(","):
            continue

        # 2. Check if it's time to remind
        if current_time >= task_time:
            should_remind = False
            
            if not last_reminded_str:
                should_remind = True
            else:
                last_reminded = datetime.datetime.strptime(last_reminded_str, "%Y-%m-%d %H:%M:%S")
                # Remind again if 60 minutes have passed
                if (now - last_reminded).total_seconds() >= 3600:
                    should_remind = True
                    
            if should_remind:
                try:
                    keyboard = [[InlineKeyboardButton("✅ Mark as Done", callback_data=f"done_{task_id}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await context.bot.send_message(
                        chat_id=user_id, 
                        text=f"🔔 **Reminder:** Have you done: **{task_name}**? (Scheduled for {task_time})",
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
                    cursor.execute("UPDATE tasks SET last_reminded = ? WHERE id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), task_id))
                    conn.commit()
                except Exception as e:
                    print(f"Failed to send reminder: {e}")

# ---------- MAIN ----------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_task_start)],
        states={
            ASK_TASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_name)],
            ASK_TASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_time)],
            ASK_TASK_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_days)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("delete", delete_task))
    app.add_handler(CallbackQueryHandler(mark_done, pattern="^done_"))
    app.add_handler(CallbackQueryHandler(confirm_delete, pattern="^del_"))

    job_queue = app.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)

    print("✅ Bot is running with Weekly Scheduler & Nagging Reminders.")
    app.run_polling()

if __name__ == "__main__":
    main()
