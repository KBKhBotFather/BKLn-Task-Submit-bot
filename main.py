import os
import threading
import time
from datetime import datetime
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
app = Flask('')

@app.route('/')
def home(): return "BKLn Task Submit Bot is Alive & Running!"

def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def get_db_connection():
    uri = DB_URI
    if uri.startswith("postgres://"): uri = uri.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(uri)

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
                msg_1 TEXT, msg_2 TEXT, msg_3 TEXT,
                status TEXT DEFAULT 'Active'
            );
        """)
        conn.commit()
        conn.close()
    except Exception as e: print(f"DB Init Error: {e}")

init_db()

# --- States ---
user_states = {}
admin_states = {}

def get_member_info(tg_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT fb_name, team_name FROM members WHERE telegram_id = %s AND team_name IN ('Team Electron', 'Team Proton', 'Team Neutron') AND is_blocked = FALSE AND is_removed = FALSE AND status = 'Approved'", (tg_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    except Exception: return None

# 📱 Keyboards
def member_main_menu():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(KeyboardButton("Task"), KeyboardButton("Pending Content"))
    return markup

def admin_main_menu():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(KeyboardButton("Pending Content"), KeyboardButton("Task Assign"))
    markup.add(KeyboardButton("Task Status"), KeyboardButton("Edit Instructions"))
    return markup

def get_instruction_keyboard(selected=None):
    markup = InlineKeyboardMarkup(row_width=6)
    labels = ['1', '2', '3', '4', '5', '❌']
    btns = [InlineKeyboardButton(f"{l}✅" if l == selected else l, callback_data=f"inst_sel_{l}") for l in labels]
    markup.add(*btns)
    markup.row(InlineKeyboardButton("Submit", callback_data="inst_submit"), InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
    return markup

# 📌 Start Command
@bot.message_handler(commands=['start'])
def send_welcome(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "Welcome Admin to Control Panel!", reply_markup=admin_main_menu())
        return
        
    user = get_member_info(tg_id)
    if not user:
        bot.send_message(message.chat.id, "❌ You are not registered in the Meme Team or your account is not approved.")
        return
    bot.send_message(message.chat.id, "Welcome to KBKh Bot Ecosystem!\nYou can submit tasks directly here...", reply_markup=member_main_menu())

# 📌 User: Submit Photo
@bot.message_handler(content_types=['photo'])
def handle_photo_submission(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID: return 
    if not get_member_info(tg_id): return
    
    if tg_id in user_states and user_states[tg_id].get('status') == 'submitting':
        bot.send_message(message.chat.id, "Submit or Cancel your previous meme first!❌\nPlease submit one Meme at a time...")
        return

    user_states[tg_id] = {'status': 'submitting', 'photo_id': message.photo[-1].file_id}
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("General Post", callback_data="type_general"), InlineKeyboardButton("Special Post", callback_data="type_special"))
    markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
    bot.send_photo(message.chat.id, message.photo[-1].file_id, reply_markup=markup)

# 📌 User: Inline Selection Logic
@bot.callback_query_handler(func=lambda call: call.data.startswith("type_") or call.data.startswith("sub_") or call.data.startswith("sp_"))
def handle_submission_selection(call):
    tg_id = call.from_user.id
    if tg_id not in user_states: return
    data = call.data
    state = user_states[tg_id]

    if data == "sub_cancel":
        user_states.pop(tg_id, None)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "Process Cancelled✅")
        return
        
    elif data == "type_general":
        state['selected_type'] = 'General Post'
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("General Post✅", callback_data="ignore"), InlineKeyboardButton("Special Post", callback_data="type_special"))
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "type_special":
        state['selected_type'] = 'Special Post'
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(InlineKeyboardButton("Eng to Ban", callback_data="sp_eng"), InlineKeyboardButton("OC", callback_data="sp_oc"), InlineKeyboardButton("Inspired OC", callback_data="sp_insp"))
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data.startswith("sp_"):
        cat_map = {"sp_eng": "Eng to Ban", "sp_oc": "OC", "sp_insp": "Inspired OC"}
        state['special_cat'] = cat_map[data]
        markup = InlineKeyboardMarkup(row_width=3)
        btns = [InlineKeyboardButton(f"{v}✅" if k == data else v, callback_data=k) for k, v in cat_map.items()]
        markup.add(*btns)
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "sub_final":
        sel_type = state.get('selected_type')
        if not sel_type:
            bot.answer_callback_query(call.id, "Please select an option first!", show_alert=True)
            return
        if sel_type == 'Special Post' and 'special_cat' not in state:
            bot.answer_callback_query(call.id, "Please select a category first!", show_alert=True)
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO submissions (telegram_id, content_type, special_category, photo_id) VALUES (%s, %s, %s, %s)",
                       (tg_id, sel_type, state.get('special_cat'), state.get('photo_id')))
        conn.commit()
        conn.close()
        
        bot.delete_message(call.message.chat.id, call.message.message_id)
        msg = "Your General Post has been successfully submitted.✅" if sel_type == 'General Post' else "Your meme has been successfully submitted. Please wait for further instructions!"
        bot.send_message(call.message.chat.id, msg)
        user_states.pop(tg_id, None)

# 📌 Shared: Pending Content Button
@bot.message_handler(func=lambda msg: msg.text == "Pending Content")
def handle_pending_content(message):
    tg_id = message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    if str(tg_id) == ADMIN_CHAT_ID: # Admin View
        cursor.execute("SELECT s.telegram_id, m.fb_name, COUNT(s.id) as total FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.status = 'Pending' GROUP BY s.telegram_id, m.fb_name")
        records = cursor.fetchall()
        conn.close()
        
        if not records:
            bot.send_message(message.chat.id, "No Pending Content found!")
            return
            
        markup = InlineKeyboardMarkup(row_width=1)
        for r in records:
            markup.add(InlineKeyboardButton(f"Fb Name: {r['fb_name']} | Total Content: {r['total']}", callback_data=f"adm_rev_{r['telegram_id']}"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
        bot.send_message(message.chat.id, "Pending List:", reply_markup=markup)
        
    else: # User View
        cursor.execute("SELECT photo_id, assigned_instruction FROM submissions WHERE telegram_id = %s", (tg_id,))
        subs = cursor.fetchall()
        conn.close()
        
        if not subs:
            bot.send_message(message.chat.id, "No Pending content available!")
            return
            
        for sub in subs:
            inst = sub['assigned_instruction'] if sub['assigned_instruction'] else "Pending⏳"
            bot.send_photo(message.chat.id, sub['photo_id'], caption=f"Instruction: {inst}")

# 📌 Admin: Review Logistics
@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_") or call.data.startswith("inst_"))
def admin_callbacks(call):
    adm_id = call.from_user.id
    if str(adm_id) != ADMIN_CHAT_ID: return
    data = call.data

    if data == "adm_cancel":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "Process Cancelled✅")
        admin_states.pop(adm_id, None)
        return

    elif data.startswith("adm_rev_"):
        target_tg_id = int(data.split("_")[2])
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.telegram_id = %s AND s.status = 'Pending' LIMIT 1", (target_tg_id,))
        sub = cursor.fetchone()
        conn.close()
        
        if not sub:
            bot.answer_callback_query(call.id, "No pending content for this user.")
            return
            
        admin_states[adm_id] = {'sub_id': sub['id'], 'tg_id': target_tg_id, 'c_type': sub['content_type']}
        bot.send_photo(call.message.chat.id, sub['photo_id'], caption=f"Fb Name: {sub['fb_name']}", reply_markup=get_instruction_keyboard())

    elif data.startswith("inst_sel_"):
        sel = data.split("_")[2]
        admin_states[adm_id]['selected'] = sel
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_instruction_keyboard(sel))
        
    elif data == "inst_submit":
        state = admin_states.get(adm_id, {})
        sel = state.get('selected')
        if not sel:
            bot.answer_callback_query(call.id, "Select an instruction first!", show_alert=True)
            return

        inst_texts = {
            '1': "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং মিম পেইজ দিয়েই কিছুটা সময় পর সকল গ্রুপে পোস্ট করুন।",
            '2': "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং কিছুটা সময় পর মিম পেইজ দিয়েই শুধুমাত্র মিমগ্রুপে পোস্ট করুন।",
            '3': "নিজ মডারেটর আইডি থেকে সকল গ্রুপে পোস্ট করুন।",
            '4': "নিজ মডারেটর আইডি থেকে শুধুমাত্র মিম গ্রুপে পোস্ট করুন।",
            '5': "নিজ মডারেটর আইডি থেকে Annonymous ভাবে বা সেকেন্ড যেকোনো আইডি থেকে সরাসরি শুধু মিম গ্রুপে পোস্ট করুন।",
            '❌': "মিমটি পোস্টযোগ্য নয়। প্রয়োজনে যেকোনো সিনিয়র সদস্যের সাথে যোগাযোগ করুন।"
        }
        
        msg_text = inst_texts[sel]
        tg_id, sub_id, c_type = state['tg_id'], state['sub_id'], state['c_type']
        month_name = datetime.now().strftime("%B")

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Update status
        cursor.execute("UPDATE submissions SET status = 'Reviewed', assigned_instruction = %s WHERE id = %s", (msg_text, sub_id))
        
        # 2. Add count to Task Records (Bot Control Room Integration)
        if sel != '❌':
            cursor.execute("""
                INSERT INTO task_records (telegram_id, month, general_post, special_post, task_done)
                VALUES (%s, %s, 0, 0, 0) ON CONFLICT (telegram_id, month) DO NOTHING;
            """, (tg_id, month_name))
            
            if c_type == 'General Post':
                cursor.execute("UPDATE task_records SET general_post = general_post + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
            elif c_type == 'Special Post':
                cursor.execute("UPDATE task_records SET special_post = special_post + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
                
        conn.commit()
        conn.close()

        bot.delete_message(call.message.chat.id, call.message.message_id)
        try: bot.send_message(tg_id, f"Instruction: {msg_text}")
        except: pass
        bot.send_message(call.message.chat.id, "Instruction Sent & Count Updated!✅")

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("🤖 BKLn Task Submit Bot is Active...")
    bot.infinity_polling(skip_pending=True)
