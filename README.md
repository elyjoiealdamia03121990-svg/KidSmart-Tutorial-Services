# KidSmart AI Tutor

A friendly AI tutor for Filipino kids from **Preschool to Grade 4**. The tutor ("Teacher Kiko") speaks every question out loud,
shows big pictures, and the child just taps the answer. Lessons adapt to each child's level, in English and Filipino.

- **Website:** `/` - info, subjects, pricing, sign-up / log in
- **Parent app:** `/app` - add children, start learning (kid mode), progress reports, subscribe and pay
- **Back office:** `/admin` - payments, parents, learners, plans, expenses, reports, settings

## Subjects (6 levels: Preschool, Kinder, Grade 1-4)
Letters & Phonics · English Reading · Math · Shapes & Colors · Filipino · Science

## How the AI works
- Each lesson is a "round" of 5 questions. The server asks **Claude Haiku 4.5** to make a round that fits the child's
  subject, level and recent mistakes. The answer is checked on the server.
- No AI key, AI switched off, or AI error? The app automatically uses the **built-in lessons** (hundreds of questions),
  so kids can always keep learning.
- Child safety: no open chat, the AI only makes lesson questions, it never asks for personal info, and the child's
  name is never sent to the AI (it is added on the device).
- Voice uses the phone/tablet's own text-to-speech (free). Best on Chrome (Android) or Safari (iPhone/iPad).
- Rough AI cost: about ₱0.25 per round, so a child who uses the full daily limit costs about ₱3 a day.
  The **Reports** page shows the real estimated AI cost every month.

## Files (all in the repo root)
| File | What it is |
|---|---|
| `app.py` | Server, database, all APIs |
| `tutor.py` | Lesson engine: AI rounds + built-in lessons |
| `requirements.txt` | Python packages |
| `index.html` | Public website + sign-up/log-in |
| `app.html` | Parent dashboard + kid learning mode |
| `admin.html` | Back office |

## Deploy on Render

1. **Database:** create a PostgreSQL database (Render PostgreSQL, or Neon free) and copy its connection URL.
   (Render's free web service erases local files on every deploy, so a database is required.)
2. **Web Service:** New > Web Service > this repo.
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --timeout 60`
3. **Environment variables:**

| Key | Value |
|---|---|
| `DATABASE_URL` | the database URL |
| `ADMIN_PASSWORD` | your own strong back office password |
| `SECRET_KEY` | any long random text |
| `ANTHROPIC_API_KEY` | your Claude API key from console.anthropic.com (optional at first) |

4. Wait for **Live**. Open `/admin`, go to **Settings**: add GCash / Maya / bank details, check prices in **Plans**,
   then press **Test AI now**.

## Getting the Claude API key
1. Go to console.anthropic.com and create an account.
2. Billing: add a payment card and buy a small amount of credit (you can set a monthly spend limit there).
3. API Keys > Create Key. Copy it (it is shown only once).
4. On Render > your web service > Environment > add `ANTHROPIC_API_KEY` with that key > Save. Render redeploys.

## Daily flow
1. Parent signs up (free trial starts), adds children, kids learn.
2. Parent picks a plan, pays by GCash/Maya/bank, uploads the receipt.
3. Back office **Payments**: check the receipt, **Approve** - the subscription is extended automatically.
4. **Reports**: revenue, AI cost, expenses, net profit, minutes learned, results per subject.

## Default settings (change in Settings / Plans)
- Free trial 3 days · 30 minutes and 12 rounds per child per day · up to 3 children per account
- Monthly ₱299 · 3 Months ₱799 · 6 Months ₱1,499
