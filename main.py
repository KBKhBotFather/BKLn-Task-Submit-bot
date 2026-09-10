import os
import threading
import time
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask

# ⚙️ Environment Variables
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
DB_URI = (os.environ.get("DATABASE_URL") or "").strip()
ADMIN_CHAT_ID = (os.environ.get("ADMIN_ID") or "").strip()

bot = telebot.TeleBot(BOT_TOKEN)

# 🌐 Flask Server
app = Flask('')

@app.route('/')
def home():
    return "BKLn Task Submit Bot is Alive & Running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# 🔌 Database Connection
def get_db_connection():
    uri = DB_URI
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(uri)

# 🛠️ DB Setup (ধাপ ১: নতুন টেবিল তৈরি)
def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                content_type TEXT, 
                special_category TEXT, 
                photo_id TEXT,
                status TEXT DEFAULT 'Pending',
                assigned_instruction TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                task_num INT UNIQUE,
                msg_1 TEXT,
                msg_2 TEXT,
                msg_3 TEXT,
                status TEXT DEFAULT 'Active'
            );
            CREATE TABLE IF NOT EXISTS active_task_assignment (
                id SERIAL PRIMARY KEY,
                task_num INT,
                team_name TEXT,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        conn.close()
        print("Database initialized successfully!")
    except Exception as e:
        print(f"DB Init Error: {e}")

init_db()

# --- User State (কড়া নিয়মের জন্য ট্র্যাক রাখা) ---
user_states = {}

def get_member_info(tg_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT fb_name, team_name FROM members WHERE telegram_id = %s AND team_name IN ('Team Electron', 'Team Proton', 'Team Neutron') AND is_blocked = FALSE AND is_removed = FALSE AND status = 'Approved'", (tg_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    except Exception:
        return None

# 📱 Reply Keyboards
def member_main_menu():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(KeyboardButton("Task"), KeyboardButton("Pending Content"))
    return markup

# 📌 ধাপ ২: মেম্বার ভিউ এবং ছবি সাবমিশন
@bot.message_handler(commands=['start'])
def send_welcome(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "Welcome Admin! (Admin Panel will be connected soon)")
        return
        
    user = get_member_info(tg_id)
    if not user:
        bot.send_message(message.chat.id, "❌ You are not registered in the Meme Team or your account is not approved.")
        return

    bot.send_message(
        message.chat.id, 
        "Welcome to KBKh Bot Ecosystem!\nYou can submit tasks directly here...", 
        reply_markup=member_main_menu()
    )

@bot.message_handler(content_types=['photo'])
def handle_photo_submission(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID: return 
    
    user = get_member_info(tg_id)
    if not user: return
    
    # স্ট্রিক্ট রুল: আগেরটা শেষ না করে নতুন ছবি দিলে কড়া ওয়ার্নিং
    if tg_id in user_states and user_states[tg_id].get('status') == 'submitting':
        bot.send_message(message.chat.id, "Submit or Cancel your previous meme first!❌\nPlease submit one Meme at a time...")
        return

    photo_id = message.photo[-1].file_id
    user_states[tg_id] = {'status': 'submitting', 'photo_id': photo_id}
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("General Post", callback_data="type_general"),
        InlineKeyboardButton("Special Post", callback_data="type_special")
    )
    markup.row(
        InlineKeyboardButton("Submit", callback_data="sub_final"),
        InlineKeyboardButton("Cancel", callback_data="sub_cancel")
    )
    
    bot.send_photo(message.chat.id, photo_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("type_") or call.data.startswith("sub_") or call.data.startswith("sp_"))
def handle_submission_selection(call):
    tg_id = call.from_user.id
    if tg_id not in user_states:
        try: bot.answer_callback_query(call.id, "Session expired. Send photo again.")
        except: pass
        return
        
    data = call.data
    state = user_states[tg_id]
    photo_id = state.get('photo_id')

    if data == "sub_cancel":
        user_states.pop(tg_id, None)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "Process Cancelled✅")
        return
        
    elif data == "type_general":
        state['selected_type'] = 'General Post'
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("General Post✅", callback_data="ignore"),
            InlineKeyboardButton("Special Post", callback_data="type_special")
        )
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "type_special":
        state['selected_type'] = 'Special Post'
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(
            InlineKeyboardButton("Eng to Ban", callback_data="sp_eng"),
            InlineKeyboardButton("OC", callback_data="sp_oc"),
            InlineKeyboardButton("Inspired OC", callback_data="sp_insp")
        )
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data.startswith("sp_"):
        cat_map = {"sp_eng": "Eng to Ban", "sp_oc": "OC", "sp_insp": "Inspired OC"}
        state['special_cat'] = cat_map[data]
        markup = InlineKeyboardMarkup(row_width=3)
        btns = []
        for k, v in cat_map.items():
            text = f"{v}✅" if k == data else v
            btns.append(InlineKeyboardButton(text, callback_data=k))
        markup.add(*btns)
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "sub_final":
        sel_type = state.get('selected_type')
        if not sel_type:
            bot.answer_callback_query(call.id, "Please select an option first!", show_alert=True)
            return
        if sel_type == 'Special Post' and 'special_cat' not in state:
            bot.answer_callback_query(call.id, "Please select a category (Eng to Ban/OC/Inspired OC)!", show_alert=True)
            return
            
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO submissions (telegram_id, content_type, special_category, photo_id) VALUES (%s, %s, %s, %s)",
                (tg_id, sel_type, state.get('special_cat'), photo_id)
            )
            conn.commit()
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            if sel_type == 'General Post':
                bot.send_message(call.message.chat.id, "Your General Post has been successfully submitted.✅")
            else:
                bot.send_message(call.message.chat.id, "Your meme has been successfully submitted. Please wait for further instructions!")
                
            user_states.pop(tg_id, None)
        except Exception as e:
            bot.send_message(call.message.chat.id, f"Error: {e}")

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("🤖 BKLn Task Submit Bot is Active...")
    bot.infinity_polling(skip_pending=True)
