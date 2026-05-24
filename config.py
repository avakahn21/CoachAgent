import os
from dotenv import load_dotenv

# Always load from the .env next to this file, regardless of working directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# Twilio
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_NUMBER = os.environ["TWILIO_WHATSAPP_NUMBER"]

# Anthropic
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = "claude-sonnet-4-5"

# Supabase
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
SPREADSHEET_ID = "1rtq6B4vO3Yf30KnzgmtX-OKxpSysIJCWZqyKeAIqCt0"

# Plaid
PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID", "")
PLAID_SECRET = os.environ.get("PLAID_SECRET", "")
PLAID_ENV = os.environ.get("PLAID_ENV", "sandbox")

# App base URL — used to build the Plaid Link URL sent over WhatsApp
# Local: your ngrok URL (e.g. https://abc123.ngrok.io)
# Production: your Railway URL (e.g. https://your-app.railway.app)
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

# App
TIMEZONE = "America/Los_Angeles"
LMU_START_DATE = "2026-08-17"

# Budget targets
BUDGET_TARGETS = {
    "groceries": 400,
    "dining": 400,
    "shopping": 300,
    "personal_care": 300,
    "gas": 160,
    "uber_lyft": 200,
    "entertainment": 100,
    "misc": 150,
    "gym": 170,
    "subscriptions": 166,
}

MONTHLY_NET = 2488

# Current spending averages (from gap analysis)
CURRENT_AVERAGES = {
    "shopping": 1857,
    "dining": 1136,
    "personal_care": 602,
    "gym": 500,
    "groceries": 489,
    "uber_lyft": 400,
}

# Sheet tab names — must match spreadsheet exactly
SHEET_CHECKLIST_TAB = "Pre-Law Checklist"
SHEET_MONTHLY_BUDGET_TAB = "Monthly Budget"
SHEET_LOAN_SUMMARY_TAB = "Loan Summary"
SHEET_ANNUAL_ROLLUP_TAB = "Annual Rollup"
SHEET_SPENDING_CUTS_TAB = "Spending Cuts Guide"
SHEET_SCHOOL_COMPARISON_TAB = "School Comparison"
