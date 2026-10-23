import os
import json
import re
import time
import html
import sqlite3
from datetime import date, timedelta
import pandas as pd
import streamlit as st
from PIL import Image
from pypdf import PdfReader
from google import genai
from google.genai import types

app_icon = "📌"
if os.path.exists("milo_icon.png"):
    try:
        app_icon = Image.open("milo_icon.png")
    except Exception:
        app_icon = "📌"

st.set_page_config(
    page_title="MILO",
    page_icon=app_icon,
    layout="wide",
)

css_rules = [
    "<style>",
    ".stApp { background: #0b0f17; color: #f1f5f9; }",
    ".block-container { padding-top: 1.8rem; max-width: 1200px; }",
    "[data-testid='stChatInput'] {",
    "  background: #fceee8 !important;",
    "  border-radius: 32px !important;",
    "  padding: 6px 14px !important;",
    "  border: none !important;",
    "  box-shadow: none !important;",
    "  max-width: 820px; margin: 0 auto 1rem auto;",
    "}",
    "[data-testid='stChatInput']>div {",
    "  background: transparent !important;",
    "  border: none !important;",
    "  box-shadow: none !important;",
    "}",
    "[data-testid='stChatInput'] textarea {",
    "  color: #18181b !important; font-size: 1rem !important;",
    "}",
    "[data-testid='stChatInput'] textarea::placeholder {",
    "  color: #6e655f !important; opacity: 1 !important;",
    "}",
    "[data-testid='stChatInput'] button {",
    "  color: #18181b !important;",
    "}",
    ".reply-box {",
    "  background: #1e293b; border: 1px solid #334155;",
    "  padding: 12px 18px; border-radius: 16px; color: #f8fafc;",
    "  font-size: 0.95rem; margin: 0 auto 1rem auto; max-width: 820px;",
    "}",
    ".sticky {",
    "  position: relative; background: #fbf7dc; color: #27272a;",
    "  padding: 26px 18px 18px; border-radius: 4px;",
    "  box-shadow: 0 12px 24px rgba(0,0,0,0.5);",
    "  transform: rotate(-1.5deg); height: 340px;",
    "  box-sizing: border-box; font-family: cursive, sans-serif;",
    "  overflow: visible; margin-top: 6px;",
    "}",
    ".sticky-list {",
    "  max-height: 255px; overflow-y: auto; padding-right: 4px;",
    "}",
    ".pin {",
    "  position: absolute; top: -11px; left: 50%;",
    "  transform: translateX(-50%); width: 22px; height: 22px;",
    "  background: radial-gradient(",
    "    circle at 35% 35%, #ff4b4b, #8a0b0b",
    "  );",
    "  border-radius: 50%;",
    "  box-shadow: 0 3px 6px rgba(0,0,0,0.5);",
    "  z-index: 10;",
    "}",
    ".stitle {",
    "  font-weight: 700; border-bottom: 2px dashed #d1c79f;",
    "  padding-bottom: 6px; margin-bottom: 10px;",
    "  text-align: center;",
    "}",
    ".sitem {",
    "  font-size: 0.86rem; margin-bottom: 8px;",
    "  padding-bottom: 5px; border-bottom: 1px dotted #e2d9b5;",
    "}",
    "[data-testid='stVerticalBlockBorderWrapper']:has(.slate-marker) {",
    "  border: 2.5px solid #ffffff !important;",
    "  border-radius: 28px !important;",
    "  padding: 18px !important;",
    "  height: 340px !important;",
    "  max-height: 340px !important;",
    "  background: #0b0f17 !important;",
    "  overflow: hidden !important;",
    "  box-sizing: border-box !important;",
    "  margin-top: 6px !important;",
    "}",
    "div[data-baseweb='select']>div {",
    "  background: #1e293b !important; color: #f8fafc !important;",
    "  border-radius: 10px !important;",
    "  border: 1px solid #334155 !important; font-weight: 600;",
    "}",
    ".scrollable-disc {",
    "  height: 235px; max-height: 235px; overflow-y: auto;",
    "  padding-right: 6px; margin-top: 6px;",
    "}",
    ".scrollable-disc::-webkit-scrollbar { width: 5px; }",
    ".scrollable-disc::-webkit-scrollbar-thumb {",
    "  background: #334155; border-radius: 10px;",
    "}",
    "@media (max-width: 640px) {",
    "  .sticky { transform: rotate(-0.5deg) !important; }",
    "}",
    "</style>",
]
st.markdown("\n".join(css_rules), unsafe_allow_html=True)

raw_key = None
try:
    raw_key = st.secrets.get("GEMINI_API_KEY")
except Exception:
    raw_key = None

if not raw_key:
    raw_key = os.getenv("GEMINI_API_KEY")

if not raw_key:
    st.error("Missing GEMINI_API_KEY in Secrets.")
    st.stop()

api_key = str(raw_key).strip().strip('"').strip("'")
ai_client = genai.Client(api_key=api_key)
DB_FILE = "milo_study.db"

with sqlite3.connect(DB_FILE) as conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS topics ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "subject TEXT, topic TEXT, module TEXT, logged_date TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dues ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "subject TEXT, task TEXT, due_date TEXT, "
        "status TEXT DEFAULT 'Pending')"
    )


def extract_json_dict(raw_text: str):
    cleaned = raw_text.strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def parse_with_milo(content_items: list):
    today_iso = date.today().isoformat()
    prompt_lines = [
        "Current Date: " + today_iso,
        "You are MILO, a fast, helpful AI study assistant and academic tracker for a college student.",
        "1. Answer any study questions or chat naturally. Put your response in 'assistant_reply'.",
        "2. If the user input contains class notes or assignments, extract them into 'topics' and 'dues'. Otherwise, leave them empty [].",
        "Return ONLY valid JSON matching this exact structure:",
        "{",
        '  "assistant_reply": "Your brief, friendly answer here",',
        '  "topics": [{"subject": "str", "topic": "str", "module": "General", "logged_date": "YYYY-MM-DD"}],',
        '  "dues": [{"subject": "str", "task": "str", "due_date": "YYYY-MM-DD"}]',
        "}",
        f"Default logged_date to {today_iso} if topics are found without dates. Use 'TBD' for due_date if unknown.",
    ]
    sys_prompt = "\n".join(prompt_lines)
    cfg = types.GenerateContentConfig(response_mime_type="application/json")

    models_to_try = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
        "gemini-2.5-pro"
    ]

    for m in models_to_try:
        for attempt in range(2):
            try:
                res = ai_client.models.generate_content(
                    model=m,
                    contents=[sys_prompt] + content_items,
                    config=cfg,
                )
                if res and res.text:
                    return extract_json_dict(res.text)
            except Exception as err:
                err_str = str(err)
                if "503" in err_str or "UNAVAILABLE" in err_str.upper():
                    time.sleep(1)
                    continue
                break

    st.error("Service is temporarily busy (503). Please try your request again in a few seconds.")
    return None
