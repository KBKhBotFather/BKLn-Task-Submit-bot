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

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def home(): return "BKLn Task Submit Bot is Alive!", 200
@app.route('/ping')
def ping(): return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

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
                id SERIAL PRIMARY KEY, telegram_id BIGINT, content_type TEXT, special_category TEXT, 
                photo_id TEXT, status TEXT DEFAULT 'Pending', assigned_instruction TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY, task_num INT UNIQUE, msg_1 TEXT, msg_2 TEXT, msg_3 TEXT
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
        """)
        # Insert Default Instructions
        default_inst = {
            1: "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং মিম পেইজ দিয়েই কিছুটা সময় পর সকল গ্রুপে পোস্ট করুন।",
            2: "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং কিছুটা সময় পর মিম পেইজ দিয়েই শুধুমাত্র মিমগ্রুপে পোস্ট করুন।",
            3: "নিজ মডারেটর আইডি থেকে সকল গ্রুপে পোস্ট করুন।",
            4: "নিজ মডারেটর আইডি থেকে শুধুমাত্র মিম গ্রুপে পোস্ট করুন।",
            5: "নিজ মডারেটর আইডি থেকে Annonymous ভাবে বা সেকেন্ড যেকোনো আইডি থেকে সরাসরি শুধু মিম গ্রুপে পোস্ট করুন।"
        }
        for k, v in default_inst.items():
            cursor.execute("INSERT INTO custom_instructions (id, instruction_text) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))
            
        # Upgrade old tables safely
        try: cursor.execute("ALTER TABLE active_assignments ADD COLUMN scheduled_for TIMESTAMP;")
        except: pass
        try: cursor.execute("ALTER TABLE active_assignments ADD COLUMN is_started BOOLEAN DEFAULT FALSE;")
        except: pass
        
        conn.commit()
        conn.close()
    except Exception as e: print(f"DB Init Error: {e}")

init_db()

user_photo_states = {} 
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
    markup.add(KeyboardButton("My Task Status"))
    return markup

def admin_main_menu():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(KeyboardButton("Pending Content"), KeyboardButton("Task Assign"))
    markup.add(KeyboardButton("Task Status"), KeyboardButton("Edit Instructions"))
    markup.add(KeyboardButton("Triggered Task"))
    return markup

# 📌 Core Commands
@bot.message_handler(commands=['start'])
def send_welcome(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "Welcome Admin to Control Panel!", reply_markup=admin_main_menu())
        return
    user = get_member_info(tg_id)
    if not user:
        bot.send_message(message.chat.id, "You are not registered yet❌\nPlease register first.", reply_markup=ReplyKeyboardRemove())
        return
    bot.send_message(message.chat.id, "Welcome to KBKh Bot Ecosystem!\nYou can submit tasks directly here...", reply_markup=member_main_menu())

# 📌 USER: Multi-Photo Submission
@bot.message_handler(content_types=['photo'])
def handle_photo_submission(message):
    tg_id = message.from_user.id
    msg_id = message.message_id
    if str(tg_id) == ADMIN_CHAT_ID: return 
    user = get_member_info(tg_id)
    if not user: return
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT task_num FROM active_assignments WHERE team_name = %s AND is_started = TRUE", (user['team_name'],))
    active_task = cursor.fetchone()
    conn.close()

    if tg_id not in user_photo_states: user_photo_states[tg_id] = {}
    user_photo_states[tg_id][msg_id] = {'photo_id': message.photo[-1].file_id}
    
    markup = InlineKeyboardMarkup(row_width=2)
    if active_task:
        user_photo_states[tg_id][msg_id]['active_task_num'] = active_task['task_num']
        markup.add(InlineKeyboardButton("General Post", callback_data=f"tgen_{msg_id}"), InlineKeyboardButton("Special Task", callback_data=f"tspt_{msg_id}"))
    else:
        markup.add(InlineKeyboardButton("General Post", callback_data=f"tgen_{msg_id}"), InlineKeyboardButton("Special Post", callback_data=f"tspf_{msg_id}"))
    markup.row(InlineKeyboardButton("Submit", callback_data=f"subf_{msg_id}"), InlineKeyboardButton("Cancel", callback_data=f"ucanc_{msg_id}"))
    bot.send_photo(message.chat.id, message.photo[-1].file_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("tgen_") or call.data.startswith("tspt_") or call.data.startswith("tspf_") or call.data.startswith("spc_") or call.data.startswith("subf_") or call.data.startswith("ucanc_"))
def handle_user_submission_clicks(call):
    tg_id = call.from_user.id
    parts = call.data.split("_")
    action = parts[0]
    msg_id = int(parts[1])
    
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
            return bot.answer_callback_query(call.id, "Select an option first!", show_alert=True)
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if sel_type == 'Task':
            t_num = state.get('active_task_num')
            cursor.execute("INSERT INTO submissions (telegram_id, content_type, photo_id) VALUES (%s, %s, %s)", (tg_id, f"Task-{t_num}", state.get('photo_id')))
            cursor.execute("INSERT INTO user_task_status (telegram_id, task_num, completed) VALUES (%s, %s, TRUE) ON CONFLICT (telegram_id, task_num) DO UPDATE SET completed = TRUE", (tg_id, t_num))
            msg = "Your task has been successfully submitted✅\nPlease wait for further instructions."
        else:
            cursor.execute("INSERT INTO submissions (telegram_id, content_type, special_category, photo_id) VALUES (%s, %s, %s, %s)", (tg_id, sel_type, state.get('special_cat'), state.get('photo_id')))
            msg = "Your General Post has been successfully submitted.✅" if sel_type == 'General Post' else "Your meme has been successfully submitted. Please wait for further instructions!"
            
        conn.commit()
        conn.close()
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, msg)
        user_photo_states[tg_id].pop(msg_id, None)

# 📌 USER: Additional Menus
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
        bot.send_message(message.chat.id, "No Task Available at this moment!")
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
    
    month_name = datetime.now().strftime("%B")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM task_records WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
    rec = cursor.fetchone()
    conn.close()
    
    gen = rec['general_post'] if rec else 0
    spc = rec['special_post'] if rec else 0
    tdone = rec['task_done'] if rec else 0
    ttot = rec['task_total'] if rec else 0
    
    text = (f"🗂️Your Task status for {month_name}\n"
            f"General Post - {gen}\n"
            f"Special Post - {spc}\n"
            f"Special Task - {tdone}/{ttot}\n"
            f"Special Mark - 0")
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == "Pending Content")
def handle_pending_content(message):
    tg_id = message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    if str(tg_id) == ADMIN_CHAT_ID:
        cursor.execute("SELECT s.telegram_id, m.fb_name, COUNT(s.id) as total FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.status = 'Pending' GROUP BY s.telegram_id, m.fb_name")
        records = cursor.fetchall()
        if not records:
            bot.send_message(message.chat.id, "No Pending Content found!")
            return
        markup = InlineKeyboardMarkup(row_width=1)
        for r in records: markup.add(InlineKeyboardButton(f"{r['fb_name']} | Total Count: {r['total']}", callback_data=f"arev_{r['telegram_id']}"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
        bot.send_message(message.chat.id, "Pending List:", reply_markup=markup)
    else:
        cursor.execute("SELECT photo_id, assigned_instruction FROM submissions WHERE telegram_id = %s AND status = 'Pending'", (tg_id,))
        subs = cursor.fetchall()
        if not subs: bot.send_message(message.chat.id, "No Pending content available!")
        for sub in subs: bot.send_photo(message.chat.id, sub['photo_id'], caption="Instruction: Pending⏳")
    conn.close()


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
    cursor.execute("SELECT team_name, task_num, is_started FROM active_assignments ORDER BY team_name")
    assigns = cursor.fetchall()
    conn.close()
    
    if not assigns: return bot.send_message(message.chat.id, "No Task Triggered!")
    
    is_st = any(a['is_started'] for a in assigns)
    text = ""
    for a in assigns:
        team = a['team_name'].replace("Team ", "")
        text += f"Team {team} - Task {a['task_num']}\n"
        
    text += f"\nTask Status: {'Started!🟢' if is_st else 'Not Assigned yet!🔴'}"
    markup = InlineKeyboardMarkup(row_width=2)
    if not is_st: markup.add(InlineKeyboardButton("Stop Task", callback_data="ta_stop"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
    bot.send_message(message.chat.id, text, reply_markup=markup)


# 📌 ADMIN: Callbacks
@bot.callback_query_handler(func=lambda call: call.data.startswith("acanc") or call.data.startswith("arev_") or call.data.startswith("isel_") or call.data.startswith("isub_") or call.data.startswith("ta_") or call.data.startswith("ei_") or call.data.startswith("ti_") or call.data.startswith("mi_"))
def admin_callbacks(call):
    adm_id = call.from_user.id
    if str(adm_id) != ADMIN_CHAT_ID: return
    data = call.data

    if data == "acanc":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.clear_step_handler_by_chat_id(call.message.chat.id)
        if adm_id in admin_states: admin_states.pop(adm_id, None)
        return

    # 1. Admin Review Multi-Photo
    elif data.startswith("arev_"):
        target_tg_id = int(data.split("_")[1])
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.telegram_id = %s AND s.status = 'Pending'", (target_tg_id,))
        subs = cursor.fetchall()
        conn.close()
        
        bot.delete_message(call.message.chat.id, call.message.message_id)
        if not subs: return bot.answer_callback_query(call.id, "No pending content.")
        
        for sub in subs:
            markup = get_admin_instruction_keyboard(sub['id'])
            bot.send_photo(call.message.chat.id, sub['photo_id'], caption=f"Fb Name: {sub['fb_name']}", reply_markup=markup)

    elif data.startswith("isel_"):
        parts = data.split("_")
        sub_id = int(parts[1])
        sel = parts[2]
        if 'rev' not in admin_states: admin_states['rev'] = {}
        admin_states['rev'][sub_id] = sel
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_admin_instruction_keyboard(sub_id, sel))

    elif data.startswith("isub_"):
        sub_id = int(data.split("_")[1])
        sel = admin_states.get('rev', {}).get(sub_id)
        if not sel: return bot.answer_callback_query(call.id, "Select instruction!", show_alert=True)
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT telegram_id, content_type FROM submissions WHERE id = %s", (sub_id,))
        sub_info = cursor.fetchone()
        if not sub_info: return
        
        tg_id, c_type = sub_info['telegram_id'], sub_info['content_type']
        month_name = datetime.now().strftime("%B")

        if sel == '❌': msg_text = "মিমটি পোস্টযোগ্য নয়। প্রয়োজনে যেকোনো সিনিয়র সদস্যের সাথে যোগাযোগ করুন।"
        else:
            cursor.execute("SELECT instruction_text FROM custom_instructions WHERE id = %s", (int(sel),))
            msg_text = cursor.fetchone()['instruction_text']
            
            cursor.execute("INSERT INTO task_records (telegram_id, month, general_post, special_post, task_done, task_total) VALUES (%s, %s, 0, 0, 0, 0) ON CONFLICT (telegram_id, month) DO NOTHING;", (tg_id, month_name))
            if c_type == 'General Post': cursor.execute("UPDATE task_records SET general_post = general_post + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
            elif c_type == 'Special Post': cursor.execute("UPDATE task_records SET special_post = special_post + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
            elif c_type.startswith('Task'): cursor.execute("UPDATE task_records SET task_done = task_done + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
            
        cursor.execute("UPDATE submissions SET status = 'Reviewed', assigned_instruction = %s WHERE id = %s", (msg_text, sub_id))
        conn.commit()
        conn.close()
        
        bot.delete_message(call.message.chat.id, call.message.message_id)
        try: bot.send_message(tg_id, f"Instruction: {msg_text}")
        except: pass
        
    # 2. Task Assign System
    elif data == "ta_add":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        msg = bot.send_message(call.message.chat.id, "Task Number?")
        bot.register_next_step_handler(msg, step_add_task_num)
        
    elif data == "ta_list":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Organize Task", callback_data="ta_org"), InlineKeyboardButton("Assign Task", callback_data="ta_assign"))
        markup.row(InlineKeyboardButton("Back", callback_data="ta_back_main"), InlineKeyboardButton("Cancel", callback_data="acanc"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data == "ta_org":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        msg = bot.send_message(call.message.chat.id, "Please provide the list.\n(Example: 1,2,3)")
        bot.register_next_step_handler(msg, step_org_task)
        
    elif data == "ta_assign":
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM organized_tasks")
        orgs = cursor.fetchall()
        conn.close()
        
        if not orgs: return bot.answer_callback_query(call.id, "No Tasks organized❌", show_alert=True)
        admin_states['assigning'] = {'orgs': orgs}
        markup = InlineKeyboardMarkup(row_width=1)
        for org in orgs: markup.add(InlineKeyboardButton(f"Task {org['sequence'].replace(',', '  ')}   🔳", callback_data=f"ta_asgsel_{org['id']}"))
        markup.add(InlineKeyboardButton("Back", callback_data="ta_list"), InlineKeyboardButton("Cancel", callback_data="acanc"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data.startswith("ta_asgsel_"):
        org_id = int(data.split("_")[2])
        admin_states['assigning']['selected'] = org_id
        markup = InlineKeyboardMarkup(row_width=1)
        for org in admin_states['assigning']['orgs']:
            icon = "✅" if org['id'] == org_id else "🔳"
            markup.add(InlineKeyboardButton(f"Task {org['sequence'].replace(',', '  ')}   {icon}", callback_data=f"ta_asgsel_{org['id']}"))
        markup.add(InlineKeyboardButton("Submit", callback_data="ta_asgsub"), InlineKeyboardButton("Cancel", callback_data="acanc"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data == "ta_asgsub":
        state = admin_states.get('assigning', {})
        org_id = state.get('selected')
        if not org_id: return bot.answer_callback_query(call.id, "Select one first!")
        
        seq_str = next((o['sequence'] for o in state['orgs'] if o['id'] == org_id), None)
        tasks = seq_str.split(',')
        teams = ['Team Electron', 'Team Proton', 'Team Neutron']
        month_name = datetime.now().strftime("%B")
        
        # Calculate next midnight
        now = datetime.now()
        tomorrow = now.date() + timedelta(days=1)
        next_midnight = datetime.combine(tomorrow, datetime.min.time())
        
        conn = get_db_connection()
        cursor = conn.cursor()
        for i, t_num in enumerate(tasks):
            if i < len(teams):
                cursor.execute("DELETE FROM active_assignments WHERE team_name = %s", (teams[i],))
                cursor.execute("INSERT INTO active_assignments (team_name, task_num, scheduled_for, is_started, current_msg) VALUES (%s, %s, %s, FALSE, 0)", (teams[i], t_num, next_midnight))
                cursor.execute("UPDATE task_records SET task_total = task_total + 1 WHERE telegram_id IN (SELECT telegram_id FROM members WHERE team_name = %s) AND month = %s", (teams[i], month_name))
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

    # 3. Edit Instructions Menu
    elif data == "ei_task":
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT task_num, msg_1, msg_2, msg_3 FROM tasks ORDER BY task_num")
        tasks = cursor.fetchall()
        conn.close()
        if not tasks: return bot.answer_callback_query(call.id, "No Tasks Added Yet!", show_alert=True)
        admin_states['edit_task'] = {'tasks': tasks, 'exp': None}
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_task_edit_keyboard())
        
    elif data.startswith("ti_exp_"):
        t_num = int(data.split("_")[2])
        admin_states['edit_task']['exp'] = t_num if admin_states['edit_task'].get('exp') != t_num else None
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_task_edit_keyboard())
        
    elif data.startswith("ti_edit_"):
        parts = data.split("_")
        t_num, msg_idx = int(parts[2]), int(parts[3])
        task = next((t for t in admin_states['edit_task']['tasks'] if t['task_num'] == t_num), None)
        msg_text = task[f'msg_{msg_idx}']
        
        bot.delete_message(call.message.chat.id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Yes", callback_data=f"ti_yes_{t_num}_{msg_idx}"), InlineKeyboardButton("No", callback_data="acanc"))
        bot.send_message(call.message.chat.id, f"{msg_text}\n\nWould you like to change anything here?", reply_markup=markup)
        
    elif data.startswith("ti_yes_"):
        parts = data.split("_")
        t_num, msg_idx = int(parts[2]), int(parts[3])
        bot.delete_message(call.message.chat.id, call.message.message_id)
        msg = bot.send_message(call.message.chat.id, f"Input the new message for Task {t_num}, Msg {msg_idx}:")
        admin_states['wait_ti'] = {'t_num': t_num, 'msg_idx': msg_idx}
        bot.register_next_step_handler(msg, step_update_task_msg)

    # 4. Main Instructions Menu
    elif data == "ei_main":
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_main_inst_keyboard())
        
    elif data.startswith("mi_sel_"):
        sel_id = int(data.split("_")[2])
        admin_states['edit_main'] = sel_id
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_main_inst_keyboard(sel_id))
        
    elif data == "mi_click":
        sel_id = admin_states.get('edit_main')
        if not sel_id: return bot.answer_callback_query(call.id, "Select one first!")
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT instruction_text FROM custom_instructions WHERE id = %s", (sel_id,))
        inst = cursor.fetchone()
        conn.close()
        
        bot.delete_message(call.message.chat.id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Yes", callback_data=f"mi_yes_{sel_id}"), InlineKeyboardButton("No", callback_data="acanc"))
        bot.send_message(call.message.chat.id, f"{inst['instruction_text']}\n\nWould you like to change anything here?", reply_markup=markup)
        
    elif data.startswith("mi_yes_"):
        sel_id = int(data.split("_")[2])
        bot.delete_message(call.message.chat.id, call.message.message_id)
        msg = bot.send_message(call.message.chat.id, f"Input the new message for Instruction {sel_id}:", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc")))
        admin_states['wait_mi'] = sel_id
        bot.register_next_step_handler(msg, step_update_main_msg)
        
    elif data == "mi_add":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        msg = bot.send_message(call.message.chat.id, "Please provide the instruction number (1-5):", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc")))
        bot.register_next_step_handler(msg, step_add_main_msg)
        
    elif data == "ei_back":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Task Instructions", callback_data="ei_task"), InlineKeyboardButton("Main Instructions", callback_data="ei_main"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="acanc"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

# --- Admin Keyboards Builders ---
def get_admin_instruction_keyboard(sub_id, selected=None):
    markup = InlineKeyboardMarkup(row_width=6)
    labels = ['1', '2', '3', '4', '5', '❌']
    btns = [InlineKeyboardButton(f"{l}✅" if str(l) == str(selected) else l, callback_data=f"isel_{sub_id}_{l}") for l in labels]
    markup.add(*btns)
    markup.row(InlineKeyboardButton("Submit", callback_data=f"isub_{sub_id}"), InlineKeyboardButton("Cancel", callback_data="acanc"))
    return markup

def get_task_edit_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    state = admin_states.get('edit_task', {})
    for t in state.get('tasks', []):
        t_num = t['task_num']
        if state.get('exp') == t_num:
            markup.add(InlineKeyboardButton(f"Task - {t_num} 🔻", callback_data=f"ti_exp_{t_num}"))
            btns = []
            if t['msg_1']: btns.append(InlineKeyboardButton("1", callback_data=f"ti_edit_{t_num}_1"))
            if t['msg_2']: btns.append(InlineKeyboardButton("2", callback_data=f"ti_edit_{t_num}_2"))
            if t['msg_3']: btns.append(InlineKeyboardButton("3", callback_data=f"ti_edit_{t_num}_3"))
            markup.row(*btns)
        else:
            markup.add(InlineKeyboardButton(f"Task - {t_num} 🔺", callback_data=f"ti_exp_{t_num}"))
    markup.add(InlineKeyboardButton("Back", callback_data="ei_back"), InlineKeyboardButton("Cancel", callback_data="acanc"))
    return markup

def get_main_inst_keyboard(sel_id=None):
    markup = InlineKeyboardMarkup(row_width=1)
    for i in range(1, 6):
        icon = "❌" if i == sel_id else "🔳"
        markup.add(InlineKeyboardButton(f"Instruction - {i}   {icon}", callback_data=f"mi_sel_{i}"))
    markup.row(InlineKeyboardButton("Add", callback_data="mi_add"), InlineKeyboardButton("Submit", callback_data="mi_click"))
    markup.row(InlineKeyboardButton("Back", callback_data="ei_back"), InlineKeyboardButton("Cancel", callback_data="acanc"))
    return markup

# --- Admin Step Handlers ---
def step_update_task_msg(message):
    state = admin_states.get('wait_ti')
    if not state: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE tasks SET msg_{state['msg_idx']} = %s WHERE task_num = %s", (message.text, state['t_num']))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Successfully Replaced!✅")

def step_update_main_msg(message):
    sel_id = admin_states.get('wait_mi')
    if not sel_id: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE custom_instructions SET instruction_text = %s WHERE id = %s", (message.text, sel_id))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Successfully Replaced!✅")

def step_add_main_msg(message):
    try:
        num = int(message.text)
        if num < 1 or num > 5: raise ValueError
        admin_states['wait_mi'] = num
        msg = bot.send_message(message.chat.id, f"Please provide the text for instruction {num}:", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Cancel", callback_data="acanc")))
        bot.register_next_step_handler(msg, step_finalize_add_main)
    except: bot.send_message(message.chat.id, "Invalid number.")

def step_finalize_add_main(message):
    num = admin_states.get('wait_mi')
    if not num: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO custom_instructions (id, instruction_text) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET instruction_text = EXCLUDED.instruction_text", (num, message.text))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Your instruction has been successfully added✅")

def step_add_task_num(message):
    try:
        t_num = int(message.text)
        admin_states[message.from_user.id] = {'t_num': t_num}
        msg = bot.send_message(message.chat.id, "Give the first message of the task.")
        bot.register_next_step_handler(msg, step_add_msg1)
    except: bot.send_message(message.chat.id, "Invalid number.")

def step_add_msg1(message):
    if message.text.lower() == "end": return finalize_task(message.from_user.id, message.chat.id, 0)
    admin_states[message.from_user.id]['msg_1'] = message.text
    msg = bot.send_message(message.chat.id, "Give the second message of the task.\n(Type 'end' to finish)")
    bot.register_next_step_handler(msg, step_add_msg2)

def step_add_msg2(message):
    if message.text.lower() == "end": return finalize_task(message.from_user.id, message.chat.id, 1)
    admin_states[message.from_user.id]['msg_2'] = message.text
    msg = bot.send_message(message.chat.id, "Give the Third message of the task.\n(Type 'end' to finish)")
    bot.register_next_step_handler(msg, step_add_msg3)

def step_add_msg3(message):
    if message.text.lower() == "end": return finalize_task(message.from_user.id, message.chat.id, 2)
    admin_states[message.from_user.id]['msg_3'] = message.text
    finalize_task(message.from_user.id, message.chat.id, 3)

def finalize_task(adm_id, chat_id, count):
    state = admin_states.get(adm_id, {})
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (task_num, msg_1, msg_2, msg_3) VALUES (%s, %s, %s, %s) ON CONFLICT (task_num) DO UPDATE SET msg_1=EXCLUDED.msg_1, msg_2=EXCLUDED.msg_2, msg_3=EXCLUDED.msg_3", 
                   (state.get('t_num'), state.get('msg_1'), state.get('msg_2'), state.get('msg_3')))
    conn.commit()
    conn.close()
    bot.clear_step_handler_by_chat_id(chat_id)
    bot.send_message(chat_id, f"Your {max(1, count)} messages for Task-{state.get('t_num')} have been successfully added✅")

def step_org_task(message):
    seq = message.text.replace(" ", "")
    tasks = seq.split(',')
    conn = get_db_connection()
    cursor = conn.cursor()
    success, failed = [], []
    for t in tasks:
        cursor.execute("SELECT id FROM tasks WHERE task_num = %s", (t,))
        if cursor.fetchone(): success.append(t)
        else: failed.append(t)
        
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

# ⏰ Background Auto-Timer (Midnight Start & 24h Logic)
def auto_task_timer():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM active_assignments")
            assignments = cursor.fetchall()
            now = datetime.now()
            
            for assign in assignments:
                team, t_num, msg_lvl, sched = assign['team_name'], assign['task_num'], assign['current_msg'], assign['scheduled_for']
                is_st = assign['is_started']
                
                # Check Midnight Trigger
                if not is_st and now >= sched:
                    cursor.execute("UPDATE active_assignments SET is_started = TRUE, current_msg = 1 WHERE id = %s", (assign['id'],))
                    cursor.execute("SELECT msg_1 FROM tasks WHERE task_num = %s", (t_num,))
                    m1 = cursor.fetchone()['msg_1']
                    
                    cursor.execute("SELECT telegram_id FROM members WHERE team_name = %s AND status = 'Approved'", (team,))
                    for u in cursor.fetchall():
                        try: bot.send_message(u['telegram_id'], m1)
                        except: pass
                    continue
                
                if not is_st: continue # Not started yet
                
                # Check 24h & 48h logics after Trigger (sched = Trigger time)
                diff_hours = (now - sched).total_seconds() / 3600
                cursor.execute("SELECT msg_2, msg_3 FROM tasks WHERE task_num = %s", (t_num,))
                t_msgs = cursor.fetchone()
                
                cursor.execute("""
                    SELECT m.telegram_id FROM members m 
                    LEFT JOIN user_task_status uts ON m.telegram_id = uts.telegram_id AND uts.task_num = %s
                    WHERE m.team_name = %s AND m.status = 'Approved' 
                    AND (uts.completed IS NULL OR uts.completed = FALSE)
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
        time.sleep(1800) # Check every 30 mins

if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask)
    t_flask.daemon = True
    t_flask.start()
    
    t_timer = threading.Thread(target=auto_task_timer)
    t_timer.daemon = True
    t_timer.start()
    
    print("🤖 BKLn Task Submit Bot is Active...")
    
    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 409:
                print("Conflict Error 409: Waiting for older instance to shut down...")
                time.sleep(10) # 409 এরর আসলে বট ক্র্যাশ না করে ১০ সেকেন্ড ওয়েট করবে
            else:
                time.sleep(5)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)
