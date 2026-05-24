# Life Coach + Financial Accountability Agent

A proactive WhatsApp agent operating across three modes in one conversation:
- **Financial accountability** — tracks spending, fires budget alerts, coaches behavior change
- **Pre-law checklist** — manages tasks and deadlines before law school
- **Law school life coach** — guides through 1L at LMU with weekly check-ins and milestone alerts

**Stack:** Flask · Twilio WhatsApp · Anthropic Claude (claude-sonnet-4-5) · Supabase · Google Sheets API · APScheduler · Railway

---

## 1. Prerequisites

- Python 3.11+
- A [Twilio account](https://twilio.com) with WhatsApp sandbox or approved sender
- An [Anthropic API key](https://console.anthropic.com)
- A [Supabase project](https://supabase.com)
- A Google Cloud service account with Sheets API enabled
- A [Railway account](https://railway.app) for hosting

---

## 2. Local Setup

```bash
git clone <your-repo>
cd life-coach-agent

# Python 3.11 is required. On macOS it's at /opt/homebrew/bin/python3.11
# If missing: brew install python@3.11
/opt/homebrew/bin/python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in all values in .env (see sections below)
python app.py
```

---

## 3. Google Sheets API — Service Account Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → Create or select a project.
2. Enable **Google Sheets API** and **Google Drive API**.
3. Go to **IAM & Admin → Service Accounts** → Create a service account.
4. Generate a JSON key: Service Account → Keys → Add Key → JSON. Download it.
5. Open the JSON file. Paste its entire contents as a single line into `GOOGLE_SHEETS_CREDENTIALS` in your `.env`.
6. **Share the spreadsheet** with the service account email (it looks like `name@project.iam.gserviceaccount.com`) — grant **Editor** access.
7. The spreadsheet ID is already baked in: `1rtq6B4vO3Yf30KnzgmtX-OKxpSysIJCWZqyKeAIqCt0`

### Required sheet tabs and column structure

**Pre-law Checklist tab** — columns (in order):
| Task Name | Deadline (YYYY-MM-DD) | Priority | Category | Completed | Notes |

**Gap Analysis tab** — columns:
| Category | Current Average | 1L Target | Gap |

**Monthly Budget tab** — columns:
| Category | Budget | Actual |

---

## 4. Supabase Setup

1. Create a new Supabase project.
2. Copy the **Project URL** → `SUPABASE_URL`
3. Copy the **service_role** key (Settings → API) → `SUPABASE_KEY` (use service role, not anon, for server-side access)
4. Go to **SQL Editor** and run the contents of `supabase_schema.sql` to create all tables.

---

## 5. Twilio WhatsApp Setup

### Sandbox (local testing)
1. Twilio Console → Messaging → Try it out → Send a WhatsApp message
2. Join the sandbox by texting the join code to the Twilio sandbox number
3. Set the sandbox webhook URL to your ngrok URL (see below)

### Local testing with ngrok
```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8080
# Copy the https URL, e.g. https://abc123.ngrok.io
```
In Twilio Console → WhatsApp Sandbox → "When a message comes in":
```
https://abc123.ngrok.io/webhook
```
Method: `HTTP POST`

### Production (approved WhatsApp sender)
Once deployed to Railway, use your Railway URL:
```
https://your-app.railway.app/webhook
```

---

## 6. Deploying to Railway

1. Push your code to a GitHub repo.
2. Go to [Railway](https://railway.app) → New Project → Deploy from GitHub repo.
3. Add all environment variables from `.env.example` in Railway's **Variables** tab.
4. Railway auto-detects `Procfile` and `railway.toml` — no extra config needed.
5. Once deployed, copy the Railway URL and set it as your Twilio webhook.

**Important:** Railway free tier sleeps after inactivity. Use the **Hobby plan** ($5/mo) or set up an uptime monitor to keep the instance alive, so scheduled messages fire reliably.

---

## 7. WhatsApp Command Reference

### Financial commands
```
I spent $45 at Nobu           → Logs $45 dining, shows running total + alert if near budget
I paid $120 at Zara           → Logs $120 shopping
I bought groceries for $67    → Logs $67 groceries
How am I doing on budget?     → Full category breakdown
What's my dining total?       → Single category check
```

### Checklist commands
```
Mark [task name] complete
Mark LSAT score report complete

Push [task] deadline to [date], [reason]
Push financial aid application deadline to June 15, waiting on document

Add task: [description] due [date], priority [urgent/soon/normal], category [financial/admin/academic/lifestyle]
Add task: Schedule orientation appointment due May 30, priority urgent, category admin

Remove [task name]
Remove old LSAT prep task

What's still due this month?
What have I completed so far?
```

### Law school context updates (Layer 2)
These commands update your semester context — no code changes ever needed:
```
Update my classes: Contracts, Torts, Civil Procedure, Legal Writing, Property
My professor for Contracts is Professor Smith
Add deadline: Contracts outline due November 15
Exam schedule: Contracts Dec 10, Torts Dec 12, CivPro Dec 14
New semester: Spring 2027, new class list follows
```

### General coaching
```
I'm overwhelmed           → Warmth first, then practical steps
How do I brief a case?    → 1L academic coaching
What should I focus on this week?   → Surfaces top checklist + spending check
```

---

## 8. Proactive Message Schedule

| Time | Message |
|------|---------|
| Every Monday 9am PT | Top 3 checklist items with action steps |
| Every Sunday 6pm PT | Weekly spending breakdown + coaching challenge |
| Every Sunday 7pm PT | Law school week-ahead check-in (after Aug 17, 2026) |
| Every Wednesday 12pm PT | Midweek balance check — gym, cooking, sleep (after Aug 17, 2026) |
| 1st of each month 8am PT | Full monthly financial review + savings runway |
| When category hits 75% before the 20th | Immediate threshold alert |
| Daily 7am PT | Check for overdue tasks |

---

## 9. Adding Plaid (v2)

When you're ready to connect live bank/credit card transactions:

1. Sign up at [Plaid](https://plaid.com) → get `PLAID_CLIENT_ID` and `PLAID_SECRET`
2. Add both to your Railway environment variables
3. Set `PLAID_ENV=production` (or `sandbox` for testing)
4. The agent will auto-detect Plaid credentials and enable live transaction pulling
5. Text "connect Plaid" to Ava's number — she'll walk you through the Link flow

In v1 (default), all spending is logged via manual text input — just text "I spent $X at Y" and it's logged automatically.

---

## 10. Law School Semester Updates

At the start of each new semester, text these commands in any order:

```
New semester: Fall 2027
Update my classes: Evidence, Criminal Law, Administrative Law, Moot Court
My professor for Evidence is Professor Jones
My professor for Criminal Law is Professor Williams
Exam schedule: Evidence Dec 8, Criminal Law Dec 10, Admin Law Dec 13
Add deadline: Evidence outline due November 20
Add deadline: Moot Court brief due November 1
```

The agent stores everything in Supabase and uses it to make all coaching specific to your actual classes, professors, and deadlines. No code changes required — ever.

---

## 11. Architecture Overview

```
WhatsApp → Twilio → /webhook (Flask)
                        ↓
              message_router.py
             /        |         \
    financial.py  checklist.py  life_coach.py
          \           |           /
           claude_agent.py (Claude API)
                    ↓
              database.py (Supabase)
              sheets.py (Google Sheets)

APScheduler (background thread)
  → fires proactive messages on schedule
  → calls same financial/checklist/life_coach modules
  → sends via Twilio directly
```
