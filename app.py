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


@st.cache_resource(show_spinner=False)
def get_available_models():
    preferred = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-flash-latest",
    ]
    skip_words = [
        "embedding",
        "tts",
        "audio",
        "image",
        "vision",
        "live",
        "aqa",
        "thinking",
    ]
    found = []
    try:
        for m in ai_client.models.list():
            name = getattr(m, "name", "")
            clean = name.replace("models/", "")
            if "gemini" in clean:
                if not any(w in clean.lower() for w in skip_words):
                    found.append(clean)
    except Exception:
        pass

    ordered = [p for p in preferred if p in found]
    for f in found:
        if "flash" in f and f not in ordered:
        
            ordered.append(f)
    if not ordered:
        ordered = preferred
    return ordered


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
        "You are MILO, a helpful AI study assistant and academic tracker for a college student.",
        "Your goal is twofold:",
        "1. Answer any study questions, explain doubts, or chat naturally. Put your main response/answer in 'assistant_reply'.",
        "2. If the user input contains class notes, lecture updates, or assignments, extract them into 'topics' and 'dues'. If there are no notes/dues, leave those lists empty [] and just answer the question.",
        "Return ONLY valid JSON matching this exact structure:",
        "{",
        '  "assistant_reply": "Your conversational answer or explanation here",',
        '  "topics": [',
        '    {"subject": "str", "topic": "str", "module": "General", "logged_date": "YYYY-MM-DD"}',
        "  ],",
        '  "dues": [',
        '    {"subject": "str", "task": "str", "due_date": "YYYY-MM-DD"}',
        "  ]",
        "}",
        "Rules:",
        "- Always provide a helpful explanation or friendly acknowledgment in 'assistant_reply'.",
        f"- Default logged_date to {today_iso} if topics are found without dates.",
        "- Use 'TBD' for due_date if unknown.",
    ]
    sys_prompt = "\n".join(prompt_lines)
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json"
    )

    last_error = ""
    for m in get_available_models():
        for attempt in range(3):
            try:
                res = ai_client.models.generate_content(
                    model=m,
                    contents=[sys_prompt] + content_items,
                    config=cfg,
                )
                if res and res.text:
                    return extract_json_dict(res.text)
            except Exception as err:
                last_error = str(err)
                err_up = last_error.upper()
                is_busy = (
                    "503" in last_error
                    or "429" in last_error
                    or "UNAVAILABLE" in err_up
                    or "OVERLOADED" in err_up
                    or "RESOURCE_EXHAUSTED" in err_up
                )
                if is_busy and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

    st.error("API Error: " + last_error)
    return None


logo_lines = [
    "<svg width='180' height='60' viewBox='0 0 240 75'",
    " style='user-select:none;display:block;margin-bottom:1rem'>",
    "<text x='10' y='58' fill='#ffffff' font-family='Georgia,serif'",
    " font-size='58' font-weight='700' letter-spacing='3'>MIL</text>",
    "<text x='142' y='58' fill='#ffffff' font-family='Georgia,serif'",
    " font-size='58' font-weight='700'>O</text>",
    "<ellipse cx='172' cy='38' rx='36' ry='11' fill='none'",
    " stroke='#ffffff' stroke-width='1.2'",
    " transform='rotate(-22 172 38)'/>",
    "<ellipse cx='172' cy='38' rx='36' ry='8' fill='none'",
    " stroke='#ffffff' stroke-width='0.9'",
    " transform='rotate(-15 172 38)'/>",
    "<text x='132' y='52' fill='#ffffff' font-size='12'>✦</text>",
    "<text x='202' y='32' fill='#ffffff' font-size='12'>✦</text>",
    "</svg>",
]
st.markdown("".join(logo_lines), unsafe_allow_html=True)

_, chat_col, _ = st.columns([0.1, 3.8, 0.1])
with chat_col:
    prompt = st.chat_input(
        "Ask a doubt, paste lecture notes, or drop deadlines...",
        accept_file="multiple",
        file_type=["png", "jpg", "jpeg", "pdf", "txt"],
    )
    if st.session_state.get("reply"):
        safe_reply = html.escape(str(st.session_state["reply"]))
        reply_html = (
            "<div class='reply-box'>💡 <b>MILO:</b> " + safe_reply + "</div>"
        )
        st.markdown(reply_html, unsafe_allow_html=True)

if prompt:
    if isinstance(prompt, str):
        txt = prompt
        files = []
    else:
        txt = getattr(prompt, "text", "") or ""
        files = getattr(prompt, "files", []) or []

    if txt.strip() or files:
        items = []
        if txt.strip():
            items.append(txt.strip())
        for f in files:
            try:
                f.seek(0)
            except Exception:
                pass
            fname = getattr(f, "name", "").lower()
            ftype = getattr(f, "type", "").lower()
            is_img = "image" in ftype or fname.endswith(
                (".png", ".jpg", ".jpeg")
            )
            if is_img:
                try:
                    items.append(Image.open(f))
                except Exception:
                    pass
            elif "pdf" in ftype or fname.endswith(".pdf"):
                try:
                    reader = PdfReader(f)
                    pages_txt = [
                        p.extract_text() or ""
                        for p in reader.pages[:8]
                    ]
                    items.append("\n".join(pages_txt)[:5000])
                except Exception:
                    pass
            else:
                try:
                    raw_bytes = f.read()
                    items.append(
                        raw_bytes.decode("utf-8", errors="ignore")[:5000]
                    )
                except Exception:
                    pass

        if items:
            with st.spinner("Thinking..."):
                res = parse_with_milo(items)
                if isinstance(res, dict):
                    # Always display the conversational reply/answer
                    st.session_state["reply"] = res.get("assistant_reply") or "Done!"
                    
                    topics_list = res.get("topics") or []
                    dues_list = res.get("dues") or []
                    
                    if topics_list or dues_list:
                        with sqlite3.connect(DB_FILE) as conn:
                            for t in topics_list:
                                if not isinstance(t, dict):
                                    continue
                                subj = str(t.get("subject") or "General").strip()
                                top = str(t.get("topic") or "").strip()
                                mod = str(t.get("module") or "General").strip()
                                raw_d = str(t.get("logged_date") or "").strip()
                                log_d = raw_d if raw_d else str(date.today())
                                if top:
                                    conn.execute(
                                        "INSERT INTO topics(subject,topic,module,logged_date) VALUES(?,?,?,?)",
                                        (subj, top, mod, log_d),
                                    )
                            for d in dues_list:
                                if not isinstance(d, dict):
                                    continue
                                subj = str(d.get("subject") or "General").strip()
                                task = str(d.get("task") or "").strip()
                                due_val = str(d.get("due_date") or "TBD").strip()
                                if task:
                                    conn.execute(
                                        "INSERT INTO dues(subject,task,due_date,status) VALUES(?,?,?,'Pending')",
                                        (subj, task, due_val),
                                    )
                    st.rerun()

st.write("")

left, right = st.columns([1, 1.8], gap="large")

with left:
    with sqlite3.connect(DB_FILE) as conn:
        dues_query = (
            "SELECT * FROM dues WHERE status='Pending' "
            "ORDER BY due_date ASC LIMIT 5"
        )
        dues = pd.read_sql_query(dues_query, conn)

    row_list = []
    for _, r in dues.iterrows():
        s_subj = html.escape(str(r["subject"]))
        s_task = html.escape(str(r["task"]))
        s_due = html.escape(str(r["due_date"]))
        item_str = (
            "<div class='sitem'><b>["
            + s_subj
            + "]</b> "
            + s_task
            + "<br><span style='color:#dc2626'>Due: "
            + s_due
            + "</span></div>"
        )
        row_list.append(item_str)

    if row_list:
        rows_html = (
            "<div class='sticky-list'>"
            + "".join(row_list)
            + "</div>"
        )
    else:
        rows_html = (
            "<div style='text-align:center;padding-top:70px'>"
            "No dues pending!</div>"
        )

    sticky_html = (
        "<div class='sticky'><div class='pin'></div>"
        "<div class='stitle'>UPCOMING DUES</div>"
        + rows_html
        + "</div>"
    )
    st.markdown(sticky_html, unsafe_allow_html=True)

    if not dues.empty:
        with st.expander("Mark Complete"):
            for _, r in dues.iterrows():
                row_id = int(r["id"])
                btn_lbl = (
                    "✓ "
                    + str(r["subject"])
                    + ": "
                    + str(r["task"])[:15]
                )
                if st.button(
                    btn_lbl,
                    key="d" + str(row_id),
                    use_container_width=True,
                ):
                    with sqlite3.connect(DB_FILE) as conn:
                        conn.execute(
                            "UPDATE dues SET status='Done' WHERE id=?",
                            (row_id,),
                        )
                    st.rerun()

with right:
    with st.container(border=True):
        st.markdown(
            "<span class='slate-marker'></span>",
            unsafe_allow_html=True,
        )
        td = date.today()
        # Correct calculation for Monday to Sunday of the current week
        mon = td - timedelta(days=td.weekday())
        sun = mon + timedelta(days=6)

        sc1, sc2 = st.columns([1.2, 1.2])
        with sc1:
            view_opts = [
                "Today's Discussion",
                "This Week (Mon - Sun)",
                "Custom Range",
            ]
            mode = st.selectbox(
                "View",
                view_opts,
                label_visibility="collapsed",
            )

        drange = None
        if mode == "Custom Range":
            with sc2:
                drange = st.date_input(
                    "Date Range",
                    value=(td - timedelta(days=7), td),
                    max_value=td,
                    label_visibility="collapsed",
                )

        with sqlite3.connect(DB_FILE) as conn:
            if mode == "Today's Discussion":
                q_today = (
                    "SELECT * FROM topics WHERE logged_date=? "
                    "ORDER BY id DESC"
                )
                df = pd.read_sql_query(q_today, conn, params=(str(td),))
            elif mode == "This Week (Mon - Sun)":
                q_week = (
                    "SELECT * FROM topics "
                    "WHERE logged_date BETWEEN ? AND ? "
                    "ORDER BY id DESC"
                )
                df = pd.read_sql_query(
                    q_week, conn, params=(str(mon), str(sun))
                )
            else:
                if drange and isinstance(drange, (tuple, list)):
                    s_d = drange[0]
                    e_d = drange[1] if len(drange) > 1 else s_d
                    q_cust = (
                        "SELECT * FROM topics "
                        "WHERE logged_date BETWEEN ? AND ? "
                        "ORDER BY logged_date DESC, id DESC"
                    )
                    df = pd.read_sql_query(
                        q_cust,
                        conn,
                        params=(
                            str(min(s_d, e_d)),
                            str(max(s_d, e_d)),
                        ),
                    )
                else:
                    cols = [
                        "subject",
                        "topic",
                        "module",
                        "logged_date",
                    ]
                    df = pd.DataFrame(columns=cols)

        disc_list = []
        for _, r in df.iterrows():
            s_subj = html.escape(str(r["subject"]))
            s_mod = html.escape(str(r["module"]))
            s_top = html.escape(str(r["topic"]))
            s_date = html.escape(str(r["logged_date"]))
            d_str = (
                "<div style='margin-bottom:8px;"
                "border-bottom:1px solid #1e293b;"
                "padding-bottom:5px;font-size:0.86rem'>"
                "<b>["
                + s_subj
                + "]</b> ("
                + s_mod
                + "): "
                + s_top
                + " <small style='color:#64748b'>["
                + s_date
                + "]</small></div>"
            )
            disc_list.append(d_str)

        if disc_list:
            items_html = "".join(disc_list)
        else:
            items_html = (
                "<p style='color:#64748b;padding-top:45px;"
                "text-align:center;font-size:0.88rem'>"
                "No discussions logged for this period yet.</p>"
            )

        slate_html = (
            "<div class='scrollable-disc'>"
            + items_html
            + "</div>"
        )
        st.markdown(slate_html, unsafe_allow_html=True)

st.write("")
with st.expander("⚙️ Manage / Reset Database"):
    c_res1, c_res2 = st.columns(2)
    with c_res1:
        if st.button("🗑️ Clear Entire Discussion History", type="primary", use_container_width=True):
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute("DELETE FROM topics")
            st.success("Cleared all discussion history!")
            st.rerun()
    with c_res2:
        if st.button("🗑️ Clear All Pending Dues", use_container_width=True):
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute("DELETE FROM dues")
            st.success("Cleared all dues!")
            st.rerun()
