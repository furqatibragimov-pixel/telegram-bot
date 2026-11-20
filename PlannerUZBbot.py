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
        task_type TEXT,
        task_time TEXT,
        task_days TEXT,
        deadline_date TEXT,
        last_completed DATE,
        last_reminded DATETIME
    )
''')
# Safely add new columns to your existing database
try: cursor.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'recurring'")
except sqlite3.OperationalError: pass
try: cursor.execute("ALTER TABLE tasks ADD COLUMN deadline_date TEXT")
except sqlite3.OperationalError: pass
conn.commit()

# ---------- CONVERSATION STATES ----------
(ASK_NAME, ASK_TYPE, ASK_TIME, ASK_DAYS, ASK_DATE) = range(5)

# ---------- HELPER FUNCTIONS ----------
def parse_days(text):
    text = text.lower().strip()
    if "daily" in text or "everyday" in text or "every day" in text: return "0,1,2,3,4,5,6"
    if "weekday" in text: return "0,1,2,3,4"
    if "weekend" in text: return "5,6"
    days_map = {"mon":0,"monday":0,"tue":1,"tuesday":1,"wed":2,"wednesday":2,"thu":3,"thursday":3,"fri":4,"friday":4,"sat":5,"saturday":5,"sun":6,"sunday":6}
    selected = set()
    for word in text.replace(",", " ").split():
        if word in days_map: selected.add(str(days_map[word]))
    return ",".join(sorted(list(selected))) if selected else None

def format_days(days_str):
    if not days_str: return ""
    days_arr = days_str.split(",")
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if len(days_arr) == 7: return "Daily"
    if days_str == "0,1,2,3,4": return "Weekdays"
    if days_str == "5,6": return "Weekends"
    return ", ".join([names[int(d)] for d in days_arr if d.isdigit() and int(d) < 7])

# ---------- ADD TASK FLOW ----------
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What is the task? (e.g., Drink drugs / Finish budget)\n\nSend /cancel to stop.")
    return ASK_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("🔁 Recurring (Daily/Weekly)", callback_data="type_recurring")],
        [InlineKeyboardButton("📌 One-Time Deadline", callback_data="type_deadline")]
    ]
    await update.message.reply_text("Is this a recurring task or a one-time deadline?", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_TYPE

async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['type'] = "recurring" if query.data == "type_recurring" else "deadline"
    
    if context.user_data['type'] == "deadline":
        await query.edit_message_text("What is the deadline date? Please use YYYY-MM-DD format (e.g., 2024-12-31).")
        return ASK_DATE
    else:
        await query.edit_message_text("At what time? Please use 24-hour format HH:MM (e.g., 08:00 or 20:30).")
        return ASK_TIME

async def add_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = update.message.text
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
        context.user_data['deadline'] = date_str
        await update.message.reply_text("At what time should I remind you on that day? (HH:MM)")
        return ASK_TIME
    except ValueError:
        await update.message.reply_text("Invalid format. Please use YYYY-MM-DD (like 2024-12-31).")
        return ASK_DATE

async def add_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text
    try:
        datetime.datetime.strptime(time_str, "%H:%M")
        context.user_data['time'] = time_str
        
        if context.user_data['type'] == "deadline":
            # Save deadline task immediately
            user_id = update.effective_user.id
            cursor.execute("INSERT INTO tasks (user_id, task_name, task_type, task_time, deadline_date, last_completed) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, context.user_data['name'], 'deadline', time_str, context.user_data['deadline'], "2000-01-01"))
            conn.commit()
            await update.message.reply_text(f"✅ Deadline Saved!\n📌 {context.user_data['name']}\n📅 Due: {context.user_data['deadline']} at {time_str}")
            return ConversationHandler.END
        else:
            await update.message.reply_text("Which days?\nType: 'daily', 'weekdays', 'weekends', or specific days like 'mon, wed, fri'.")
            return ASK_DAYS
    except ValueError:
        await update.message.reply_text("Invalid format. Please use HH:MM (like 08:00).")
        return ASK_TIME

async def add_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days_str = parse_days(update.message.text)
    if not days_str:
        await update.message.reply_text("I didn't understand. Please type 'daily', 'weekdays', or 'mon, wed, fri'.")
        return ASK_DAYS

    user_id = update.effective_user.id
    cursor.execute("INSERT INTO tasks (user_id, task_name, task_type, task_time, task_days, last_completed) VALUES (?, ?, ?, ?, ?, ?)",
                   (user_id, context.user_data['name'], 'recurring', context.user_data['time'], days_str, "2000-01-01"))
    conn.commit()
    await update.message.reply_text(f"✅ Recurring Task Saved!\n📌 {context.user_data['name']}\n⏰ {context.user_data['time']}\n📅 {format_days(days_str)}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# --- LIST TASKS ---
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.datetime.now(TASHKENT_TZ)
    today_str = str(now.date())
    today_weekday = str(now.weekday())

    cursor.execute("SELECT id, task_name, task_type, task_time, task_days, deadline_date, last_completed FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()

    if not tasks:
        await update.message.reply_text("You have no tasks. Use /add to create one.")
        return

    deadlines_msg = "📌 **Deadlines:**\n"
    recurring_msg = "🔁 **Recurring Tasks:**\n"
    keyboard = []
    has_deadlines, has_recurring = False, False

    for t in tasks:
        task_id, name, t_type, time_str, days, deadline, last_completed = t
        
        if t_type == 'deadline':
            # Don't show if already completed
            if last_completed != "2000-01-01":
                continue
            has_deadlines = True
            status = "🔴 Overdue" if deadline < today_str else "⏳ Pending"
            deadlines_msg += f"{status} | {deadline} {time_str} - {name}\n"
            keyboard.append([InlineKeyboardButton(f"Done: '{name}'", callback_data=f"done_{task_id}")])
            
        elif t_type == 'recurring':
            if today_weekday in days.split(","):
                has_recurring = True
                status = "✅" if last_completed == today_str else "⏳"
                recurring_msg += f"{status} {time_str} - {name} ({format_days(days)})\n"
                if last_completed != today_str:
                    keyboard.append([InlineKeyboardButton(f"Done: '{name}'", callback_data=f"done_{task_id}")])

    final_msg = ""
    if has_deadlines: final_msg += deadlines_msg + "\n"
    if has_recurring: final_msg += recurring_msg
    
    if not final_msg:
        await update.message.reply_text("You have no pending tasks for today! 🎉")
        return

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(final_msg, reply_markup=reply_markup, parse_mode="Markdown")

# --- MARK AS DONE ---
async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = query.data.split("_")[1]
    today = str(datetime.datetime.now(TASHKENT_TZ).date())
    
    # We update last_completed to today. For deadlines, this hides them forever.
    # For recurring, it resets tomorrow.
    cursor.execute("UPDATE tasks SET last_completed = ?, last_reminded = NULL WHERE id = ?", (today, task_id))
    conn.commit()
    await query.edit_message_text("🎉 Excellent! Task marked as Done.")

# --- DELETE TASK ---
async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("SELECT id, task_name FROM tasks WHERE user_id = ?", (user_id,))
    tasks = cursor.fetchall()
    if not tasks: return await update.message.reply_text("No tasks to delete.")
    keyboard = [[InlineKeyboardButton(f"❌ {t[1]}", callback_data=f"del_{t[0]}")] for t in tasks]
    await update.message.reply_text("Select a task to delete:", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cursor.execute("DELETE FROM tasks WHERE id = ?", (query.data.split("_")[1],))
    conn.commit()
    await query.edit_message_text("🗑️ Task deleted.")

# ---------- SCHEDULER (NAGGING REMINDERS) ----------
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(TASHKENT_TZ)
    today_date = str(now.date())
    today_weekday = str(now.weekday())
    current_time = now.strftime("%H:%M")

    # Get ALL uncompleted tasks (for recurring) OR pending deadlines
    cursor.execute('''
        SELECT id, user_id, task_name, task_type, task_time, task_days, deadline_date, last_reminded 
        FROM tasks 
        WHERE last_completed = '2000-01-01' OR last_completed != ?
    ''', (today_date,))
    
    tasks = cursor.fetchall()
    
    for task in tasks:
        task_id, user_id, name, t_type, time_str, days, deadline, last_reminded_str = task
        
        should_remind = False
        remind_text = ""

        # Logic for RECURRING tasks
        if t_type == 'recurring':
            if today_weekday in days.split(",") and current_time >= time_str:
                should_remind = True
                remind_text = f"🔔 **Reminder:** Have you done: **{name}**? (Scheduled for {time_str})"

        # Logic for DEADLINE tasks
        elif t_type == 'deadline':
            if deadline == today_date and current_time >= time_str:
                should_remind = True
                remind_text = f"🚨 **DEADLINE TODAY:** You need to finish **{name}**!"
            elif deadline < today_date:
                # If it's overdue, remind them once a day at 09:00
                if current_time >= "09:00":
                    should_remind = True
                    remind_text = f"🔴 **OVERDUE:** Don't forget to finish **{name}** (Was due: {deadline})!"

        # NAGGING LOGIC (Check if 60 mins have passed since last reminder)
        if should_remind:
            send_it = False
            if not last_reminded_str:
                send_it = True
            else:
                last_reminded = datetime.datetime.strptime(last_reminded_str, "%Y-%m-%d %H:%M:%S")
                if (now - last_reminded).total_seconds() >= 3600:
                    send_it = True
            
            # Prevent multiple reminders on the same day for overdue tasks
            if send_it and last_reminded_str:
                if t_type == 'deadline' and deadline < today_date and last_reminded.date() == now.date():
                    send_it = False

            if send_it:
                try:
                    keyboard = [[InlineKeyboardButton("✅ Mark as Done", callback_data=f"done_{task_id}")]]
                    await context.bot.send_message(chat_id=user_id, text=remind_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                    cursor.execute("UPDATE tasks SET last_reminded = ? WHERE id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), task_id))
                    conn.commit()
                except Exception as e:
                    print(f"Failed to send: {e}")

# ---------- MAIN ----------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ASK_TYPE: [CallbackQueryHandler(add_type, pattern="^type_")],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_time)],
            ASK_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_days)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start := lambda u, c: u.message.reply_text("Commands:\n/add - Add task\n/list - See tasks\n/delete - Remove task")))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("delete", delete_task))
    app.add_handler(CallbackQueryHandler(mark_done, pattern="^done_"))
    app.add_handler(CallbackQueryHandler(confirm_delete, pattern="^del_"))

    app.job_queue.run_repeating(check_reminders, interval=60, first=10)

    print("✅ Bot is running with Unified Task Manager.")
    app.run_polling()

if __name__ == "__main__":
    main()
