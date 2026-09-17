import os
import threading
import time
from datetime import datetime, timedelta
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask

# ⚙️ Environment Variables
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
DB_URI = (os.environ.get("DATABASE_URL") or "").strip()
ADMIN_CHAT_ID = (os.environ.get("ADMIN_ID") or "").strip()
BRAFT_GROUP_ID = -1003913949837

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def home(): return "BKLn Task Submit Bot is Alive!", 200
@app.route('/ping')
def ping(): return "OK", 200
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# 🕒 BD Time Helper
def get_bd_time():
    return datetime.utcnow() + timedelta(hours=6)

def get_db_connection():
    uri = DB_URI
    if uri.startswith("postgres://"): uri = uri.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(uri)

def init_db():
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY, telegram_id BIGINT, content_type TEXT, special_category TEXT, 
                photo_id TEXT, media_type TEXT DEFAULT 'photo', status TEXT DEFAULT 'Pending', assigned_instruction TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                caption TEXT, assigned_grader BIGINT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY, task_num INT, msg_1 TEXT, msg_2 TEXT, msg_3 TEXT
            );
            CREATE TABLE IF NOT EXISTS organized_tasks (
                id SERIAL PRIMARY KEY, sequence TEXT
            );
            CREATE TABLE IF NOT EXISTS active_assignments (
                id SERIAL PRIMARY KEY, team_name TEXT, task_num INT, assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                current_msg INT DEFAULT 0, scheduled_for TIMESTAMP, is_started BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS user_task_status (
                telegram_id BIGINT, task_num INT, completed BOOLEAN DEFAULT FALSE, UNIQUE(telegram_id, task_num)
            );
            CREATE TABLE IF NOT EXISTS custom_instructions (
                id INT PRIMARY KEY, instruction_text TEXT
            );
            CREATE TABLE IF NOT EXISTS task_records (
                id SERIAL PRIMARY KEY, telegram_id BIGINT, month TEXT, 
                general_post INT DEFAULT 0, special_post INT DEFAULT 0, 
                task_done INT DEFAULT 0, task_total INT DEFAULT 0, special_mark INT DEFAULT 0, UNIQUE(telegram_id, month)
            );
            CREATE TABLE IF NOT EXISTS grading_moderators (
                telegram_id BIGINT PRIMARY KEY, resign_count INT DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, total_assigned INT DEFAULT 0
            );
        """)
        
        default_inst = {
            1: "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং মিম পেইজ দিয়েই কিছুটা সময় পর সকল গ্রুপে পোস্ট করুন।",
            2: "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং কিছুটা সময় পর মিম পেইজ দিয়েই শুধুমাত্র মিমগ্রুপে পোস্ট করুন।",
            3: "নিজ মডারেটর আইডি থেকে সকল গ্রুপে পোস্ট করুন।",
            4: "নিজ মডারেটর আইডি থেকে শুধুমাত্র মিম গ্রুপে পোস্ট করুন।",
            5: "নিজ মডারেটর আইডি থেকে Annonymous ভাবে বা সেকেন্ড যেকোনো আইডি থেকে সরাসরি শুধু মিম গ্রুপে পোস্ট করুন।"
        }
        for k, v in default_inst.items():
            try: cursor.execute("INSERT INTO custom_instructions (id, instruction_text) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))
            except: pass
            
        upgrade_queries = [
            "ALTER TABLE tasks ADD COLUMN task_num INT;",
            "ALTER TABLE tasks ADD COLUMN msg_1 TEXT;",
            "ALTER TABLE tasks ADD COLUMN msg_2 TEXT;",
            "ALTER TABLE tasks ADD COLUMN msg_3 TEXT;",
            "ALTER TABLE tasks ADD CONSTRAINT unique_task_num UNIQUE(task_num);",
            "ALTER TABLE submissions ADD COLUMN media_type TEXT DEFAULT 'photo';",
            "ALTER TABLE submissions ADD COLUMN caption TEXT;",
            "ALTER TABLE submissions ADD COLUMN assigned_grader BIGINT;",
            "ALTER TABLE active_assignments ADD COLUMN scheduled_for TIMESTAMP;",
            "ALTER TABLE active_assignments ADD COLUMN is_started BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE task_records ADD COLUMN special_mark INT DEFAULT 0;",
            "ALTER TABLE grading_moderators ADD COLUMN total_assigned INT DEFAULT 0;"
        ]
        for query in upgrade_queries:
            try: cursor.execute(query)
            except: pass
            
        try: cursor.execute("DELETE FROM tasks WHERE task_num IS NULL;")
        except: pass

        conn.close()
    except Exception as e: print(f"DB Init Error: {e}")

init_db()
user_photo_states = {} 
admin_states = {}
moderator_states = {}

def get_member_info(tg_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT fb_name, team_name FROM members WHERE telegram_id = %s AND team_name IN ('Team Electron', 'Team Proton', 'Team Neutron') AND is_blocked = FALSE AND is_removed = FALSE AND status = 'Approved'", (tg_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    except Exception: return None

# 🔴 Updated Distribution Logic: Strict Round-Robin based on total_assigned
def get_least_loaded_moderator():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT telegram_id 
        FROM grading_moderators 
        WHERE is_active = TRUE 
        ORDER BY total_assigned ASC, telegram_id ASC LIMIT 1
    """)
    mod = cursor.fetchone()
    conn.close()
    return mod['telegram_id'] if mod else None

# 📱 Keyboards
def member_main_menu():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(KeyboardButton("Task"), KeyboardButton("Pending Content"))
    markup.add(KeyboardButton("My Task Status"))
    return markup

def admin_main_menu():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(KeyboardButton("Pending Content"), KeyboardButton("Task Assign"))
    markup.add(KeyboardButton("Reset Task Data"), KeyboardButton("Edit Instructions"))
    markup.add(KeyboardButton("Triggered Task"))
    return markup

def moderator_main_menu():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(KeyboardButton("Pending Content"), KeyboardButton("Resignation ⚠️"))
    return markup

# 📌 Moderator Key Login (🟢 FIX APPLIED HERE)
@bot.message_handler(func=lambda msg: msg.text and msg.text.strip().upper().startswith("BKLNKEY22"))
def handle_moderator_login(message):
    tg_id = message.from_user.id
    code = message.text.strip()
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT resign_count FROM grading_moderators WHERE telegram_id = %s", (tg_id,))
    mod = cursor.fetchone()
    
    expected_suffix = ""
    if mod and mod['resign_count'] > 0:
        expected_suffix = str(mod['resign_count'])
        
    # Ignore case sensitivity during check
    if code.upper() == f"BKLNKEY22{expected_suffix}":
        cursor.execute("INSERT INTO grading_moderators (telegram_id, is_active) VALUES (%s, TRUE) ON CONFLICT (telegram_id) DO UPDATE SET is_active = TRUE", (tg_id,))
        conn.commit()
        bot.send_message(message.chat.id, "Welcome sir!\nYou are now in a sector of the Admin panel.", reply_markup=moderator_main_menu())
    else:
        bot.send_message(message.chat.id, "Invalid Key! Please provide the correct key.")
    conn.close()

# 📌 Core Commands
@bot.message_handler(commands=['start'])
def send_welcome(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "Welcome Admin to Control Panel!", reply_markup=admin_main_menu())
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM grading_moderators WHERE telegram_id = %s", (tg_id,))
    mod = cursor.fetchone()
    conn.close()
    if mod and mod[0]:
        bot.send_message(message.chat.id, "Welcome back to Moderator Panel!", reply_markup=moderator_main_menu())
        return

    user = get_member_info(tg_id)
    if not user:
        bot.send_message(message.chat.id, "You are not registered yet❌\nPlease register first.\n(Or provide your security key if you are a moderator)", reply_markup=ReplyKeyboardRemove())
        return
    bot.send_message(message.chat.id, "Welcome to KBKh Bot Ecosystem!\nYou can submit tasks directly here...", reply_markup=member_main_menu())

# 📌 MODERATOR: Resignation
@bot.message_handler(func=lambda msg: msg.text == "Resignation ⚠️")
def handle_resignation(message):
    tg_id = message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM grading_moderators WHERE telegram_id = %s", (tg_id,))
    mod = cursor.fetchone()
    conn.close()
    
    if not mod or not mod[0]: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Yes", callback_data="res_yes"), InlineKeyboardButton("No", callback_data="res_no"))
    bot.send_message(message.chat.id, "⚠️Are you sure you want to resign?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["res_yes", "res_no"])
def resignation_callback(call):
    tg_id = call.from_user.id
    try: bot.answer_callback_query(call.id)
    except: pass
    
    if call.data == "res_no":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return
        
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("UPDATE grading_moderators SET is_active = FALSE, resign_count = resign_count + 1 WHERE telegram_id = %s", (tg_id,))
    
    # Re-distribute pending items perfectly using Round-Robin
    cursor.execute("SELECT id FROM submissions WHERE assigned_grader = %s AND status = 'Grading'", (tg_id,))
    pending_items = cursor.fetchall()
    
    for item in pending_items:
        new_mod = get_least_loaded_moderator()
        if new_mod:
            cursor.execute("UPDATE submissions SET assigned_grader = %s WHERE id = %s", (new_mod, item['id']))
            cursor.execute("UPDATE grading_moderators SET total_assigned = total_assigned + 1 WHERE telegram_id = %s", (new_mod,))
            
    conn.commit()
    conn.close()
    bot.delete_message(call.message.chat.id, call.message.message_id)
    
    # Send user back to basic menu if they are a registered member, else remove keyboard
    user = get_member_info(tg_id)
    if user:
        bot.send_message(call.message.chat.id, "You have successfully resigned!✅", reply_markup=member_main_menu())
    else:
        bot.send_message(call.message.chat.id, "You have successfully resigned!✅", reply_markup=ReplyKeyboardRemove())

# 📌 MODERATOR: Pending Content (Grading)
@bot.message_handler(func=lambda msg: msg.text == "Pending Content")
def handle_moderator_pending(message):
    tg_id = message.from_user.id
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM grading_moderators WHERE telegram_id = %s", (tg_id,))
    mod = cursor.fetchone()
    conn.close()
    
    if str(tg_id) == ADMIN_CHAT_ID:
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Special Post", callback_data="pend_sp"), InlineKeyboardButton("Special Task", callback_data="pend_task"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
        bot.send_message(message.chat.id, "Select Content Type to Review:", reply_markup=markup)
        
    elif mod and mod[0]:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT s.telegram_id, m.fb_name, COUNT(s.id) as total FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.assigned_grader = %s AND s.status = 'Grading' GROUP BY s.telegram_id, m.fb_name", (tg_id,))
        records = cursor.fetchall()
        conn.close()
        
        if not records:
            return bot.send_message(message.chat.id, "No Pending Content found!")
        markup = InlineKeyboardMarkup(row_width=1)
        for r in records: markup.add(InlineKeyboardButton(f"{r['fb_name']} | Total Count: {r['total']}", callback_data=f"modrev_{r['telegram_id']}"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="mcanc"))
        bot.send_message(message.chat.id, "Pending List:", reply_markup=markup)
        
    else:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT photo_id, media_type FROM submissions WHERE telegram_id = %s AND status = 'Pending'", (tg_id,))
        subs = cursor.fetchall()
        conn.close()
        if not subs: bot.send_message(message.chat.id, "No Pending content available!")
        for sub in subs:
            if sub['media_type'] == 'video': bot.send_video(message.chat.id, sub['photo_id'], caption="Instruction: Pending⏳")
            else: bot.send_photo(message.chat.id, sub['photo_id'], caption="Instruction: Pending⏳")

@bot.callback_query_handler(func=lambda call: call.data.startswith("modrev_") or call.data.startswith("msel_") or call.data.startswith("msub_") or call.data == "mcanc")
def moderator_grading_callbacks(call):
    tg_id = call.from_user.id
    data = call.data
    try: bot.answer_callback_query(call.id)
    except: pass
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM grading_moderators WHERE telegram_id = %s", (tg_id,))
    mod = cursor.fetchone()
    conn.close()
    
    if not mod or not mod[0]:
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        bot.answer_callback_query(call.id, "You are no longer an active moderator!", show_alert=True)
        return

    if tg_id not in moderator_states: moderator_states[tg_id] = {}

    try:
        if data == "mcanc":
            bot.delete_message(call.message.chat.id, call.message.message_id)
            return

        elif data.startswith("modrev_"):
            target_tg_id = int(data.split("_")[1])
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.assigned_grader = %s AND s.telegram_id = %s AND s.status = 'Grading'", (tg_id, target_tg_id))
            subs = cursor.fetchall()
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            if not subs: return bot.send_message(call.message.chat.id, "No pending content.")
            
            for sub in subs:
                dt = sub.get('created_at')
                sub_date = (dt + timedelta(hours=6)).strftime("%d %B") if dt else get_bd_time().strftime("%d %B")
                
                caption_text = f"Name: {sub['fb_name']}\nDate: {sub_date}"
                markup = get_moderator_grading_keyboard(sub['id'])
                
                if sub.get('media_type') == 'video':
                    bot.send_video(call.message.chat.id, sub['photo_id'], caption=caption_text, reply_markup=markup)
                else:
                    bot.send_photo(call.message.chat.id, sub['photo_id'], caption=caption_text, reply_markup=markup)

        elif data.startswith("msel_"):
            parts = data.split("_")
            sub_id, mark = int(parts[1]), parts[2]
            if 'grade' not in moderator_states[tg_id]: moderator_states[tg_id]['grade'] = {}
            moderator_states[tg_id]['grade'][sub_id] = mark
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_moderator_grading_keyboard(sub_id, mark))

        elif data.startswith("msub_"):
            sub_id = int(data.split("_")[1])
            mark = moderator_states[tg_id].get('grade', {}).get(sub_id)
            if not mark: return bot.send_message(call.message.chat.id, "Select a mark first!")
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT telegram_id, created_at FROM submissions WHERE id = %s", (sub_id,))
            sub_info = cursor.fetchone()
            
            if sub_info:
                submitter_tg_id = sub_info['telegram_id']
                submit_month = (sub_info['created_at'] + timedelta(hours=6)).strftime("%B")
                
                cursor.execute("UPDATE submissions SET status = 'Graded' WHERE id = %s", (sub_id,))
                cursor.execute("UPDATE task_records SET special_mark = special_mark + %s WHERE telegram_id = %s AND month = %s", (int(mark), submitter_tg_id, submit_month))
                conn.commit()
                
            conn.close()
            bot.delete_message(call.message.chat.id, call.message.message_id)
            
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            bot.send_message(ADMIN_CHAT_ID, f"⚠️ Mod System Error: {str(e)}")

def get_moderator_grading_keyboard(sub_id, selected=None):
    markup = InlineKeyboardMarkup(row_width=6)
    labels = ['1', '2', '3', '4', '5', '6']
    btns = [InlineKeyboardButton(f"{l}✅" if str(l) == str(selected) else l, callback_data=f"msel_{sub_id}_{l}") for l in labels]
    markup.add(*btns)
    markup.row(InlineKeyboardButton("Submit", callback_data=f"msub_{sub_id}"), InlineKeyboardButton("Cancel", callback_data="mcanc"))
    return markup


# 📌 USER: Submissions (Photos & Videos)
@bot.message_handler(content_types=['photo', 'video'])
def handle_media_submission(message):
    tg_id = message.from_user.id
    msg_id = message.message_id
    if str(tg_id) == ADMIN_CHAT_ID: return 
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM grading_moderators WHERE telegram_id = %s", (tg_id,))
    mod = cursor.fetchone()
    conn.close()
    if mod and mod[0]: return 
    
    user = get_member_info(tg_id)
    if not user: return
    
    media_type = message.content_type
    file_id = message.photo[-1].file_id if media_type == 'photo' else message.video.file_id
    caption = message.caption if message.caption else ""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT task_num FROM active_assignments WHERE team_name = %s AND is_started = TRUE", (user['team_name'],))
    active_task = cursor.fetchone()
    
    task_completed = False
    if active_task:
        cursor.execute("SELECT completed FROM user_task_status WHERE telegram_id = %s AND task_num = %s", (tg_id, active_task['task_num']))
        uts = cursor.fetchone()
        if uts and uts['completed']:
            task_completed = True
    conn.close()

    if tg_id not in user_photo_states: user_photo_states[tg_id] = {}
    user_photo_states[tg_id][msg_id] = {'file_id': file_id, 'media_type': media_type, 'caption': caption}
    
    markup = InlineKeyboardMarkup(row_width=2)
    if active_task and not task_completed:
        user_photo_states[tg_id][msg_id]['active_task_num'] = active_task['task_num']
        markup.add(InlineKeyboardButton("General Post", callback_data=f"tgen_{msg_id}"), InlineKeyboardButton("Special Task", callback_data=f"tspt_{msg_id}"))
    else:
        markup.add(InlineKeyboardButton("General Post", callback_data=f"tgen_{msg_id}"), InlineKeyboardButton("Special Post", callback_data=f"tspf_{msg_id}"))
    
    markup.row(InlineKeyboardButton("Submit", callback_data=f"subf_{msg_id}"), InlineKeyboardButton("Cancel", callback_data=f"ucanc_{msg_id}"))
    
    if media_type == 'photo': bot.send_photo(message.chat.id, file_id, reply_markup=markup)
    else: bot.send_video(message.chat.id, file_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("tgen_") or call.data.startswith("tspt_") or call.data.startswith("tspf_") or call.data.startswith("spc_") or call.data.startswith("subf_") or call.data.startswith("ucanc_"))
def handle_user_submission_clicks(call):
    tg_id = call.from_user.id
    parts = call.data.split("_")
    action, msg_id = parts[0], int(parts[1])
    
    try: bot.answer_callback_query(call.id)
    except: pass

    if tg_id not in user_photo_states or msg_id not in user_photo_states[tg_id]: return
    state = user_photo_states[tg_id][msg_id]

    if action == "ucanc":
        user_photo_states[tg_id].pop(msg_id, None)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        
    elif action == "tgen":
        state['selected_type'] = 'General Post'
        markup = InlineKeyboardMarkup(row_width=2)
        if 'active_task_num' in state: markup.add(InlineKeyboardButton("General Post✅", callback_data="ignore"), InlineKeyboardButton("Special Task", callback_data=f"tspt_{msg_id}"))
        else: markup.add(InlineKeyboardButton("General Post✅", callback_data="ignore"), InlineKeyboardButton("Special Post", callback_data=f"tspf_{msg_id}"))
        markup.row(InlineKeyboardButton("Submit", callback_data=f"subf_{msg_id}"), InlineKeyboardButton("Cancel", callback_data=f"ucanc_{msg_id}"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "tspt":
        state['selected_type'] = 'Task'
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("General Post", callback_data=f"tgen_{msg_id}"), InlineKeyboardButton("Special Task✅", callback_data="ignore"))
        markup.row(InlineKeyboardButton("Submit", callback_data=f"subf_{msg_id}"), InlineKeyboardButton("Cancel", callback_data=f"ucanc_{msg_id}"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "tspf":
        state['selected_type'] = 'Special Post'
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(InlineKeyboardButton("Eng to Ban", callback_data=f"spc_{msg_id}_eng"), InlineKeyboardButton("OC", callback_data=f"spc_{msg_id}_oc"), InlineKeyboardButton("Inspired OC", callback_data=f"spc_{msg_id}_insp"))
        markup.row(InlineKeyboardButton("Submit", callback_data=f"subf_{msg_id}"), InlineKeyboardButton("Cancel", callback_data=f"ucanc_{msg_id}"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif action == "spc":
        cat_map = {"eng": "Eng to Ban", "oc": "OC", "insp": "Inspired OC"}
        state['special_cat'] = cat_map[parts[2]]
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(*[InlineKeyboardButton(f"{v}✅" if k == parts[2] else v, callback_data=f"spc_{msg_id}_{k}") for k, v in cat_map.items()])
        markup.row(InlineKeyboardButton("Submit", callback_data=f"subf_{msg_id}"), InlineKeyboardButton("Cancel", callback_data=f"ucanc_{msg_id}"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "subf":
        sel_type = state.get('selected_type')
        if not sel_type or (sel_type == 'Special Post' and 'special_cat' not in state):
            return bot.send_message(call.message.chat.id, "Select an option first!")
            
        conn = get_db_connection()
        cursor = conn.cursor()
        month_name = get_bd_time().strftime("%B")
        m_type = state.get('media_type', 'photo')
        caption_text = state.get('caption', '')
        
        if sel_type == 'General Post':
            cursor.execute("INSERT INTO submissions (telegram_id, content_type, photo_id, media_type, status, caption) VALUES (%s, %s, %s, %s, 'Direct', %s)", (tg_id, sel_type, state.get('file_id'), m_type, caption_text))
            cursor.execute("INSERT INTO task_records (telegram_id, month, general_post, special_post, task_done, task_total) VALUES (%s, %s, 1, 0, 0, 0) ON CONFLICT (telegram_id, month) DO UPDATE SET general_post = task_records.general_post + 1", (tg_id, month_name))
            msg = "Your General Post has been successfully submitted and counted!✅"
        elif sel_type == 'Task':
            t_num = state.get('active_task_num')
            cursor.execute("INSERT INTO submissions (telegram_id, content_type, photo_id, media_type, caption) VALUES (%s, %s, %s, %s, %s)", (tg_id, f"Task-{t_num}", state.get('file_id'), m_type, caption_text))
            cursor.execute("INSERT INTO user_task_status (telegram_id, task_num, completed) VALUES (%s, %s, TRUE) ON CONFLICT (telegram_id, task_num) DO UPDATE SET completed = TRUE", (tg_id, t_num))
            msg = "Your task has been successfully submitted✅\nPlease wait for further instructions."
        else:
            cursor.execute("INSERT INTO submissions (telegram_id, content_type, special_category, photo_id, media_type, caption) VALUES (%s, %s, %s, %s, %s, %s)", (tg_id, sel_type, state.get('special_cat'), state.get('file_id'), m_type, caption_text))
            msg = "Your meme has been successfully submitted. Please wait for further instructions!"
            
        conn.commit()
        conn.close()
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, msg)
        user_photo_states[tg_id].pop(msg_id, None)

@bot.message_handler(func=lambda msg: msg.text == "Task")
def handle_task_display(message):
    tg_id = message.from_user.id
    user = get_member_info(tg_id)
    if not user: return
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT task_num, current_msg FROM active_assignments WHERE team_name = %s AND is_started = TRUE", (user['team_name'],))
    assign = cursor.fetchone()
    
    if not assign: 
        cursor.execute("SELECT COUNT(*) FROM active_assignments WHERE is_started = TRUE")
        global_active = cursor.fetchone()['count']
        
        cursor.execute("SELECT completed FROM user_task_status WHERE telegram_id = %s ORDER BY task_num DESC LIMIT 1", (tg_id,))
        recent_uts = cursor.fetchone()
        
        if global_active > 0 and recent_uts and recent_uts['completed']:
            bot.send_message(message.chat.id, "You have successfully completed the Task!✅")
        else:
            bot.send_message(message.chat.id, "No Task Available at this moment!")
    else:
        cursor.execute("SELECT completed FROM user_task_status WHERE telegram_id = %s AND task_num = %s", (tg_id, assign['task_num']))
        uts = cursor.fetchone()
        
        if uts and uts['completed']:
            cursor.execute("SELECT status FROM submissions WHERE telegram_id = %s AND content_type = %s ORDER BY id DESC LIMIT 1", (tg_id, f"Task-{assign['task_num']}"))
            sub_status = cursor.fetchone()
            
            if sub_status and sub_status['status'] == 'Pending':
                bot.send_message(message.chat.id, "Your task has been successfully submitted.\nTask Status: Pending!⏳")
            else:
                bot.send_message(message.chat.id, "You have successfully completed the Task!✅")
        else:
            cursor.execute("SELECT msg_1, msg_2, msg_3 FROM tasks WHERE task_num = %s", (assign['task_num'],))
            t_data = cursor.fetchone()
            msgs = []
            if t_data['msg_1'] and assign['current_msg'] >= 1: msgs.append(t_data['msg_1'])
            if t_data['msg_2'] and assign['current_msg'] >= 2: msgs.append(t_data['msg_2'])
            if t_data['msg_3'] and assign['current_msg'] >= 3: msgs.append(t_data['msg_3'])
            bot.send_message(message.chat.id, "\n\n".join(msgs))
    conn.close()

@bot.message_handler(func=lambda msg: msg.text == "My Task Status")
def handle_my_task_status(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID: return
    user = get_member_info(tg_id)
    if not user: return
    
    month_name = get_bd_time().strftime("%B")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM task_records WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
    rec = cursor.fetchone()
    conn.close()
    
    gen = rec['general_post'] if rec else 0
    spc = rec['special_post'] if rec else 0
    tdone = rec['task_done'] if rec else 0
    ttot = rec['task_total'] if rec else 0
    s_mark = rec['special_mark'] if rec else 0
    
    text = (f"🗂️Your Task status for {month_name}\n"
            f"General Post - {gen}\n"
            f"Special Post - {spc}\n"
            f"Special Task - {tdone}/{ttot}\n"
            f"Special Mark - {s_mark}")
    bot.send_message(message.chat.id, text)

# 📌 ADMIN: Menus & Logics
@bot.message_handler(func=lambda msg: msg.text == "Task Assign")
def task_assign_menu(message):
    if str(message.from_user.id) != ADMIN_CHAT_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Add Task", callback_data="ta_add"), InlineKeyboardButton("Task List", callback_data="ta_list"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
    bot.send_message(message.chat.id, "Task Assign Menu:", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "Edit Instructions")
def edit_instructions_menu(message):
    if str(message.from_user.id) != ADMIN_CHAT_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Task Instructions", callback_data="ei_task"), InlineKeyboardButton("Main Instructions", callback_data="ei_main"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
    bot.send_message(message.chat.id, "Edit Menu:", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "Triggered Task")
def triggered_task_menu(message):
    if str(message.from_user.id) != ADMIN_CHAT_ID: return
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT team_name, task_num, is_started FROM active_assignments ORDER BY id ASC")
    assigns = cursor.fetchall()
    conn.close()
    if not assigns: return bot.send_message(message.chat.id, "No Task Triggered!")
    
    is_st = any(a['is_started'] for a in assigns)
    text = ""
    for a in assigns: text += f"{a['team_name']} - Task {a['task_num']}\n"
    text += f"\nTask Status: {'Started!🟢' if is_st else 'Not Assigned yet!🔴'}"
    markup = InlineKeyboardMarkup(row_width=2)
    if not is_st: 
        markup.add(InlineKeyboardButton("Stop Task", callback_data="ta_stop"))
    else:
        markup.add(InlineKeyboardButton("End", callback_data="ta_force_end"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "Reset Task Data")
def handle_reset_task_data(message):
    if str(message.from_user.id) != ADMIN_CHAT_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Yes", callback_data="reset_task_yes"), InlineKeyboardButton("No", callback_data="acanc"))
    bot.send_message(message.chat.id, "Are you sure you want to reset all Task Data?", reply_markup=markup)

# 📌 ADMIN: Callbacks
@bot.callback_query_handler(func=lambda call: call.data.startswith("acanc") or call.data.startswith("arev_") or call.data.startswith("isel_") or call.data.startswith("isub_") or call.data.startswith("ta_") or call.data.startswith("ei_") or call.data.startswith("ti_") or call.data.startswith("mi_") or call.data == "reset_task_yes" or call.data.startswith("pend_"))
def admin_callbacks(call):
    adm_id = call.from_user.id
    if str(adm_id) != ADMIN_CHAT_ID: return
    data = call.data

    if adm_id not in admin_states: admin_states[adm_id] = {}
    try: bot.answer_callback_query(call.id)
    except: pass

    try:
        if data == "acanc":
            if 'clean_msgs' in admin_states.get(adm_id, {}):
                for m_id in admin_states[adm_id]['clean_msgs']:
                    try: bot.delete_message(call.message.chat.id, m_id)
                    except: pass
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.clear_step_handler_by_chat_id(call.message.chat.id)
            admin_states[adm_id] = {}
            return

        elif data == "reset_task_yes":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_task_status")
            cursor.execute("DELETE FROM active_assignments")
            cursor.execute("DELETE FROM submissions")
            cursor.execute("DELETE FROM task_records")
            conn.commit()
            conn.close()
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "All Data (Task, General Post, Special Post) has been permanently reset!✅\nUser profiles are completely fresh (00).")

        elif data in ["pend_sp", "pend_task"]:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            if data == "pend_sp":
                c_label = "sp"
                cursor.execute("SELECT s.telegram_id, m.fb_name, COUNT(s.id) as total FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.status = 'Pending' AND s.content_type = 'Special Post' GROUP BY s.telegram_id, m.fb_name")
            else:
                c_label = "tk"
                cursor.execute("SELECT s.telegram_id, m.fb_name, COUNT(s.id) as total FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.status = 'Pending' AND s.content_type LIKE 'Task-%%' GROUP BY s.telegram_id, m.fb_name")
                
            records = cursor.fetchall()
            conn.close()
            
            if not records:
                return bot.edit_message_text("No Pending Content found for this category!", call.message.chat.id, call.message.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Back", callback_data="pend_back")))
                
            markup = InlineKeyboardMarkup(row_width=1)
            for r in records: 
                markup.add(InlineKeyboardButton(f"{r['fb_name']} | Total Count: {r['total']}", callback_data=f"arev_{r['telegram_id']}_{c_label}"))
            markup.add(InlineKeyboardButton("Back", callback_data="pend_back"), InlineKeyboardButton("Cancel", callback_data="acanc"))
            bot.edit_message_text("Pending List:", call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data == "pend_back":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(InlineKeyboardButton("Special Post", callback_data="pend_sp"), InlineKeyboardButton("Special Task", callback_data="pend_task"))
            markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
            bot.edit_message_text("Select Content Type to Review:", call.message.chat.id, call.message.message_id, reply_markup=markup)

        elif data.startswith("arev_"):
            parts = data.split("_")
            target_tg_id = int(parts[1])
            c_label = parts[2] if len(parts) > 2 else "all"
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            if c_label == "sp":
                cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.telegram_id = %s AND s.status = 'Pending' AND s.content_type = 'Special Post'", (target_tg_id,))
            elif c_label == "tk":
                cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.telegram_id = %s AND s.status = 'Pending' AND s.content_type LIKE 'Task-%%'", (target_tg_id,))
            else:
                cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.telegram_id = %s AND s.status = 'Pending'", (target_tg_id,))
                
            subs = cursor.fetchall()
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            if not subs: return bot.send_message(call.message.chat.id, "No pending content.")
            for sub in subs:
                dt = sub.get('created_at')
                sub_date = (dt + timedelta(hours=6)).strftime("%d %B") if dt else get_bd_time().strftime("%d %B")
                
                caption_text = f"Name: {sub['fb_name']}\nDate: {sub_date}"
                if sub['content_type'].startswith('Task-'):
                    caption_text += "\n⭐Special Task"
                    
                markup = get_admin_instruction_keyboard(sub['id'])
                if sub.get('media_type') == 'video':
                    bot.send_video(call.message.chat.id, sub['photo_id'], caption=caption_text, reply_markup=markup)
                else:
                    bot.send_photo(call.message.chat.id, sub['photo_id'], caption=caption_text, reply_markup=markup)

        elif data.startswith("isel_"):
            parts = data.split("_")
            sub_id, sel = int(parts[1]), parts[2]
            if 'rev' not in admin_states[adm_id]: admin_states[adm_id]['rev'] = {}
            admin_states[adm_id]['rev'][sub_id] = sel
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_admin_instruction_keyboard(sub_id, sel))

        elif data.startswith("isub_"):
            sub_id = int(data.split("_")[1])
            sel = admin_states[adm_id].get('rev', {}).get(sub_id)
            if not sel: return bot.send_message(call.message.chat.id, "Select instruction first!")
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.id = %s", (sub_id,))
            sub_info = cursor.fetchone()
            if not sub_info: return
            
            tg_id, c_type, photo_id, m_type, fb_name = sub_info['telegram_id'], sub_info['content_type'], sub_info['photo_id'], sub_info.get('media_type', 'photo'), sub_info['fb_name']
            dt = sub_info.get('created_at')
            sub_date = (dt + timedelta(hours=6)).strftime("%d %B") if dt else get_bd_time().strftime("%d %B")
            
            month_name = get_bd_time().strftime("%B")

            if sel == '❌': 
                msg_text = "মিমটি পোস্টযোগ্য নয়। প্রয়োজনে যেকোনো সিনিয়র সদস্যের সাথে যোগাযোগ করুন।"
                if c_type.startswith('Task-'):
                    try:
                        t_num = int(c_type.split('-')[1])
                        cursor.execute("UPDATE user_task_status SET completed = FALSE WHERE telegram_id = %s AND task_num = %s", (tg_id, t_num))
                    except: pass
                status_val = 'Rejected'
                assigned_grader = None
            else:
                cursor.execute("SELECT instruction_text FROM custom_instructions WHERE id = %s", (int(sel),))
                msg_text = cursor.fetchone()['instruction_text']
                
                cursor.execute("INSERT INTO task_records (telegram_id, month, general_post, special_post, task_done, task_total) VALUES (%s, %s, 0, 0, 0, 0) ON CONFLICT (telegram_id, month) DO NOTHING;", (tg_id, month_name))
                if c_type == 'Special Post': cursor.execute("UPDATE task_records SET special_post = special_post + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
                elif c_type.startswith('Task'): cursor.execute("UPDATE task_records SET task_done = task_done + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
                
                if str(sel) == '1' and (c_type == 'Special Post' or c_type.startswith('Task')):
                    try:
                        braft_cap = sub_info.get('caption')
                        braft_cap = braft_cap if (braft_cap and braft_cap.strip()) else None
                        
                        if m_type == 'video': bot.send_video(BRAFT_GROUP_ID, photo_id, caption=braft_cap)
                        else: bot.send_photo(BRAFT_GROUP_ID, photo_id, caption=braft_cap)
                    except Exception as e: print(f"Group send error: {e}")
                
                assigned_grader = get_least_loaded_moderator()
                if assigned_grader:
                    cursor.execute("UPDATE grading_moderators SET total_assigned = total_assigned + 1 WHERE telegram_id = %s", (assigned_grader,))
                status_val = 'Grading' if assigned_grader else 'Reviewed'
                
            cursor.execute("UPDATE submissions SET status = %s, assigned_instruction = %s, assigned_grader = %s WHERE id = %s", (status_val, msg_text, assigned_grader, sub_id))
            conn.commit()
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            try: 
                if m_type == 'video': bot.send_video(tg_id, photo_id, caption=f"Instruction:\n{msg_text}")
                else: bot.send_photo(tg_id, photo_id, caption=f"Instruction:\n{msg_text}")
            except: pass
            
        elif data == "ta_add":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(task_num) FROM tasks")
            row = cursor.fetchone()
            max_t = row[0] if (row and row[0] is not None) else 0
            conn.close()
            
            next_t = max_t + 1
            admin_states[adm_id]['t_num'] = next_t
            admin_states[adm_id]['clean_msgs'] = []
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc"))
            msg = bot.send_message(call.message.chat.id, f"Give the first message of the Task -{next_t}:", reply_markup=markup)
            admin_states[adm_id]['clean_msgs'].append(msg.message_id)
            bot.register_next_step_handler(msg, step_add_msg1)
            
        elif data.startswith("ta_end_"):
            count = int(data.split("_")[2])
            bot.clear_step_handler_by_chat_id(call.message.chat.id)
            if 'clean_msgs' in admin_states[adm_id]:
                admin_states[adm_id]['clean_msgs'].append(call.message.message_id)
            finalize_task(adm_id, call.message.chat.id, count)

        elif data == "ta_list":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks")
            count = cursor.fetchone()[0]
            conn.close()
            if count == 0:
                bot.send_message(call.message.chat.id, "Please add at least 1 Task.")
                return
            
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(InlineKeyboardButton("Organize Task", callback_data="ta_org"), InlineKeyboardButton("Assign Task", callback_data="ta_assign"))
            markup.row(InlineKeyboardButton("Back", callback_data="ta_back_main"), InlineKeyboardButton("Cancel", callback_data="acanc"))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data == "ta_org":
            bot.delete_message(call.message.chat.id, call.message.message_id)
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc"))
            msg = bot.send_message(call.message.chat.id, "Please provide the list.\n(Example: 1,2,3)", reply_markup=markup)
            bot.register_next_step_handler(msg, step_org_task)
            
        elif data == "ta_assign":
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT COUNT(*) FROM active_assignments")
            if cursor.fetchone()['count'] > 0:
                conn.close()
                return bot.answer_callback_query(call.id, "Already assigned a Task! Please stop it first in Triggered Task.", show_alert=True)
                
            cursor.execute("SELECT * FROM organized_tasks")
            orgs = cursor.fetchall()
            conn.close()
            
            if not orgs: return bot.send_message(call.message.chat.id, "No Tasks organized❌")
            admin_states[adm_id]['assigning'] = {'orgs': orgs}
            markup = InlineKeyboardMarkup(row_width=1)
            for org in orgs: markup.add(InlineKeyboardButton(f"Task {org['sequence'].replace(',', '  ')}   🔳", callback_data=f"ta_asgsel_{org['id']}"))
            markup.add(InlineKeyboardButton("Back", callback_data="ta_list"), InlineKeyboardButton("Cancel", callback_data="acanc"))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data.startswith("ta_asgsel_"):
            org_id = int(data.split("_")[2])
            admin_states[adm_id]['assigning']['selected'] = org_id
            markup = InlineKeyboardMarkup(row_width=1)
            for org in admin_states[adm_id]['assigning']['orgs']:
                icon = "✅" if org['id'] == org_id else "🔳"
                markup.add(InlineKeyboardButton(f"Task {org['sequence'].replace(',', '  ')}   {icon}", callback_data=f"ta_asgsel_{org['id']}"))
            markup.add(InlineKeyboardButton("Submit", callback_data="ta_asgsub"), InlineKeyboardButton("Cancel", callback_data="acanc"))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data == "ta_asgsub":
            state = admin_states[adm_id].get('assigning', {})
            org_id = state.get('selected')
            if not org_id: return bot.send_message(call.message.chat.id, "Select one first!")
            
            seq_str = next((o['sequence'] for o in state['orgs'] if o['id'] == org_id), None)
            tasks = seq_str.split(',')
            teams = ['Team Electron', 'Team Proton', 'Team Neutron']
            month_name = get_bd_time().strftime("%B")
            
            now = get_bd_time()
            tomorrow = now.date() + timedelta(days=1)
            next_midnight = datetime.combine(tomorrow, datetime.min.time())
            
            conn = get_db_connection()
            cursor = conn.cursor()
            for i, t_num in enumerate(tasks):
                if i < len(teams):
                    cursor.execute("DELETE FROM active_assignments WHERE team_name = %s", (teams[i],))
                    cursor.execute("DELETE FROM user_task_status WHERE task_num = %s AND telegram_id IN (SELECT telegram_id FROM members WHERE team_name = %s)", (t_num, teams[i]))
                    cursor.execute("DELETE FROM submissions WHERE content_type = %s AND telegram_id IN (SELECT telegram_id FROM members WHERE team_name = %s)", (f"Task-{t_num}", teams[i]))
                    
                    cursor.execute("INSERT INTO active_assignments (team_name, task_num, scheduled_for, is_started, current_msg) VALUES (%s, %s, %s, FALSE, 0)", (teams[i], t_num, next_midnight))
                    cursor.execute("INSERT INTO task_records (telegram_id, month, task_total) SELECT telegram_id, %s, 1 FROM members WHERE team_name = %s ON CONFLICT (telegram_id, month) DO UPDATE SET task_total = task_records.task_total + 1", (month_name, teams[i]))
            conn.commit()
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, f"Task: {seq_str.replace(',',' ')} Assigned Successfully✅\nWill trigger at Midnight.")
            
        elif data == "ta_back_main":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(InlineKeyboardButton("Add Task", callback_data="ta_add"), InlineKeyboardButton("Task List", callback_data="ta_list"))
            markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data == "ta_stop":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_assignments WHERE is_started = FALSE")
            conn.commit()
            conn.close()
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "Task Assign Cancelled Successfully✅")
            
        elif data == "ta_force_end":
            bot.delete_message(call.message.chat.id, call.message.message_id)
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(InlineKeyboardButton("Yes", callback_data="ta_force_end_yes"), InlineKeyboardButton("No", callback_data="acanc"))
            bot.send_message(call.message.chat.id, "Are you sure you want to end the Task?", reply_markup=markup)
            
        elif data == "ta_force_end_yes":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_assignments WHERE is_started = TRUE")
            conn.commit()
            conn.close()
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "Task Successfully end!✅")

        elif data == "ei_task":
            markup = get_task_edit_keyboard(adm_id)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data.startswith("ti_exp_"):
            t_num = int(data.split("_")[2])
            current_exp = admin_states[adm_id].get('edit_task_exp')
            admin_states[adm_id]['edit_task_exp'] = t_num if current_exp != t_num else None
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_task_edit_keyboard(adm_id))
            
        elif data.startswith("ti_edit_"):
            parts = data.split("_")
            t_num, msg_idx = int(parts[2]), int(parts[3])
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(f"SELECT msg_{msg_idx} FROM tasks WHERE task_num = %s", (t_num,))
            msg_text = cursor.fetchone()[f'msg_{msg_idx}']
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(InlineKeyboardButton("Yes", callback_data=f"ti_yes_{t_num}_{msg_idx}"), InlineKeyboardButton("No", callback_data="acanc"))
            bot.send_message(call.message.chat.id, f"{msg_text}\n\nWould you like to change anything here?", reply_markup=markup)
            
        elif data.startswith("ti_yes_"):
            parts = data.split("_")
            t_num, msg_idx = int(parts[2]), int(parts[3])
            bot.delete_message(call.message.chat.id, call.message.message_id)
            msg = bot.send_message(call.message.chat.id, f"Input the new message for Task -{t_num} ;Message -{msg_idx}:", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc")))
            admin_states[adm_id]['wait_ti'] = {'t_num': t_num, 'msg_idx': msg_idx}
            bot.register_next_step_handler(msg, step_update_task_msg)

        elif data.startswith("ti_del_sel_"):
            t_num = int(data.split("_")[3])
            admin_states[adm_id]['del_task'] = t_num
            markup = get_task_edit_keyboard(adm_id, t_num)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data == "ti_del_sub":
            t_num = admin_states[adm_id].get('del_task')
            if not t_num: return bot.send_message(call.message.chat.id, "Select one to delete!")
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("DELETE FROM tasks WHERE task_num = %s", (t_num,))
            
            cursor.execute("SELECT id, sequence FROM organized_tasks")
            orgs = cursor.fetchall()
            for org in orgs:
                if str(t_num) in org['sequence'].split(','):
                    cursor.execute("DELETE FROM organized_tasks WHERE id = %s", (org['id'],))
            
            conn.commit()
            conn.close()
            
            admin_states[adm_id]['del_task'] = None
            markup = get_task_edit_keyboard(adm_id)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id, "Task Deleted Successfully!", show_alert=True)

        elif data == "ei_main":
            markup, text = get_main_inst_keyboard(adm_id)
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data.startswith("mi_sel_"):
            sel_id = int(data.split("_")[2])
            admin_states[adm_id]['edit_main'] = sel_id
            markup, text = get_main_inst_keyboard(adm_id, sel_id)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data.startswith("mi_view_"):
            i_id = int(data.split("_")[2])
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT instruction_text FROM custom_instructions WHERE id = %s", (i_id,))
            inst = cursor.fetchone()
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(InlineKeyboardButton("Yes", callback_data=f"mi_yes_{i_id}"), InlineKeyboardButton("No", callback_data="acanc"))
            bot.send_message(call.message.chat.id, f"{inst['instruction_text']}\n\nWould you like to change anything here?", reply_markup=markup)
            
        elif data.startswith("mi_yes_"):
            sel_id = int(data.split("_")[2])
            bot.delete_message(call.message.chat.id, call.message.message_id)
            msg = bot.send_message(call.message.chat.id, f"Input the new message for Instruction {sel_id}:", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc")))
            admin_states[adm_id]['wait_mi'] = sel_id
            bot.register_next_step_handler(msg, step_update_main_msg)
            
        elif data == "mi_add":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(id) FROM custom_instructions")
            max_id = cursor.fetchone()[0] or 0
            conn.close()
            
            bot.delete_message(call.message.chat.id, call.message.message_id)
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc"))
            msg = bot.send_message(call.message.chat.id, f"Please provide the instruction number {max_id + 1}:", reply_markup=markup)
            admin_states[adm_id]['wait_mi'] = max_id + 1
            bot.register_next_step_handler(msg, step_finalize_add_main)

        elif data == "mi_click":
            sel_id = admin_states[adm_id].get('edit_main')
            if not sel_id: return bot.send_message(call.message.chat.id, "Select one first!")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM custom_instructions WHERE id = %s", (sel_id,))
            conn.commit()
            conn.close()
            admin_states[adm_id]['edit_main'] = None
            markup, text = get_main_inst_keyboard(adm_id)
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
            
        elif data == "ei_back":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(InlineKeyboardButton("Task Instructions", callback_data="ei_task"), InlineKeyboardButton("Main Instructions", callback_data="ei_main"))
            markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            bot.send_message(ADMIN_CHAT_ID, f"⚠️ System Debug Error: {str(e)}")

def get_admin_instruction_keyboard(sub_id, selected=None):
    markup = InlineKeyboardMarkup(row_width=6)
    labels = ['1', '2', '3', '4', '5', '❌']
    btns = [InlineKeyboardButton(f"{l}✅" if str(l) == str(selected) else l, callback_data=f"isel_{sub_id}_{l}") for l in labels]
    markup.add(*btns)
    markup.row(InlineKeyboardButton("Submit", callback_data=f"isub_{sub_id}"), InlineKeyboardButton("Cancel", callback_data="acanc"))
    return markup

def get_task_edit_keyboard(adm_id, sel_del_id=None):
    markup = InlineKeyboardMarkup()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT task_num, msg_1, msg_2, msg_3 FROM tasks ORDER BY task_num")
    tasks = cursor.fetchall()
    conn.close()

    exp = admin_states.get(adm_id, {}).get('edit_task_exp')

    if not tasks:
        markup.row(InlineKeyboardButton("Back", callback_data="ei_back"), InlineKeyboardButton("Cancel", callback_data="acanc"))
        return markup

    for t in tasks:
        t_num = t['task_num']
        del_icon = "❌" if str(sel_del_id) == str(t_num) else "🔳"
        
        row = []
        if exp == t_num:
            row.append(InlineKeyboardButton(f"Task - {t_num} 🔻", callback_data=f"ti_exp_{t_num}"))
        else:
            row.append(InlineKeyboardButton(f"Task - {t_num} 🔺", callback_data=f"ti_exp_{t_num}"))
            
        row.append(InlineKeyboardButton(del_icon, callback_data=f"ti_del_sel_{t_num}"))
        markup.row(*row)

        if exp == t_num:
            btns = []
            if t['msg_1']: btns.append(InlineKeyboardButton("1", callback_data=f"ti_edit_{t_num}_1"))
            if t['msg_2']: btns.append(InlineKeyboardButton("2", callback_data=f"ti_edit_{t_num}_2"))
            if t['msg_3']: btns.append(InlineKeyboardButton("3", callback_data=f"ti_edit_{t_num}_3"))
            if btns: markup.row(*btns)
            
    if sel_del_id:
        markup.row(InlineKeyboardButton("Submit", callback_data="ti_del_sub"))
        
    markup.row(InlineKeyboardButton("Back", callback_data="ei_back"), InlineKeyboardButton("Cancel", callback_data="acanc"))
    return markup

def get_main_inst_keyboard(adm_id, sel_id=None):
    markup = InlineKeyboardMarkup()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id FROM custom_instructions ORDER BY id")
    insts = cursor.fetchall()
    conn.close()

    if not insts:
        markup.row(InlineKeyboardButton("Add", callback_data="mi_add"), InlineKeyboardButton("Submit", callback_data="mi_click"))
        markup.row(InlineKeyboardButton("Back", callback_data="ei_back"), InlineKeyboardButton("Cancel", callback_data="acanc"))
        return markup, "No Instructions Available!"
    
    for inst in insts:
        i_id = inst['id']
        icon = "❌" if str(i_id) == str(sel_id) else "🔳"
        markup.row(InlineKeyboardButton(f"Instruction - {i_id}", callback_data=f"mi_view_{i_id}"), InlineKeyboardButton(icon, callback_data=f"mi_sel_{i_id}"))
    
    markup.row(InlineKeyboardButton("Add", callback_data="mi_add"), InlineKeyboardButton("Submit", callback_data="mi_click"))
    markup.row(InlineKeyboardButton("Back", callback_data="ei_back"), InlineKeyboardButton("Cancel", callback_data="acanc"))
    return markup, "Main Instructions:"

def step_update_task_msg(message):
    adm_id = message.from_user.id
    state = admin_states.get(adm_id, {}).get('wait_ti')
    if not state: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE tasks SET msg_{state['msg_idx']} = %s WHERE task_num = %s", (message.text, state['t_num']))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Successfully Replaced!✅")

def step_update_main_msg(message):
    adm_id = message.from_user.id
    sel_id = admin_states.get(adm_id, {}).get('wait_mi')
    if not sel_id: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE custom_instructions SET instruction_text = %s WHERE id = %s", (message.text, sel_id))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Successfully Replaced!✅")

def step_finalize_add_main(message):
    adm_id = message.from_user.id
    num = admin_states.get(adm_id, {}).get('wait_mi')
    if not num: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO custom_instructions (id, instruction_text) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET instruction_text = EXCLUDED.instruction_text", (num, message.text))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Your instruction has been successfully added✅")

def step_add_msg1(message):
    adm_id = message.from_user.id
    if adm_id not in admin_states or 't_num' not in admin_states[adm_id]: return
    admin_states[adm_id]['msg_1'] = message.text
    admin_states[adm_id]['clean_msgs'].append(message.message_id)
    
    next_t = admin_states[adm_id]['t_num']
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"), InlineKeyboardButton("End", callback_data="ta_end_1"))
    msg = bot.send_message(message.chat.id, f"Give the second message of the Task -{next_t}:", reply_markup=markup)
    admin_states[adm_id]['clean_msgs'].append(msg.message_id)
    bot.register_next_step_handler(msg, step_add_msg2)

def step_add_msg2(message):
    adm_id = message.from_user.id
    if adm_id not in admin_states or 't_num' not in admin_states[adm_id]: return
    admin_states[adm_id]['msg_2'] = message.text
    admin_states[adm_id]['clean_msgs'].append(message.message_id)
    
    next_t = admin_states[adm_id]['t_num']
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"), InlineKeyboardButton("End", callback_data="ta_end_2"))
    msg = bot.send_message(message.chat.id, f"Give the Third message of the Task -{next_t}:", reply_markup=markup)
    admin_states[adm_id]['clean_msgs'].append(msg.message_id)
    bot.register_next_step_handler(msg, step_add_msg3)

def step_add_msg3(message):
    adm_id = message.from_user.id
    if adm_id not in admin_states: return
    admin_states[adm_id]['msg_3'] = message.text
    admin_states[adm_id]['clean_msgs'].append(message.message_id)
    finalize_task(adm_id, message.chat.id, 3)

def finalize_task(adm_id, chat_id, count):
    state = admin_states.get(adm_id, {})
    t_num = state.get('t_num')
    if not t_num: return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (task_num, msg_1, msg_2, msg_3) VALUES (%s, %s, %s, %s) ON CONFLICT (task_num) DO UPDATE SET msg_1=EXCLUDED.msg_1, msg_2=EXCLUDED.msg_2, msg_3=EXCLUDED.msg_3", 
                   (t_num, state.get('msg_1'), state.get('msg_2', None), state.get('msg_3', None)))
    conn.commit()
    conn.close()
    
    for m_id in state.get('clean_msgs', []):
        try: bot.delete_message(chat_id, m_id)
        except: pass
        
    admin_states[adm_id].pop('t_num', None)
    admin_states[adm_id].pop('clean_msgs', None)
    
    bot.send_message(chat_id, f"All your messages for Task-{t_num} have been successfully added!✅")

def step_org_task(message):
    seq = message.text.replace(" ", "")
    tasks = seq.split(',')
    conn = get_db_connection()
    cursor = conn.cursor()
    success, failed = [], []
    for t in tasks:
        try:
            cursor.execute("SELECT id FROM tasks WHERE task_num = %s", (int(t),))
            if cursor.fetchone(): success.append(t)
            else: failed.append(t)
        except: failed.append(t)
        
    if success and not failed:
        cursor.execute("INSERT INTO organized_tasks (sequence) VALUES (%s)", (seq,))
        bot.send_message(message.chat.id, "Task list added successfully✅")
    else:
        msg = ""
        if success: msg += f"Task {','.join(success)} added successfully✅\n"
        if failed: msg += f"Task {','.join(failed)} doesn't exist❌"
        bot.send_message(message.chat.id, msg)
    conn.commit()
    conn.close()

# ⏰ Background Auto-Timer
def auto_task_timer():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM active_assignments")
            assignments = cursor.fetchall()
            now = get_bd_time()
            
            for assign in assignments:
                team, t_num, msg_lvl, sched = assign['team_name'], assign['task_num'], assign['current_msg'], assign['scheduled_for']
                is_st = assign['is_started']
                
                if not is_st and now >= sched:
                    cursor.execute("UPDATE active_assignments SET is_started = TRUE, current_msg = 1 WHERE id = %s", (assign['id'],))
                    cursor.execute("SELECT msg_1 FROM tasks WHERE task_num = %s", (t_num,))
                    m1 = cursor.fetchone()['msg_1']
                    cursor.execute("SELECT telegram_id FROM members WHERE team_name = %s AND status = 'Approved'", (team,))
                    for u in cursor.fetchall():
                        try: bot.send_message(u['telegram_id'], m1)
                        except: pass
                    continue
                
                if not is_st: continue 
                
                cursor.execute("SELECT COUNT(*) FROM members WHERE team_name = %s AND status = 'Approved'", (team,))
                total_members = cursor.fetchone()['count']
                
                cursor.execute("""
                    SELECT COUNT(DISTINCT s.telegram_id) 
                    FROM submissions s 
                    JOIN members m ON s.telegram_id = m.telegram_id 
                    WHERE m.team_name = %s 
                      AND s.content_type = %s 
                      AND s.status IN ('Reviewed', 'Grading', 'Graded')
                """, (team, f"Task-{t_num}"))
                done_members = cursor.fetchone()['count']
                
                if total_members == 0 or done_members >= total_members:
                    cursor.execute("DELETE FROM active_assignments WHERE id = %s", (assign['id'],))
                    continue

                diff_hours = (now - sched).total_seconds() / 3600
                cursor.execute("SELECT msg_2, msg_3 FROM tasks WHERE task_num = %s", (t_num,))
                t_msgs = cursor.fetchone()
                
                cursor.execute("""
                    SELECT m.telegram_id FROM members m 
                    LEFT JOIN user_task_status uts ON m.telegram_id = uts.telegram_id AND uts.task_num = %s
                    WHERE m.team_name = %s AND m.status = 'Approved' AND (uts.completed IS NULL OR uts.completed = FALSE)
                """, (t_num, team))
                pending_users = cursor.fetchall()

                if diff_hours >= 24 and msg_lvl == 1 and t_msgs['msg_2']:
                    for u in pending_users:
                        try: bot.send_message(u['telegram_id'], t_msgs['msg_2'])
                        except: pass
                    cursor.execute("UPDATE active_assignments SET current_msg = 2 WHERE id = %s", (assign['id'],))
                    
                elif diff_hours >= 48 and msg_lvl == 2 and t_msgs['msg_3']:
                    for u in pending_users:
                        try: bot.send_message(u['telegram_id'], t_msgs['msg_3'])
                        except: pass
                    cursor.execute("UPDATE active_assignments SET current_msg = 3 WHERE id = %s", (assign['id'],))
                    
                elif diff_hours >= 72:
                    cursor.execute("DELETE FROM active_assignments WHERE id = %s", (assign['id'],))
            conn.commit()
            conn.close()
        except Exception as e: print("Timer Error:", e)
        time.sleep(60)

if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask)
    t_flask.daemon = True
    t_flask.start()
    
    t_timer = threading.Thread(target=auto_task_timer)
    t_timer.daemon = True
    t_timer.start()
    
    print("🤖 BKLn Task Submit Bot is fully operational with Round-Robin grading!")
    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 409: time.sleep(10)
            else: time.sleep(5)
        except Exception: time.sleep(5)
