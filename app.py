import os
import sqlite3
import time
from datetime import date, timedelta
import pandas as pd
import streamlit as st
from pydantic import BaseModel, Field
from typing import List, Optional
from PIL import Image
from pypdf import PdfReader
from google import genai
from google.genai import types

st.set_page_config(page_title="MILO", page_icon="📌", layout="wide")

# Safe CSS block using string concatenation (immune to unterminated triple-quote errors)
css_styles = (
    "<style>"
    ".stApp { background: #0b0f17; color: #f1f5f9; }"
    ".block-container { padding-top: 1.8rem; max-width: 1200px; }"
    "[data-testid='stChatInput'] { background: #fceee8 !important; border-radius: 32px !important; padding: 6px 14px !important; border: none !important; box-shadow: none !important; max-width: 820px; margin: 0 auto 1rem auto; }"
    "[data-testid='stChatInput']>div { background: transparent !important; border: none !important; box-shadow: none !important; }"
    "[data-testid='stChatInput'] textarea { color: #18181b !important; font-size: 1rem !important; }"
    "[data-testid='stChatInput'] textarea::placeholder { color: #6e655f !important; opacity: 1 !important; }"
    "[data-testid='stChatInput'] button { color: #18181b !important; }"
    ".reply-txt { text-align: center; color: #e2e8f0; font-size: 0.95rem; margin: 0 auto 1rem auto; max-width: 820px; }"
    ".sticky { position: relative; background: #fbf7dc; color: #27272a; padding: 26px 18px 18px; border-radius: 4px; box-shadow: 0 12px 24px rgba(0,0,0,0.5); transform: rotate(-1.5deg); height: 340px; box-sizing: border-box; font-family: cursive, sans-serif; overflow: hidden; }"
    ".pin { position: absolute; top: -11px; left: 50%; transform: translateX(-50%); width: 22px; height: 22px; background: radial-gradient(circle at 35% 35%, #ff4b4b, #8a0b0b); border-radius: 50%; box-shadow: 0 3px 6px rgba(0,0,0,0.5); }"
    ".stitle { font-weight: 700; border-bottom: 2px dashed #d1c79f; padding-bottom: 6px; margin-bottom: 10px; text-align: center; }"
    ".sitem { font-size: 0.86rem; margin-bottom: 8px; padding-bottom: 5px; border-bottom: 1px dotted #e2d9b5; }"
    ".slate-box { border: 2.5px solid #ffffff; border-radius: 28px; padding: 18px; height: 340px; background: #0b0f17; box-sizing: border-box; }"
    "div[data-baseweb='select']>div { background: #1e293b !important; color: #f8fafc !important; border-radius: 10px !important; border: 1px solid #334155 !important; font-weight: 600; }"
    ".scrollable-disc { height: 230px; max-height: 230px; overflow-y: auto; padding-right: 6px; margin-top: 6px; }"
    ".scrollable-disc::-webkit-scrollbar { width: 5px; }"
    ".scrollable-disc::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; }"
    "@media (max-width: 640px) { .sticky { transform: rotate(-0.5deg) !important; } }"
    "</style>"
)
st.markdown(css_styles, unsafe_allow_html=True)

KEY = st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
if not KEY:
    st.error("Missing GEMINI_API_KEY in Secrets.")
    st.stop()

ai_client = genai.Client(api_key=KEY)
DB_FILE = "milo_study.db"

with sqlite3.connect(DB_FILE) as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, topic TEXT, module TEXT, logged_date TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS dues(id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, task TEXT, due_date TEXT, status TEXT DEFAULT 'Pending')")

class ExtractedTopic(BaseModel):
    subject: str = Field(default="General")
    topic: str = Field(default="")
    module: str = Field(default="General")
    logged_date: str = Field(default="")

class ExtractedDue(BaseModel):
    subject: str = Field(default="General")
    task: str = Field(default="")
    due_date: str = Field(default="TBD")

class MiloPayload(BaseModel):
    has_academic_content: bool = False
    assistant_reply: str = Field(default="")
    topics: List[ExtractedTopic] = Field(default_factory=list)
    dues: List[ExtractedDue] = Field(default_factory=list)

def parse_with_milo(content_items: list):
    today_iso = date.today().isoformat()
    prompt_lines = [
        f"Current Date: {today_iso}",
        "You are MILO, an academic tracker for a college student.",
        "Extract topics taught and upcoming dues from raw notes or files.",
        "Use 'General' if module is unknown.",
        f"Check notes for dates (e.g., 'yesterday', 'Oct 15'). Resolve into YYYY-MM-DD for 'logged_date'. If no date is mentioned, default logged_date to today ({today_iso}).",
        "Convert relative due dates to YYYY-MM-DD. Use 'TBD' if unknown.",
        "If input is casual chat, set has_academic_content=False and write a friendly response in assistant_reply."
    ]
    sys_prompt = "\n".join(prompt_lines)
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    for m in models:
        for attempt in range(2):
            try:
                response = ai_client.models.generate_content(
                    model=m,
                    contents=[sys_prompt] + content_items,
                    config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=MiloPayload)
                )
                if response
