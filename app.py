import os, sqlite3, time
from datetime import date, timedelta
import pandas as pd
import streamlit as st
from pydantic import BaseModel
from typing import List
from PIL import Image
from pypdf import PdfReader
from google import genai
from google.genai import types

st.set_page_config(page_title="MILO", page_icon="📌", layout="wide")

st.markdown(
    "<style>"
    ".stApp{background:#0b0f17;color:#f1f5f9}"
    ".block-container{padding-top:2rem;max-width:1200px}"
    "[data-testid='stChatInput']{background:#fceee8!important;border-radius:32px!important;padding:6px 14px!important;border:none!important;box-shadow:none!important;max-width:800px;margin:0 auto 1.2rem auto}"
    "[data-testid='stChatInput']>div{background:transparent!important;border:none!important;box-shadow:none!important}"
    "[data-testid='stChatInput'] textarea{color:#18181b!important;font-size:1rem!important}"
    "[data-testid='stChatInput'] textarea::placeholder{color:#6e655f!important;opacity:1!important}"
    "[data-testid='stChatInput'] button{color:#18181b!important}"
    ".reply-txt{text-align:center;color:#e2e8f0;font-size:0.95rem;margin:0 auto 1.2rem auto;max-width:800px}"
    ".sticky{position:relative;background:#fbf7dc;color:#27272a;padding:28px 18px 18px;border-radius:4px;"
    "box-shadow:0 12px 24px rgba(0,0,0,.5);transform:rotate(-1.5deg);min-height:280px;font-family:cursive,sans-serif}"
    ".pin{position:absolute;top:-11px;left:50%;transform:translateX(-50%);width:22px;height:22px;"
    "background:radial-gradient(circle at 35% 35%,#ff4b4b,#8a0b0b);border-radius:50%;box-shadow:0 3px 6px rgba(0,0,0,.5)}"
    ".stitle{font-weight:700;border-bottom:2px dashed #d1c79f;padding-bottom:6px;margin-bottom:10px;text-align:center}"
    ".sitem{font-size:.88rem;margin-bottom:8px;padding-bottom:5px;border-bottom:1px dotted #e2d9b5}"
    "[data-testid='stVerticalBlockBorderWrapper']{border:2.5px solid #ffffff!important;border-radius:28px!important;padding:20px!important;height:360px!important;max-height:360px!important;background:#0b0f17!important;overflow:hidden!important}"
    "div[data-baseweb='select']>div{background:#1e293b!important;color:#f8fafc!important;border-radius:10px!important;border:1px solid #334155!important;font-weight:600}"
    ".scrollable-disc{max-height:220px;overflow-y:auto;padding-right:6px;margin-top:8px}"
    ".scrollable-disc::-webkit-scrollbar{width:5px}"
    ".scrollable-disc::-webkit-scrollbar-thumb{background:#334155;border-radius:10px}"
    "</style>",
    unsafe_allow_html=True
)

KEY = st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
if not KEY:
    st.error("Missing GEMINI_API_KEY in Secrets.")
    st.stop()

ai_client = genai.Client(api_key=KEY)
conn = sqlite3.connect("milo_study.db")
conn.execute("CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY,subject TEXT,topic TEXT,module TEXT,logged_date TEXT)")
conn.execute("CREATE TABLE IF NOT EXISTS dues(id INTEGER PRIMARY KEY,subject TEXT,task TEXT,due_date TEXT,status TEXT)")

class ExtractedTopic(BaseModel):
    subject: str
    topic: str
    module: str
    logged_date: str

class ExtractedDue(BaseModel):
    subject: str
    task: str
    due_date: str

class MiloPayload(BaseModel):
    has_academic_content: bool
    assistant_reply: str
    topics: List[ExtractedTopic]
    dues: List[ExtractedDue]

def parse_with_milo(content_items: list):
    today_iso = date.today().isoformat()
    prompt_parts = [
        "Current Date: " + today_iso,
        "You are MILO, an academic tracker for a college student.",
        "Extract topics taught and upcoming dues from notes or files.",
        "Use 'General' if module is unknown.",
        "CRITICAL FOR DATES: Check notes for dates (e.g. 'yesterday', 'Oct 15'). Resolve into YYYY-MM-DD format for 'logged_date'. If no date is given, default to today (" + today_iso + ").",
        "Convert relative due dates to YYYY-MM-DD. Use 'TBD' if unknown.",
        "If input is casual chat, set has_academic_content=False and write a friendly response in assistant_reply."
    ]
    sys_prompt = "\n".join(prompt_parts)
    
    for attempt in range(3):
        try:
            response = ai_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[sys_prompt] + content_items,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MiloPayload
                )
            )
            if response.parsed:
                return response.parsed
            return MiloPayload.model_validate_json(response.text)
        except Exception as e:
            if "503" in str(e) and attempt < 2:
                time.sleep(2)
                continue
            st.error("MILO engine error: " + str(e))
            return None

# Clean MILO logo with no underline artifact between L and O
logo_svg = (
    "<svg width='180' height='60' viewBox='0 0 240 80' style='user-select:none;display:block;margin-bottom:1.2rem'>"
    "<text x='5' y='62' fill='#ffffff' font-family='Georgia,serif' font-size='64' letter-spacing='2'>MIL</text>"
    "<text x='150' y='62' fill='#ffffff' font-family='Georgia,serif' font-size='64'>O</text>"
    "<ellipse cx='178' cy='40' rx='38' ry='11' fill='none' stroke='#ffffff' stroke-width='1' transform='rotate(-22 178 40)'/>"
    "<ellipse cx='178' cy='40' rx='38' ry='8' fill='none' stroke='#ffffff' stroke-width='0.8' transform='rotate(-16 178 40)'/>"
    "<text x='137' y='54' fill='#ffffff' font-size='12'>✦</text>"
    "<text x='207' y='34' fill='#ffffff' font-size='12'>✦</text>"
    "</svg>"
)
st.markdown(logo_svg, unsafe_allow_html=True)

prompt = st.chat_input(
    "Ask MILO, paste lecture updates, or drop deadlines...",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "pdf", "txt"]
)

if "reply" in st.session_state and st.session_state["reply"]:
    st.markdown("<div class='reply-txt'>" + st.session_state["reply"] + "</div>", unsafe_allow_html=True)

if prompt:
    txt = getattr(prompt, "text", "") or ""
    files = getattr(prompt, "files", []) or []
    if txt.strip() or files:
        items = ["Today is " + date.today().isoformat() + ". Extract topics and dues. Set has_academic_content=False if casual chat."]
        if txt.strip():
            items.append(txt)
        for f in files:
            if "image" in f.type:
                items.append(Image.open(f))
            elif "pdf" in f.type:
                items.append("\n".join([p.extract_text() or "" for p in PdfReader(f).pages[:8]])[:5000])
            else:
                items.append(f.read().decode("utf-8")[:5000])
        with st.spinner(""):
            res = parse_with_milo(items)
            if res is not None:
                if not res.has_academic_content:
                    st.session_state["reply"] = res.assistant_reply or "Hey! Send class notes or deadlines anytime."
                else:
                    st.session_state["reply"] = ""
                    for t in res.topics:
                        log_d = t.logged_date if t.logged_date else str(date.today())
                        conn.execute("INSERT INTO topics(subject,topic,module,logged_date)VALUES(?,?,?,?)", (t.subject, t.topic, t.module or "General", log_d))
                    for d in res.dues:
                        conn.execute("INSERT INTO dues(subject,task,due_date,status)VALUES(?,?,?,'Pending')", (d.subject, d.task, d.due_date or "TBD
