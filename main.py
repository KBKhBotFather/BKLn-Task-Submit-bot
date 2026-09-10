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

# 🌐 Flask Server for UptimeRobot
app = Flask(__name__)

@app.route('/')
def home(): 
    return "BKLn Task Submit Bot is Alive & Running!", 200

@app.route('/ping')
def ping():
    return "OK", 200

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
                id SERIAL PRIMARY KEY, sequence TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS active_assignments (
                id SERIAL PRIMARY KEY, team_name TEXT, task_num INT, assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, current_msg INT DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS user_task_status (
                telegram_id BIGINT, task_num INT, completed BOOLEAN DEFAULT FALSE, UNIQUE(telegram_id, task_num)
            );
        """)
        conn.commit()
        conn.close()
    except Exception as e: print(f"DB Error: {e}")

init_db()

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

@bot.message_handler(content_types=['photo'])
def handle_photo_submission(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_CHAT_ID: return 
    user = get_member_info(tg_id)
    if not user: return
    
    if tg_id in user_states and user_states[tg_id].get('status') == 'submitting':
        bot.send_message(message.chat.id, "Submit or Cancel your previous meme first!❌\nPlease submit one Meme at a time...")
        return

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT task_num FROM active_assignments WHERE team_name = %s", (user['team_name'],))
    active_task = cursor.fetchone()
    conn.close()

    user_states[tg_id] = {'status': 'submitting', 'photo_id': message.photo[-1].file_id}
    markup = InlineKeyboardMarkup(row_width=2)
    
    if active_task:
        user_states[tg_id]['active_task_num'] = active_task['task_num']
        markup.add(InlineKeyboardButton("General Post", callback_data="type_general"), InlineKeyboardButton("Special Task", callback_data="type_sptask"))
    else:
        markup.add(InlineKeyboardButton("General Post", callback_data="type_general"), InlineKeyboardButton("Special Post", callback_data="type_special"))
        
    markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
    bot.send_photo(message.chat.id, message.photo[-1].file_id, reply_markup=markup)

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
        
    elif data == "type_general":
        state['selected_type'] = 'General Post'
        markup = InlineKeyboardMarkup(row_width=2)
        if 'active_task_num' in state: markup.add(InlineKeyboardButton("General Post✅", callback_data="ignore"), InlineKeyboardButton("Special Task", callback_data="type_sptask"))
        else: markup.add(InlineKeyboardButton("General Post✅", callback_data="ignore"), InlineKeyboardButton("Special Post", callback_data="type_special"))
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "type_sptask":
        state['selected_type'] = 'Task'
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("General Post", callback_data="type_general"), InlineKeyboardButton("Special Task✅", callback_data="ignore"))
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
        markup.add(*[InlineKeyboardButton(f"{v}✅" if k == data else v, callback_data=k) for k, v in cat_map.items()])
        markup.row(InlineKeyboardButton("Submit", callback_data="sub_final"), InlineKeyboardButton("Cancel", callback_data="sub_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "sub_final":
        sel_type = state.get('selected_type')
        if not sel_type or (sel_type == 'Special Post' and 'special_cat' not in state):
            bot.answer_callback_query(call.id, "Select an option first!", show_alert=True)
            return
            
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
        user_states.pop(tg_id, None)

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
        for r in records: markup.add(InlineKeyboardButton(f"Fb Name: {r['fb_name']} | Total Content: {r['total']}", callback_data=f"adm_rev_{r['telegram_id']}"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
        bot.send_message(message.chat.id, "Pending List:", reply_markup=markup)
    else:
        cursor.execute("SELECT photo_id, assigned_instruction FROM submissions WHERE telegram_id = %s", (tg_id,))
        subs = cursor.fetchall()
        if not subs: bot.send_message(message.chat.id, "No Pending content available!")
        for sub in subs: bot.send_photo(message.chat.id, sub['photo_id'], caption=f"Instruction: {sub['assigned_instruction'] if sub['assigned_instruction'] else 'Pending⏳'}")
    conn.close()

@bot.message_handler(func=lambda msg: msg.text == "Task Assign")
def task_assign_menu(message):
    if str(message.from_user.id) != ADMIN_CHAT_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Add Task", callback_data="ta_add"), InlineKeyboardButton("Task List", callback_data="ta_list"))
    markup.add(InlineKeyboardButton("Edit Task", callback_data="ta_edit"), InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
    bot.send_message(message.chat.id, "Task Assign Menu:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_") or call.data.startswith("inst_") or call.data.startswith("ta_"))
def admin_callbacks(call):
    adm_id = call.from_user.id
    if str(adm_id) != ADMIN_CHAT_ID: return
    data = call.data

    if data == "adm_cancel":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "Process Cancelled✅")
        admin_states.pop(adm_id, None)
        
    elif data == "ta_add":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        msg = bot.send_message(call.message.chat.id, "Task Number?")
        bot.register_next_step_handler(msg, step_add_task_num)
        
    elif data == "ta_list":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Organize Task", callback_data="ta_org"), InlineKeyboardButton("Assign Task", callback_data="ta_assign"))
        markup.row(InlineKeyboardButton("Back", callback_data="ta_back_main"), InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
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
        
        if not orgs:
            bot.answer_callback_query(call.id, "No Tasks organized❌", show_alert=True)
            return
            
        admin_states[adm_id] = {'action': 'assigning', 'orgs': orgs}
        markup = InlineKeyboardMarkup(row_width=1)
        for org in orgs: markup.add(InlineKeyboardButton(f"Task {org['sequence'].replace(',', '  ')}   🔳", callback_data=f"ta_asgsel_{org['id']}"))
        markup.add(InlineKeyboardButton("Back", callback_data="ta_list"), InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data.startswith("ta_asgsel_"):
        org_id = int(data.split("_")[2])
        state = admin_states.get(adm_id, {})
        markup = InlineKeyboardMarkup(row_width=1)
        for org in state.get('orgs', []):
            icon = "✅" if org['id'] == org_id else "🔳"
            markup.add(InlineKeyboardButton(f"Task {org['sequence'].replace(',', '  ')}   {icon}", callback_data=f"ta_asgsel_{org['id']}"))
        state['selected_org'] = org_id
        markup.add(InlineKeyboardButton("Submit", callback_data="ta_asgsub"), InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data == "ta_asgsub":
        state = admin_states.get(adm_id, {})
        org_id = state.get('selected_org')
        if not org_id: return bot.answer_callback_query(call.id, "Select one first!")
        
        seq_str = next((o['sequence'] for o in state['orgs'] if o['id'] == org_id), None)
        tasks = seq_str.split(',')
        teams = ['Team Electron', 'Team Proton', 'Team Neutron']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        for i, t_num in enumerate(tasks):
            if i < len(teams):
                cursor.execute("DELETE FROM active_assignments WHERE team_name = %s", (teams[i],))
                cursor.execute("INSERT INTO active_assignments (team_name, task_num) VALUES (%s, %s)", (teams[i], t_num))
        conn.commit()
        conn.close()
        
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, f"Task: {seq_str.replace(',',' ')} Assigned Successfully✅")
        
    elif data == "ta_back_main":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Add Task", callback_data="ta_add"), InlineKeyboardButton("Task List", callback_data="ta_list"))
        markup.add(InlineKeyboardButton("Edit Task", callback_data="ta_edit"), InlineKeyboardButton("Cancel", callback_data="adm_cancel"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("adm_rev_"):
        target_tg_id = int(data.split("_")[2])
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT s.*, m.fb_name FROM submissions s JOIN members m ON s.telegram_id = m.telegram_id WHERE s.telegram_id = %s AND s.status = 'Pending' LIMIT 1", (target_tg_id,))
        sub = cursor.fetchone()
        conn.close()
        if not sub: return bot.answer_callback_query(call.id, "No pending content.")
        admin_states[adm_id] = {'sub_id': sub['id'], 'tg_id': target_tg_id, 'c_type': sub['content_type']}
        bot.send_photo(call.message.chat.id, sub['photo_id'], caption=f"Fb Name: {sub['fb_name']}", reply_markup=get_instruction_keyboard())

    elif data.startswith("inst_sel_"):
        sel = data.split("_")[2]
        admin_states[adm_id]['selected'] = sel
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_instruction_keyboard(sel))
        
    elif data == "inst_submit":
        state = admin_states.get(adm_id, {})
        sel = state.get('selected')
        if not sel: return bot.answer_callback_query(call.id, "Select instruction!", show_alert=True)
        
        inst_texts = {'1': "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং মিম পেইজ দিয়েই কিছুটা সময় পর সকল গ্রুপে পোস্ট করুন।", '2': "অফিসিয়াল মিম পেইজ এ পোস্ট করুন এবং কিছুটা সময় পর মিম পেইজ দিয়েই শুধুমাত্র মিমগ্রুপে পোস্ট করুন।", '3': "নিজ মডারেটর আইডি থেকে সকল গ্রুপে পোস্ট করুন।", '4': "নিজ মডারেটর আইডি থেকে শুধুমাত্র মিম গ্রুপে পোস্ট করুন।", '5': "নিজ মডারেটর আইডি থেকে Annonymous ভাবে বা সেকেন্ড যেকোনো আইডি থেকে সরাসরি শুধু মিম গ্রুপে পোস্ট করুন।", '❌': "মিমটি পোস্টযোগ্য নয়। প্রয়োজনে যেকোনো সিনিয়র সদস্যের সাথে যোগাযোগ করুন।" }
        msg_text = inst_texts[sel]
        tg_id, sub_id, c_type = state['tg_id'], state['sub_id'], state['c_type']
        month_name = datetime.now().strftime("%B")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE submissions SET status = 'Reviewed', assigned_instruction = %s WHERE id = %s", (msg_text, sub_id))
        if sel != '❌':
            cursor.execute("INSERT INTO task_records (telegram_id, month, general_post, special_post, task_done) VALUES (%s, %s, 0, 0, 0) ON CONFLICT (telegram_id, month) DO NOTHING;", (tg_id, month_name))
            if c_type == 'General Post': cursor.execute("UPDATE task_records SET general_post = general_post + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
            elif c_type == 'Special Post': cursor.execute("UPDATE task_records SET special_post = special_post + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
            elif c_type.startswith('Task'): cursor.execute("UPDATE task_records SET task_done = task_done + 1 WHERE telegram_id = %s AND month = %s", (tg_id, month_name))
        conn.commit()
        conn.close()
        bot.delete_message(call.message.chat.id, call.message.message_id)
        try: bot.send_message(tg_id, f"Instruction: {msg_text}")
        except: pass
        bot.send_message(call.message.chat.id, "Instruction Sent & Count Updated!✅")

def step_add_task_num(message):
    if message.text == "Cancel": return bot.send_message(message.chat.id, "Process Cancelled✅")
    try:
        t_num = int(message.text)
        admin_states[message.from_user.id] = {'t_num': t_num}
        msg = bot.send_message(message.chat.id, "Give the first message of the task.")
        bot.register_next_step_handler(msg, step_add_msg1)
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number. Cancelled.")

def step_add_msg1(message):
    if message.text.lower() == "end": return finalize_task(message, 0)
    admin_states[message.from_user.id]['msg_1'] = message.text
    msg = bot.send_message(message.chat.id, "Give the second message of the task.\n(Type 'end' to finish)")
    bot.register_next_step_handler(msg, step_add_msg2)

def step_add_msg2(message):
    if message.text.lower() == "end": return finalize_task(message, 1)
    admin_states[message.from_user.id]['msg_2'] = message.text
    msg = bot.send_message(message.chat.id, "Give the Third message of the task.\n(Type 'end' to finish)")
    bot.register_next_step_handler(msg, step_add_msg3)

def step_add_msg3(message):
    if message.text.lower() == "end": return finalize_task(message, 2)
    admin_states[message.from_user.id]['msg_3'] = message.text
    finalize_task(message, 3)

def finalize_task(message, count):
    state = admin_states.get(message.from_user.id, {})
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (task_num, msg_1, msg_2, msg_3) VALUES (%s, %s, %s, %s) ON CONFLICT (task_num) DO UPDATE SET msg_1=EXCLUDED.msg_1, msg_2=EXCLUDED.msg_2, msg_3=EXCLUDED.msg_3", 
                   (state.get('t_num'), state.get('msg_1'), state.get('msg_2'), state.get('msg_3')))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"Your {max(1, count)} messages for Task-{state.get('t_num')} have been successfully added✅")

def step_org_task(message):
    if message.text == "Cancel": return bot.send_message(message.chat.id, "Process Cancelled✅")
    seq = message.text.replace(" ", "")
    tasks = seq.split(',')
    conn = get_db_connection()
    cursor = conn.cursor()
    success = []
    failed = []
    for t in tasks:
        cursor.execute("SELECT id FROM tasks WHERE task_num = %s", (t,))
        if cursor.fetchone(): success.append(t)
        else: failed.append(t)
        
    if success and not failed:
        cursor.execute("INSERT INTO organized_tasks (sequence) VALUES (%s) ON CONFLICT (sequence) DO NOTHING", (seq,))
        bot.send_message(message.chat.id, "Task list added successfully✅")
    else:
        msg = ""
        if success: msg += f"Task {','.join(success)} added successfully✅\n"
        if failed: msg += f"Task {','.join(failed)} doesn't exist❌"
        bot.send_message(message.chat.id, msg)
    conn.commit()
    conn.close()

def auto_task_timer():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM active_assignments")
            assignments = cursor.fetchall()
            now = datetime.now()
            
            for assign in assignments:
                diff = (now - assign['assigned_at']).total_seconds() / 3600
                team, t_num, msg_lvl = assign['team_name'], assign['task_num'], assign['current_msg']
                
                cursor.execute("SELECT msg_1, msg_2, msg_3 FROM tasks WHERE task_num = %s", (t_num,))
                t_msgs = cursor.fetchone()
                if not t_msgs: continue
                
                cursor.execute("""
                    SELECT m.telegram_id FROM members m 
                    LEFT JOIN user_task_status uts ON m.telegram_id = uts.telegram_id AND uts.task_num = %s
                    WHERE m.team_name = %s AND m.status = 'Approved' AND m.is_blocked = FALSE AND m.is_removed = FALSE
                    AND (uts.completed IS NULL OR uts.completed = FALSE)
                """, (t_num, team))
                pending_users = cursor.fetchall()

                if diff >= 24 and msg_lvl == 1 and t_msgs['msg_2']:
                    for u in pending_users:
                        try: bot.send_message(u['telegram_id'], t_msgs['msg_2'])
                        except: pass
                    cursor.execute("UPDATE active_assignments SET current_msg = 2 WHERE id = %s", (assign['id'],))
                    
                elif diff >= 48 and msg_lvl == 2 and t_msgs['msg_3']:
                    for u in pending_users:
                        try: bot.send_message(u['telegram_id'], t_msgs['msg_3'])
                        except: pass
                    cursor.execute("UPDATE active_assignments SET current_msg = 3 WHERE id = %s", (assign['id'],))
                    
                elif diff >= 72:
                    cursor.execute("DELETE FROM active_assignments WHERE id = %s", (assign['id'],))
            conn.commit()
            conn.close()
        except Exception as e: print("Timer Error:", e)
        time.sleep(3600)

if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask)
    t_flask.daemon = True
    t_flask.start()
    
    t_timer = threading.Thread(target=auto_task_timer)
    t_timer.daemon = True
    t_timer.start()
    
    print("🤖 BKLn Task Submit Bot is Active...")
    
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception: pass

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)
