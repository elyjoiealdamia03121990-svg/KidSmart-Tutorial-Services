"""KidSmart AI Tutor - server (Flask).
PostgreSQL when DATABASE_URL is set (Render), otherwise a local SQLite file.
Environment: DATABASE_URL, SECRET_KEY, ADMIN_PASSWORD, ANTHROPIC_API_KEY (optional), ANTHROPIC_MODEL (optional).
"""
import os, json, random, sqlite3, datetime as dt
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash, check_password_hash
import tutor

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

DB_URL = os.environ.get('DATABASE_URL', '').strip()
PG = DB_URL.startswith('postgres')
SECRET = os.environ.get('SECRET_KEY', 'kidsmart-local-secret')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
signer = URLSafeTimedSerializer(SECRET, salt='kidsmart-auth')
TOKEN_AGE = 60 * 60 * 24 * 30

if PG:
    import psycopg2, psycopg2.extras

# ---------------------------------------------------------------- database
def conn():
    if PG:
        return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    c = sqlite3.connect(os.path.join(BASE, 'kidsmart.db'))
    c.row_factory = sqlite3.Row
    return c

def q(sql, params=(), one=False):
    if PG:
        sql = sql.replace('?', '%s')
    c = conn()
    try:
        cur = c.cursor()
        cur.execute(sql, list(params))
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        c.commit()
        return (rows[0] if rows else None) if one else rows
    finally:
        c.close()

def insert(table, data):
    keys = list(data.keys())
    sql = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join(['?'] * len(keys))})"
    c = conn()
    try:
        cur = c.cursor()
        if PG:
            cur.execute(sql.replace('?', '%s') + ' RETURNING id', [data[k] for k in keys])
            new_id = cur.fetchone()['id']
        else:
            cur.execute(sql, [data[k] for k in keys])
            new_id = cur.lastrowid
        c.commit()
        return new_id
    finally:
        c.close()

def update(table, row_id, data, allowed):
    data = {k: v for k, v in data.items() if k in allowed}
    if not data:
        return
    sets = ','.join(f"{k}=?" for k in data)
    q(f"UPDATE {table} SET {sets} WHERE id=?", list(data.values()) + [row_id])

PK = 'SERIAL PRIMARY KEY' if PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
NUM = 'DOUBLE PRECISION' if PG else 'REAL'
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS ai_settings (k TEXT PRIMARY KEY, v TEXT)",
    f"CREATE TABLE IF NOT EXISTS ai_plans (id {PK}, name TEXT, days INTEGER, price {NUM}, description TEXT, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0)",
    f"CREATE TABLE IF NOT EXISTS ai_parents (id {PK}, name TEXT, phone TEXT, email TEXT, password TEXT, trial_until TEXT, sub_until TEXT, status TEXT DEFAULT 'active', admin_notes TEXT, created_at TEXT, last_login TEXT)",
    f"CREATE TABLE IF NOT EXISTS ai_children (id {PK}, parent_id INTEGER, name TEXT, birth_year INTEGER, grade INTEGER, avatar TEXT, lang TEXT, active INTEGER DEFAULT 1, created_at TEXT)",
    f"""CREATE TABLE IF NOT EXISTS ai_payments (id {PK}, code TEXT, parent_id INTEGER, plan_id INTEGER, plan_name TEXT, days INTEGER,
        amount {NUM}, method TEXT, ref TEXT, receipt TEXT, status TEXT, created_at TEXT, verified_at TEXT, notes TEXT)""",
    f"CREATE TABLE IF NOT EXISTS ai_progress (id {PK}, child_id INTEGER, subject TEXT, level INTEGER, rounds INTEGER DEFAULT 0, correct INTEGER DEFAULT 0, total INTEGER DEFAULT 0, stars INTEGER DEFAULT 0, good_streak INTEGER DEFAULT 0, updated_at TEXT)",
    f"""CREATE TABLE IF NOT EXISTS ai_rounds (id {PK}, child_id INTEGER, subject TEXT, level INTEGER, source TEXT, items TEXT,
        n_items INTEGER, n_correct INTEGER, seconds INTEGER, finished INTEGER DEFAULT 0, in_tokens INTEGER DEFAULT 0,
        out_tokens INTEGER DEFAULT 0, cost_usd {NUM} DEFAULT 0, day TEXT, created_at TEXT)""",
    f"CREATE TABLE IF NOT EXISTS ai_mistakes (id {PK}, child_id INTEGER, subject TEXT, prompt TEXT, given TEXT, correct_answer TEXT, skill TEXT, created_at TEXT)",
    f"CREATE TABLE IF NOT EXISTS ai_expenses (id {PK}, date TEXT, category TEXT, amount {NUM}, note TEXT, created_at TEXT)",
]

DEFAULT_SETTINGS = {
    'business_name': 'KidSmart AI Tutor',
    'tutor_name': 'Teacher Kiko',
    'tagline': 'A friendly AI tutor for Preschool to Grade 4',
    'phone': '09XX XXX XXXX', 'email': '', 'facebook': '',
    'trial_days': '3',
    'daily_minutes': '30',
    'max_rounds_day': '12',
    'max_children': '3',
    'ai_enabled': '1',
    'usd_php': '58',
    'ai_price_in': '1',
    'ai_price_out': '5',
    'gcash_name': '', 'gcash_number': '',
    'maya_name': '', 'maya_number': '',
    'bank_name': '', 'bank_acct_name': '', 'bank_acct_no': '',
    'policy': 'Subscriptions start once we confirm your payment (usually within the day). Payments are non-refundable. '
              'A parent or guardian should be nearby while the child uses the app.',
}
DEFAULT_PLANS = [('Monthly', 30, 299, 'All subjects, up to 3 children', 1),
                 ('3 Months', 90, 799, 'Save ₱98 - best for one school quarter', 2),
                 ('6 Months', 180, 1499, 'Save ₱295 - one full semester', 3)]

def init_db():
    for s in SCHEMA:
        q(s)
    have = {r['k'] for r in q("SELECT k FROM ai_settings")}
    for k, v in DEFAULT_SETTINGS.items():
        if k not in have:
            q("INSERT INTO ai_settings (k, v) VALUES (?, ?)", (k, v))
    if not q("SELECT id FROM ai_plans LIMIT 1"):
        for name, days, price, desc, sort in DEFAULT_PLANS:
            insert('ai_plans', {'name': name, 'days': days, 'price': price, 'description': desc, 'active': 1, 'sort': sort})

init_db()

# ---------------------------------------------------------------- helpers
PUBLIC_KEYS = ['business_name', 'tutor_name', 'tagline', 'phone', 'email', 'facebook', 'trial_days', 'daily_minutes',
               'max_children', 'gcash_name', 'gcash_number', 'maya_name', 'maya_number', 'bank_name', 'bank_acct_name',
               'bank_acct_no', 'policy']
AVATARS = ['🦁', '🐼', '🐯', '🐸', '🦊', '🐨', '🐵', '🦄', '🐙', '🐢', '🐧', '🦖']

def settings():
    return {r['k']: r['v'] for r in q("SELECT k, v FROM ai_settings")}

def ph_now():
    return dt.datetime.utcnow() + dt.timedelta(hours=8)

def now():
    return ph_now().strftime('%Y-%m-%d %H:%M')

def today():
    return ph_now().date()

def num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def err(msg, code=400):
    return jsonify({'ok': False, 'error': msg}), code

def body():
    return request.get_json(silent=True) or {}

def clean(s, n=300):
    return str(s if s is not None else '').strip()[:n]

def norm_phone(p):
    return ''.join(ch for ch in str(p or '') if ch.isdigit())[-10:]

def new_code(prefix='P'):
    while True:
        c = prefix + '-' + ''.join(random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
        if not q("SELECT id FROM ai_payments WHERE code=?", [c], one=True):
            return c

def sub_status(p):
    t = today().isoformat()
    if p.get('status') == 'blocked':
        return {'state': 'blocked', 'until': None}
    if p.get('sub_until') and p['sub_until'] >= t:
        return {'state': 'active', 'until': p['sub_until']}
    if p.get('trial_until') and p['trial_until'] >= t:
        return {'state': 'trial', 'until': p['trial_until']}
    return {'state': 'expired', 'until': p.get('sub_until') or p.get('trial_until')}

def child_age(c):
    return max(2, today().year - int(c['birth_year'] or today().year - 6))

def public_parent(p):
    return {'id': p['id'], 'name': p['name'], 'phone': p['phone'], 'email': p['email'], 'subscription': sub_status(p)}

# ---------------------------------------------------------------- auth
def make_token(role, pid=None):
    return signer.dumps({'role': role, 'pid': pid})

def require(*roles):
    def deco(f):
        @wraps(f)
        def inner(*a, **k):
            h = request.headers.get('Authorization', '')
            tok = h[7:] if h.startswith('Bearer ') else ''
            try:
                data = signer.loads(tok, max_age=TOKEN_AGE)
            except (BadSignature, SignatureExpired):
                return err('Please log in again', 401)
            if data.get('role') not in roles:
                return err('Not allowed', 403)
            g.user = data
            if data['role'] == 'parent':
                p = q("SELECT * FROM ai_parents WHERE id=?", [data['pid']], one=True)
                if not p:
                    return err('Please log in again', 401)
                if p['status'] == 'blocked':
                    return err('This account is on hold. Please contact us.', 403)
                g.parent = p
            return f(*a, **k)
        return inner
    return deco

def own_child(cid):
    return q("SELECT * FROM ai_children WHERE id=? AND parent_id=? AND active=1", [cid, g.parent['id']], one=True)

# ---------------------------------------------------------------- pages
@app.get('/')
def page_home():
    return send_from_directory(BASE, 'index.html')

@app.get('/app')
def page_app():
    return send_from_directory(BASE, 'app.html')

@app.get('/admin')
def page_admin():
    return send_from_directory(BASE, 'admin.html')

@app.get('/healthz')
def health():
    return 'ok'

# ---------------------------------------------------------------- public + parent auth
@app.get('/api/public/config')
def public_config():
    s = settings()
    return jsonify({'ok': True, 'settings': {k: s.get(k) for k in PUBLIC_KEYS},
                    'plans': q("SELECT id, name, days, price, description FROM ai_plans WHERE active=1 ORDER BY sort, id"),
                    'subjects': tutor.SUBJECTS, 'levels': tutor.LEVELS, 'avatars': AVATARS, 'today': today().isoformat()})

@app.post('/api/auth/register')
def register():
    b = body()
    name, phone, pw = clean(b.get('name'), 120), clean(b.get('phone'), 30), str(b.get('password') or '')
    if not name:
        return err('Please enter your name')
    if len(norm_phone(phone)) < 10:
        return err('Please enter a valid mobile number')
    if len(pw) < 6:
        return err('Password must be at least 6 characters')
    np_ = norm_phone(phone)
    if any(norm_phone(p['phone']) == np_ for p in q("SELECT phone FROM ai_parents")):
        return err('This mobile number is already registered. Please log in.')
    s = settings()
    trial = (today() + dt.timedelta(days=max(0, int(num(s.get('trial_days'), 3))) - 1)).isoformat() if int(num(s.get('trial_days'), 3)) > 0 else None
    pid = insert('ai_parents', {'name': name, 'phone': phone, 'email': clean(b.get('email'), 120),
                             'password': generate_password_hash(pw), 'trial_until': trial, 'sub_until': None,
                             'status': 'active', 'admin_notes': '', 'created_at': now(), 'last_login': now()})
    p = q("SELECT * FROM ai_parents WHERE id=?", [pid], one=True)
    return jsonify({'ok': True, 'token': make_token('parent', pid), 'parent': public_parent(p)})

@app.post('/api/auth/login')
def login():
    b = body()
    np_ = norm_phone(b.get('phone'))
    p = next((x for x in q("SELECT * FROM ai_parents") if norm_phone(x['phone']) == np_ and np_), None)
    if not p or not check_password_hash(p['password'], str(b.get('password') or '')):
        return err('Wrong mobile number or password', 401)
    if p['status'] == 'blocked':
        return err('This account is on hold. Please contact us.', 403)
    q("UPDATE ai_parents SET last_login=? WHERE id=?", [now(), p['id']])
    return jsonify({'ok': True, 'token': make_token('parent', p['id']), 'parent': public_parent(p)})

@app.post('/api/admin/login')
def admin_login():
    if body().get('password') == ADMIN_PASSWORD:
        return jsonify({'ok': True, 'token': make_token('admin')})
    return err('Wrong password', 401)

# ---------------------------------------------------------------- parent API
def today_usage(cid):
    r = q("SELECT COALESCE(SUM(seconds),0) AS s, COUNT(*) AS n FROM ai_rounds WHERE child_id=? AND day=?", [cid, today().isoformat()], one=True)
    return int(r['s']), int(r['n'])

def child_summary(c):
    prog = {p['subject']: p for p in q("SELECT * FROM ai_progress WHERE child_id=?", [c['id']])}
    secs, n = today_usage(c['id'])
    stars = sum(int(p['stars'] or 0) for p in prog.values())
    subs = []
    for s in tutor.subjects_for(int(c['grade'])):
        p = prog.get(s['id'])
        lvl = p['level'] if p else tutor.start_level(s['id'], int(c['grade']))
        subs.append({'id': s['id'], 'name': s['name'], 'emoji': s['emoji'], 'color': s['color'], 'level': lvl,
                     'level_name': tutor.LEVELS[lvl - 1], 'rounds': p['rounds'] if p else 0,
                     'accuracy': round(100 * p['correct'] / p['total']) if p and p['total'] else None, 'stars': p['stars'] if p else 0})
    return {'id': c['id'], 'name': c['name'], 'avatar': c['avatar'], 'grade': c['grade'], 'grade_name': tutor.LEVELS[int(c['grade']) - 1],
            'age': child_age(c), 'lang': c['lang'], 'stars': stars, 'minutes_today': round(secs / 60), 'rounds_today': n, 'subjects': subs}

@app.get('/api/me')
@require('parent')
def me():
    p = g.parent
    s = settings()
    kids = q("SELECT * FROM ai_children WHERE parent_id=? AND active=1 ORDER BY id", [p['id']])
    pend = q("SELECT code, plan_name, amount, status, created_at FROM ai_payments WHERE parent_id=? ORDER BY id DESC LIMIT 10", [p['id']])
    return jsonify({'ok': True, 'parent': public_parent(p), 'children': [child_summary(c) for c in kids], 'payments': pend,
                    'daily_minutes': int(num(s.get('daily_minutes'), 30)), 'max_children': int(num(s.get('max_children'), 3))})

def child_fields(b):
    s = settings()
    name = clean(b.get('name'), 60)
    try:
        age, grade = int(b.get('age')), int(b.get('grade'))
    except (TypeError, ValueError):
        return None, 'Please enter age and grade'
    if not name:
        return None, "Please enter your child's name"
    if not 1 <= grade <= 6:
        return None, 'Please choose a grade (Preschool to Grade 4)'
    if not 2 <= age <= 12:
        return None, 'This app is for children aged 3 to 10'
    lang = b.get('lang') if b.get('lang') in ('en', 'both') else 'both'
    avatar = b.get('avatar') if b.get('avatar') in AVATARS else random.choice(AVATARS)
    return {'name': name, 'birth_year': today().year - age, 'grade': grade, 'lang': lang, 'avatar': avatar}, None

@app.post('/api/children')
@require('parent')
def add_child():
    data, e = child_fields(body())
    if e:
        return err(e)
    mx = int(num(settings().get('max_children'), 3))
    if q("SELECT COUNT(*) AS n FROM ai_children WHERE parent_id=? AND active=1", [g.parent['id']], one=True)['n'] >= mx:
        return err(f'You can add up to {mx} children per account')
    cid = insert('ai_children', dict(data, parent_id=g.parent['id'], active=1, created_at=now()))
    return jsonify({'ok': True, 'id': cid})

@app.post('/api/children/<int:cid>')
@require('parent')
def edit_child(cid):
    c = own_child(cid)
    if not c:
        return err('Not found', 404)
    b = body()
    if b.get('remove'):
        q("UPDATE ai_children SET active=0 WHERE id=?", [cid])
        return jsonify({'ok': True})
    data, e = child_fields(b)
    if e:
        return err(e)
    update('ai_children', cid, data, data.keys())
    if b.get('reset_levels'):
        q("DELETE FROM ai_progress WHERE child_id=?", [cid])
    return jsonify({'ok': True})

@app.get('/api/children/<int:cid>/report')
@require('parent', 'admin')
def child_report(cid):
    if g.user['role'] == 'parent':
        c = own_child(cid)
    else:
        c = q("SELECT * FROM ai_children WHERE id=?", [cid], one=True)
    if not c:
        return err('Not found', 404)
    start = (today() - dt.timedelta(days=13)).isoformat()
    daily = q("""SELECT day, COALESCE(SUM(seconds),0) AS secs, COUNT(*) AS rounds, COALESCE(SUM(n_correct),0) AS correct,
                 COALESCE(SUM(n_items),0) AS total FROM ai_rounds WHERE child_id=? AND finished=1 AND day>=? GROUP BY day ORDER BY day""", [cid, start])
    by_day = {d['day']: d for d in daily}
    days = []
    for i in range(13, -1, -1):
        d = (today() - dt.timedelta(days=i)).isoformat()
        x = by_day.get(d)
        days.append({'day': d, 'minutes': round(int(x['secs']) / 60) if x else 0, 'rounds': int(x['rounds']) if x else 0})
    mist = q("SELECT subject, prompt, given, correct_answer, skill, created_at FROM ai_mistakes WHERE child_id=? ORDER BY id DESC LIMIT 15", [cid])
    skills = q("""SELECT subject, skill, COUNT(*) AS n FROM ai_mistakes WHERE child_id=? AND created_at>=? AND skill<>''
                  GROUP BY subject, skill ORDER BY n DESC LIMIT 6""", [cid, start])
    recent = q("SELECT subject, level, n_items, n_correct, seconds, created_at FROM ai_rounds WHERE child_id=? AND finished=1 ORDER BY id DESC LIMIT 10", [cid])
    for r in recent:
        r['level_name'] = tutor.LEVELS[int(r['level']) - 1]
    return jsonify({'ok': True, 'child': child_summary(c), 'days': days, 'mistakes': mist, 'weak_skills': skills, 'recent': recent})

@app.post('/api/payments')
@require('parent')
def add_payment():
    b = body()
    plan = q("SELECT * FROM ai_plans WHERE id=? AND active=1", [b.get('plan_id')], one=True)
    if not plan:
        return err('Please choose a plan')
    if b.get('method') not in ('gcash', 'maya', 'bank'):
        return err('Please choose how you paid')
    rc = b.get('receipt') or ''
    if not rc.startswith('data:image'):
        return err('Please attach a photo of your receipt')
    if q("SELECT id FROM ai_payments WHERE parent_id=? AND status='for_verification'", [g.parent['id']], one=True):
        return err('You already have a payment waiting for checking. Please wait for our confirmation.')
    code = new_code()
    insert('ai_payments', {'code': code, 'parent_id': g.parent['id'], 'plan_id': plan['id'], 'plan_name': plan['name'], 'days': plan['days'],
                        'amount': plan['price'], 'method': b['method'], 'ref': clean(b.get('ref'), 80), 'receipt': rc,
                        'status': 'for_verification', 'created_at': now(), 'verified_at': None, 'notes': ''})
    return jsonify({'ok': True, 'code': code})

@app.post('/api/account')
@require('parent')
def edit_account():
    b = body()
    data = {}
    if clean(b.get('name')):
        data['name'] = clean(b.get('name'), 120)
    if 'email' in b:
        data['email'] = clean(b.get('email'), 120)
    if b.get('new_password'):
        if not check_password_hash(g.parent['password'], str(b.get('password') or '')):
            return err('Current password is wrong')
        if len(str(b['new_password'])) < 6:
            return err('New password must be at least 6 characters')
        data['password'] = generate_password_hash(str(b['new_password']))
    update('ai_parents', g.parent['id'], data, {'name', 'email', 'password'})
    return jsonify({'ok': True})

# ---------------------------------------------------------------- learning
def get_progress(cid, subject, grade):
    p = q("SELECT * FROM ai_progress WHERE child_id=? AND subject=?", [cid, subject], one=True)
    if not p:
        insert('ai_progress', {'child_id': cid, 'subject': subject, 'level': tutor.start_level(subject, grade), 'rounds': 0,
                            'correct': 0, 'total': 0, 'stars': 0, 'good_streak': 0, 'updated_at': now()})
        p = q("SELECT * FROM ai_progress WHERE child_id=? AND subject=?", [cid, subject], one=True)
    return p

@app.post('/api/learn/start')
@require('parent')
def learn_start():
    b = body()
    c = own_child(b.get('child_id'))
    if not c:
        return err('Child not found', 404)
    subject = b.get('subject')
    if subject not in tutor.SUBJ:
        return err('Choose a subject')
    st = sub_status(g.parent)
    if st['state'] not in ('active', 'trial'):
        return err('Your free trial or subscription has ended. Please renew to keep learning.', 402)
    s = settings()
    secs, n = today_usage(c['id'])
    if secs >= int(num(s.get('daily_minutes'), 30)) * 60 or n >= int(num(s.get('max_rounds_day'), 12)):
        return err('Great work today! Time to rest your eyes. Come back tomorrow.', 429)
    p = get_progress(c['id'], subject, int(c['grade']))
    mistakes = q("SELECT prompt, correct_answer FROM ai_mistakes WHERE child_id=? AND subject=? ORDER BY id DESC LIMIT 5", [c['id'], subject])
    rnd = tutor.make_round(subject, p['level'], child_age(c), c['lang'], mistakes, s.get('tutor_name') or 'Teacher Kiko',
                           use_ai=s.get('ai_enabled') == '1')
    usage = rnd.get('usage') or {}
    tin, tout = int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0)
    cost = tin * num(s.get('ai_price_in'), 1) / 1e6 + tout * num(s.get('ai_price_out'), 5) / 1e6
    rid = insert('ai_rounds', {'child_id': c['id'], 'subject': subject, 'level': p['level'], 'source': rnd['source'],
                            'items': json.dumps(rnd['items']), 'n_items': len(rnd['items']), 'n_correct': 0, 'seconds': 0,
                            'finished': 0, 'in_tokens': tin, 'out_tokens': tout, 'cost_usd': cost, 'day': today().isoformat(), 'created_at': now()})
    subj = tutor.SUBJ[subject]
    return jsonify({'ok': True, 'round_id': rid, 'intro': rnd['intro'], 'items': rnd['items'], 'lang': subj['lang'],
                    'level': p['level'], 'level_name': tutor.LEVELS[p['level'] - 1], 'subject': subj})

@app.post('/api/learn/finish')
@require('parent')
def learn_finish():
    b = body()
    r = q("SELECT * FROM ai_rounds WHERE id=?", [b.get('round_id')], one=True)
    if not r or not own_child(r['child_id']):
        return err('Round not found', 404)
    if r['finished']:
        return err('Round already saved')
    items = json.loads(r['items'])
    chosen = b.get('answers') or []
    correct = 0
    for i, it in enumerate(items):
        pick = chosen[i] if i < len(chosen) else None
        first_try = pick[0] if isinstance(pick, list) and pick else pick
        if first_try == it['answer']:
            correct += 1
        elif first_try is not None:
            try:
                given = it['choices'][int(first_try)]
            except (ValueError, IndexError, TypeError):
                given = ''
            insert('ai_mistakes', {'child_id': r['child_id'], 'subject': r['subject'], 'prompt': (it['say'] or it['text'])[:200],
                                'given': given, 'correct_answer': it['choices'][it['answer']], 'skill': it.get('skill', ''), 'created_at': now()})
    secs = max(0, min(int(num(b.get('seconds'), 0)), 900))
    q("UPDATE ai_rounds SET n_correct=?, seconds=?, finished=1 WHERE id=?", [correct, secs, r['id']])
    c = q("SELECT * FROM ai_children WHERE id=?", [r['child_id']], one=True)
    p = get_progress(r['child_id'], r['subject'], int(c['grade']))
    acc = correct / max(1, len(items))
    level, streak, change = int(p['level']), int(p['good_streak']), 0
    s = tutor.SUBJ[r['subject']]
    if acc >= 0.8:
        streak += 1
        if streak >= 2 and level < s['max']:
            level, streak, change = level + 1, 0, 1
    elif acc <= 0.4:
        streak = 0
        if level > s['min']:
            level, change = level - 1, -1
    else:
        streak = 0
    q("""UPDATE ai_progress SET level=?, good_streak=?, rounds=rounds+1, correct=correct+?, total=total+?, stars=stars+?, updated_at=?
         WHERE id=?""", [level, streak, correct, len(items), correct, now(), p['id']])
    st = settings()
    used, n = today_usage(c['id'])
    can_more = used < int(num(st.get('daily_minutes'), 30)) * 60 and n < int(num(st.get('max_rounds_day'), 12))
    return jsonify({'ok': True, 'correct': correct, 'total': len(items), 'stars': correct, 'level': level,
                    'level_name': tutor.LEVELS[level - 1], 'level_change': change, 'can_continue': can_more,
                    'minutes_left': max(0, int(num(st.get('daily_minutes'), 30)) - round(used / 60))})

# ---------------------------------------------------------------- admin API
def month_range(m):
    return m + '%'

def ai_cost_php(where_sql='', params=()):
    s = settings()
    r = q("SELECT COALESCE(SUM(cost_usd),0) AS c FROM ai_rounds " + where_sql, params, one=True)
    return num(r['c']) * num(s.get('usd_php'), 58)

@app.get('/api/admin/dashboard')
@require('admin')
def admin_dashboard():
    t = today().isoformat()
    m = t[:7]
    parents = q("SELECT * FROM ai_parents")
    states = [sub_status(p)['state'] for p in parents]
    return jsonify({'ok': True,
        'to_verify': q("SELECT COUNT(*) AS n FROM ai_payments WHERE status='for_verification'", one=True)['n'],
        'active': states.count('active'), 'trial': states.count('trial'), 'expired': states.count('expired'),
        'parents': len(parents),
        'learners': q("SELECT COUNT(*) AS n FROM ai_children WHERE active=1", one=True)['n'],
        'revenue_month': num(q("SELECT COALESCE(SUM(amount),0) AS n FROM ai_payments WHERE status='paid' AND verified_at LIKE ?", [m + '%'], one=True)['n']),
        'ai_cost_month': ai_cost_php("WHERE day LIKE ?", [m + '%']),
        'rounds_today': q("SELECT COUNT(*) AS n FROM ai_rounds WHERE day=?", [t], one=True)['n'],
        'learners_today': q("SELECT COUNT(DISTINCT child_id) AS n FROM ai_rounds WHERE day=?", [t], one=True)['n'],
        'ai_on': settings().get('ai_enabled') == '1', 'ai_key': bool(os.environ.get('ANTHROPIC_API_KEY')),
        'expiring': [dict(public_parent(p), until=p['sub_until']) for p in parents
                     if p['sub_until'] and t <= p['sub_until'] <= (today() + dt.timedelta(days=5)).isoformat()],
        'recent': q("""SELECT p.id, p.code, p.plan_name, p.amount, p.method, p.status, p.created_at, pa.name AS parent_name, pa.phone
                       FROM ai_payments p LEFT JOIN ai_parents pa ON pa.id=p.parent_id ORDER BY p.id DESC LIMIT 8""")})

@app.get('/api/admin/payments')
@require('admin')
def admin_payments():
    f = request.args.get('filter', '')
    sql = """SELECT p.id, p.code, p.plan_name, p.days, p.amount, p.method, p.ref, p.status, p.created_at, p.verified_at,
             p.parent_id, pa.name AS parent_name, pa.phone FROM ai_payments p LEFT JOIN ai_parents pa ON pa.id=p.parent_id"""
    par = []
    if f in ('for_verification', 'paid', 'rejected'):
        sql += " WHERE p.status=?"
        par.append(f)
    sql += " ORDER BY p.id DESC LIMIT 300"
    return jsonify({'ok': True, 'payments': q(sql, par)})

@app.get('/api/admin/payments/<int:pid>')
@require('admin')
def admin_payment(pid):
    p = q("SELECT * FROM ai_payments WHERE id=?", [pid], one=True)
    if not p:
        return err('Not found', 404)
    pa = q("SELECT * FROM ai_parents WHERE id=?", [p['parent_id']], one=True)
    return jsonify({'ok': True, 'payment': p, 'parent': public_parent(pa) if pa else None})

def extend(parent_id, days):
    pa = q("SELECT * FROM ai_parents WHERE id=?", [parent_id], one=True)
    base = today()
    if pa['sub_until'] and dt.date.fromisoformat(pa['sub_until']) >= base:
        base = dt.date.fromisoformat(pa['sub_until']) + dt.timedelta(days=1)
    until = (base + dt.timedelta(days=int(days) - 1)).isoformat()
    q("UPDATE ai_parents SET sub_until=? WHERE id=?", [until, parent_id])
    return until

@app.post('/api/admin/payments/<int:pid>')
@require('admin')
def admin_payment_action(pid):
    p = q("SELECT * FROM ai_payments WHERE id=?", [pid], one=True)
    if not p:
        return err('Not found', 404)
    b = body()
    act = b.get('action')
    if act == 'approve':
        if p['status'] == 'paid':
            return err('Already approved')
        until = extend(p['parent_id'], p['days'])
        q("UPDATE ai_payments SET status='paid', verified_at=?, notes=? WHERE id=?", [now(), clean(b.get('notes'), 300), pid])
        return jsonify({'ok': True, 'until': until})
    if act == 'reject':
        if p['status'] == 'paid':
            return err('This payment is already approved. Adjust the subscription date in Parents instead.')
        q("UPDATE ai_payments SET status='rejected', verified_at=?, notes=? WHERE id=?", [now(), clean(b.get('notes'), 300), pid])
        return jsonify({'ok': True})
    return err('Unknown action')

@app.get('/api/admin/parents')
@require('admin')
def admin_parents():
    term = clean(request.args.get('q'), 60).lower()
    rows = q("""SELECT p.*, (SELECT COUNT(*) FROM ai_children c WHERE c.parent_id=p.id AND c.active=1) AS kids,
               (SELECT COALESCE(SUM(amount),0) FROM ai_payments x WHERE x.parent_id=p.id AND x.status='paid') AS paid_total
               FROM ai_parents p ORDER BY p.id DESC""")
    out = []
    f = request.args.get('filter', '')
    for r in rows:
        if term and term not in (r['name'] + ' ' + r['phone'] + ' ' + (r['email'] or '')).lower():
            continue
        st = sub_status(r)
        if f and st['state'] != f:
            continue
        r.pop('password', None)
        r['subscription'] = st
        out.append(r)
    return jsonify({'ok': True, 'parents': out})

@app.get('/api/admin/parents/<int:pid>')
@require('admin')
def admin_parent(pid):
    p = q("SELECT * FROM ai_parents WHERE id=?", [pid], one=True)
    if not p:
        return err('Not found', 404)
    kids = q("SELECT * FROM ai_children WHERE parent_id=? ORDER BY active DESC, id", [pid])
    pays = q("SELECT id, code, plan_name, amount, method, ref, status, created_at, verified_at FROM ai_payments WHERE parent_id=? ORDER BY id DESC", [pid])
    d = dict(p)
    d.pop('password', None)
    d['subscription'] = sub_status(p)
    return jsonify({'ok': True, 'parent': d, 'children': [dict(child_summary(c), active=c['active']) for c in kids], 'payments': pays})

@app.post('/api/admin/parents/<int:pid>')
@require('admin')
def admin_parent_update(pid):
    p = q("SELECT * FROM ai_parents WHERE id=?", [pid], one=True)
    if not p:
        return err('Not found', 404)
    b = body()
    if b.get('add_days'):
        until = extend(pid, int(num(b['add_days'], 0)))
        return jsonify({'ok': True, 'until': until})
    data = {}
    if 'sub_until' in b:
        v = clean(b['sub_until'], 10) or None
        if v:
            try:
                dt.date.fromisoformat(v)
            except ValueError:
                return err('Invalid date')
        data['sub_until'] = v
    if b.get('status') in ('active', 'blocked'):
        data['status'] = b['status']
    if 'admin_notes' in b:
        data['admin_notes'] = clean(b['admin_notes'], 1000)
    if b.get('new_password'):
        if len(str(b['new_password'])) < 6:
            return err('Password must be at least 6 characters')
        data['password'] = generate_password_hash(str(b['new_password']))
    for k in ('name', 'phone', 'email'):
        if k in b and clean(b[k]):
            data[k] = clean(b[k], 120)
    update('ai_parents', pid, data, {'sub_until', 'status', 'admin_notes', 'password', 'name', 'phone', 'email'})
    return jsonify({'ok': True})

@app.get('/api/admin/learners')
@require('admin')
def admin_learners():
    rows = q("""SELECT c.*, p.name AS parent_name, p.phone,
        (SELECT COUNT(*) FROM ai_rounds r WHERE r.child_id=c.id AND r.finished=1) AS rounds,
        (SELECT COALESCE(SUM(seconds),0) FROM ai_rounds r WHERE r.child_id=c.id) AS secs,
        (SELECT MAX(day) FROM ai_rounds r WHERE r.child_id=c.id) AS last_day,
        (SELECT COALESCE(SUM(stars),0) FROM ai_progress g WHERE g.child_id=c.id) AS stars
        FROM ai_children c LEFT JOIN ai_parents p ON p.id=c.parent_id WHERE c.active=1 ORDER BY c.name""")
    for r in rows:
        r['grade_name'] = tutor.LEVELS[int(r['grade']) - 1]
        r['age'] = child_age(r)
    return jsonify({'ok': True, 'learners': rows})

@app.get('/api/admin/plans')
@require('admin')
def admin_plans():
    return jsonify({'ok': True, 'plans': q("SELECT * FROM ai_plans ORDER BY sort, id")})

@app.post('/api/admin/plans')
@app.post('/api/admin/plans/<int:pid>')
@require('admin')
def admin_plan_save(pid=None):
    b = body()
    days = int(num(b.get('days'), 0))
    if not clean(b.get('name')) or days < 1:
        return err('Name and number of days are required')
    data = {'name': clean(b.get('name'), 80), 'days': days, 'price': num(b.get('price')), 'description': clean(b.get('description'), 200),
            'sort': int(num(b.get('sort'), 0)), 'active': 1 if b.get('active', 1) in (1, '1', True) else 0}
    if pid:
        update('ai_plans', pid, data, data.keys())
    else:
        pid = insert('ai_plans', data)
    return jsonify({'ok': True, 'id': pid})

@app.get('/api/admin/settings')
@require('admin')
def admin_settings():
    return jsonify({'ok': True, 'settings': settings(), 'ai_key': bool(os.environ.get('ANTHROPIC_API_KEY')),
                    'model': os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5-20251001')})

@app.post('/api/admin/settings')
@require('admin')
def admin_settings_save():
    for k, v in body().items():
        if k not in DEFAULT_SETTINGS:
            continue
        v = clean(v, 1000)
        if q("SELECT k FROM ai_settings WHERE k=?", [k], one=True):
            q("UPDATE ai_settings SET v=? WHERE k=?", [v, k])
        else:
            q("INSERT INTO ai_settings (k, v) VALUES (?, ?)", [k, v])
    return jsonify({'ok': True})

@app.post('/api/admin/test-ai')
@require('admin')
def admin_test_ai():
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return err('ANTHROPIC_API_KEY is not set on Render yet')
    try:
        rnd = tutor.ai_round('math', 2, 5, 'both', [], settings().get('tutor_name') or 'Teacher Kiko', n=3)
        return jsonify({'ok': True, 'sample': rnd['items'][0], 'usage': rnd.get('usage')})
    except Exception as e:
        return err('AI test failed: ' + repr(e)[:300])

@app.get('/api/admin/expenses')
@require('admin')
def admin_expenses():
    m = request.args.get('month') or today().isoformat()[:7]
    return jsonify({'ok': True, 'expenses': q("SELECT * FROM ai_expenses WHERE date LIKE ? ORDER BY date DESC, id DESC", [m + '%'])})

@app.post('/api/admin/expenses')
@require('admin')
def admin_expense_add():
    b = body()
    if num(b.get('amount')) <= 0 or not b.get('date'):
        return err('Date and amount are required')
    eid = insert('ai_expenses', {'date': clean(b.get('date'), 10), 'category': clean(b.get('category'), 60) or 'Other',
                              'amount': num(b.get('amount')), 'note': clean(b.get('note')), 'created_at': now()})
    return jsonify({'ok': True, 'id': eid})

@app.post('/api/admin/expenses/<int:eid>/delete')
@require('admin')
def admin_expense_delete(eid):
    q("DELETE FROM ai_expenses WHERE id=?", [eid])
    return jsonify({'ok': True})

@app.get('/api/admin/report')
@require('admin')
def admin_report():
    m = request.args.get('month') or today().isoformat()[:7]
    s = settings()
    rate = num(s.get('usd_php'), 58)
    by_plan = q("""SELECT plan_name, COUNT(*) AS n, COALESCE(SUM(amount),0) AS amount FROM ai_payments
                   WHERE status='paid' AND verified_at LIKE ? GROUP BY plan_name ORDER BY plan_name""", [m + '%'])
    revenue = sum(num(r['amount']) for r in by_plan)
    ai = q("""SELECT COUNT(*) AS rounds, COALESCE(SUM(CASE WHEN source='ai' THEN 1 ELSE 0 END),0) AS ai_rounds,
              COALESCE(SUM(in_tokens),0) AS tin, COALESCE(SUM(out_tokens),0) AS tout, COALESCE(SUM(cost_usd),0) AS usd,
              COALESCE(SUM(seconds),0) AS secs, COUNT(DISTINCT child_id) AS kids FROM ai_rounds WHERE day LIKE ?""", [m + '%'], one=True)
    ai_php = num(ai['usd']) * rate
    exp = q("SELECT category, COALESCE(SUM(amount),0) AS amount FROM ai_expenses WHERE date LIKE ? GROUP BY category ORDER BY category", [m + '%'])
    exp_total = sum(num(r['amount']) for r in exp)
    subj = q("""SELECT subject, COUNT(*) AS rounds, COALESCE(SUM(n_correct),0) AS correct, COALESCE(SUM(n_items),0) AS total
                FROM ai_rounds WHERE finished=1 AND day LIKE ? GROUP BY subject ORDER BY rounds DESC""", [m + '%'])
    for x in subj:
        x['name'] = tutor.SUBJ.get(x['subject'], {}).get('name', x['subject'])
    trend = []
    y, mo = int(m[:4]), int(m[5:7])
    for i in range(5, -1, -1):
        yy, mm = y, mo - i
        while mm < 1:
            mm += 12
            yy -= 1
        key = f'{yy:04d}-{mm:02d}'
        rev = num(q("SELECT COALESCE(SUM(amount),0) AS n FROM ai_payments WHERE status='paid' AND verified_at LIKE ?", [key + '%'], one=True)['n'])
        trend.append({'month': key, 'revenue': rev, 'ai_cost': ai_cost_php("WHERE day LIKE ?", [key + '%'])})
    new_parents = q("SELECT COUNT(*) AS n FROM ai_parents WHERE created_at LIKE ?", [m + '%'], one=True)['n']
    return jsonify({'ok': True, 'month': m, 'revenue': revenue, 'by_plan': by_plan, 'ai_cost': ai_php,
                    'ai': {k: num(ai[k]) for k in ai}, 'expenses': exp, 'expense_total': exp_total,
                    'net': revenue - ai_php - exp_total, 'subjects': subj, 'trend': trend, 'new_parents': new_parents,
                    'cost_per_kid': ai_php / num(ai['kids']) if num(ai['kids']) else 0})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
