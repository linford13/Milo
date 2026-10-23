import os
import re
import html
import time
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from PIL import Image
from pypdf import PdfReader
from pydantic import BaseModel
from google import genai
from google.genai import types, errors

# ───────────────────────── Config ─────────────────────────
DB_FILE = "milo_study.db"
# gemini-2.5-* were retired in Oct 2026; 3.x are the current replacements.
# Override without editing code: add GEMINI_MODELS = "model-a, model-b" to Streamlit secrets.
DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview"]
REVISION_GAPS = [1, 3, 7, 14, 30]  # spaced-repetition checkpoints (days)
MAX_PDF_PAGES = 15
MAX_TEXT_CHARS = 12000


def today() -> date:
    return date.today()


app_icon = "📌"
if os.path.exists("milo_icon.png"):
    try:
        app_icon = Image.open("milo_icon.png")
    except Exception:
        app_icon = "📌"

st.set_page_config(page_title="MILO", page_icon=app_icon, layout="wide")

# ───────────────────────── Styling ─────────────────────────
st.markdown(
    """
<style>
.stApp { background: #0b0f17; color: #f1f5f9; }
.block-container { padding-top: 1.8rem; max-width: 1200px; }
[data-testid='stChatInput'] {
  background: #fceee8 !important; border-radius: 32px !important;
  padding: 6px 14px !important; border: none !important;
  box-shadow: none !important; max-width: 820px; margin: 0 auto 1rem auto;
}
[data-testid='stChatInput']>div {
  background: transparent !important; border: none !important; box-shadow: none !important;
}
[data-testid='stChatInput'] textarea { color: #18181b !important; font-size: 1rem !important; }
[data-testid='stChatInput'] textarea::placeholder { color: #6e655f !important; opacity: 1 !important; }
[data-testid='stChatInput'] button { color: #18181b !important; }
.reply-box {
  background: #1e293b; border: 1px solid #334155; padding: 12px 18px;
  border-radius: 16px; color: #f8fafc; font-size: 0.95rem;
  margin: 0 auto 0.6rem auto; max-width: 820px; line-height: 1.55;
}
.notice { max-width: 820px; margin: 0 auto 1rem auto; color: #64748b; font-size: 0.8rem; text-align: center; }
.sticky {
  position: relative; background: #fbf7dc; color: #27272a;
  padding: 26px 18px 18px; border-radius: 4px;
  box-shadow: 0 12px 24px rgba(0,0,0,0.5); transform: rotate(-1.5deg);
  height: 340px; box-sizing: border-box; font-family: cursive, sans-serif;
  overflow: visible; margin-top: 6px;
}
.sticky-list { max-height: 255px; overflow-y: auto; padding-right: 4px; }
.pin {
  position: absolute; top: -11px; left: 50%; transform: translateX(-50%);
  width: 22px; height: 22px; border-radius: 50%; z-index: 10;
  background: radial-gradient(circle at 35% 35%, #ff4b4b, #8a0b0b);
  box-shadow: 0 3px 6px rgba(0,0,0,0.5);
}
.stitle { font-weight: 700; border-bottom: 2px dashed #d1c79f; padding-bottom: 6px; margin-bottom: 10px; text-align: center; }
.sitem { font-size: 0.86rem; margin-bottom: 8px; padding-bottom: 5px; border-bottom: 1px dotted #e2d9b5; }
.when { font-size: 0.78rem; font-weight: 700; }
.when.hot { color: #dc2626; }
.when.ok { color: #52525b; }
[data-testid='stVerticalBlockBorderWrapper']:has(.slate-marker) {
  border: 2.5px solid #ffffff !important; border-radius: 28px !important;
  padding: 18px !important; background: #0b0f17 !important;
  box-sizing: border-box !important; margin-top: 6px !important;
}
div[data-baseweb='select']>div {
  background: #1e293b !important; color: #f8fafc !important;
  border-radius: 10px !important; border: 1px solid #334155 !important; font-weight: 600;
}
.drow { font-size: 0.86rem; padding: 6px 0; border-bottom: 1px solid #1e293b; }
.drow small { color: #64748b; }
@media (max-width: 640px) { .sticky { transform: rotate(-0.5deg) !important; } }
</style>
""",
    unsafe_allow_html=True,
)

# ───────────────────────── API client ─────────────────────────
raw_key = None
try:
    raw_key = st.secrets.get("GEMINI_API_KEY")
except Exception:
    raw_key = None
if not raw_key:
    raw_key = os.getenv("GEMINI_API_KEY")
if not raw_key:
    st.error("Missing GEMINI_API_KEY. Add it to Streamlit secrets or your environment.")
    st.stop()
api_key = str(raw_key).strip().strip('"').strip("'")

MODELS = list(DEFAULT_MODELS)
try:
    _override = st.secrets.get("GEMINI_MODELS") or os.getenv("GEMINI_MODELS")
except Exception:
    _override = os.getenv("GEMINI_MODELS")
if _override:
    MODELS = [m.strip() for m in str(_override).split(",") if m.strip()]


@st.cache_resource
def get_client(key: str):
    return genai.Client(api_key=key)


client = get_client(api_key)


# ───────────────────────── Database ─────────────────────────
@st.cache_resource
def init_db():
    with closing(sqlite3.connect(DB_FILE)) as conn, conn:
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_topics_date ON topics(logged_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dues_status ON dues(status, due_date)")
    return True


init_db()


def db_exec(sql: str, params: tuple = ()):
    with closing(sqlite3.connect(DB_FILE)) as conn, conn:
        return conn.execute(sql, params)


def db_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    with closing(sqlite3.connect(DB_FILE)) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def db_has(sql: str, params: tuple) -> bool:
    with closing(sqlite3.connect(DB_FILE)) as conn:
        return conn.execute(sql, params).fetchone() is not None


def clean_date(value, allow_tbd=False) -> str:
    """Return a valid YYYY-MM-DD string; fall back to today (or TBD for dues)."""
    s = str(value or "").strip()
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return "TBD" if allow_tbd else today().isoformat()


def add_topic(subject, topic, module, logged_date) -> bool:
    subject = (subject or "General").strip()
    topic = (topic or "").strip()
    module = (module or "General").strip()
    logged_date = clean_date(logged_date)
    if not topic:
        return False
    if db_has(
        "SELECT 1 FROM topics WHERE lower(subject)=lower(?) AND lower(topic)=lower(?) AND logged_date=?",
        (subject, topic, logged_date),
    ):
        return False
    db_exec(
        "INSERT INTO topics(subject,topic,module,logged_date) VALUES(?,?,?,?)",
        (subject, topic, module, logged_date),
    )
    return True


def add_due(subject, task, due_date) -> bool:
    subject = (subject or "General").strip()
    task = (task or "").strip()
    due_date = clean_date(due_date, allow_tbd=True)
    if not task:
        return False
    if db_has(
        "SELECT 1 FROM dues WHERE lower(subject)=lower(?) AND lower(task)=lower(?) "
        "AND due_date=? AND status='Pending'",
        (subject, task, due_date),
    ):
        return False
    db_exec(
        "INSERT INTO dues(subject,task,due_date,status) VALUES(?,?,?,'Pending')",
        (subject, task, due_date),
    )
    return True


# ───────────────────────── AI layer ─────────────────────────
class TopicItem(BaseModel):
    subject: str
    topic: str
    module: str
    logged_date: str


class DueItem(BaseModel):
    subject: str
    task: str
    due_date: str


class MiloReply(BaseModel):
    assistant_reply: str
    topics: list[TopicItem]
    dues: list[DueItem]


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str


class Quiz(BaseModel):
    questions: list[QuizQuestion]


@st.cache_data(ttl=86400, show_spinner=False)
def discover_models(key: str):
    """Last resort: ask the API which flash models exist. Cached for a day, runs only if every
    configured model failed, so it can never loop or slow normal use."""
    try:
        bad = ("image", "live", "audio", "tts", "embed", "robotics", "computer", "native")
        found = []
        for m in get_client(key).models.list():
            name = (m.name or "").replace("models/", "")
            actions = getattr(m, "supported_actions", None) or []
            if name.startswith("gemini-") and "flash" in name and "generateContent" in actions \
                    and not any(b in name for b in bad):
                found.append(name)
        return sorted(found, reverse=True)[:4]
    except Exception:
        return []


def _try_models(models, contents, cfg, errs):
    for model in models:
        for attempt in range(3):
            try:
                res = client.models.generate_content(model=model, contents=contents, config=cfg)
                if res and res.text:
                    return res
                errs[model] = "empty response"
                break
            except errors.ServerError as e:  # 5xx incl. 503 overload: retry with backoff
                errs[model] = f"{getattr(e, 'code', '5xx')} busy"
                time.sleep(2**attempt)
            except errors.ClientError as e:
                code = getattr(e, "code", "4xx")
                errs[model] = f"{code} {str(getattr(e, 'message', e))[:60]}"
                if code == 429:  # rate limit: wait and retry
                    time.sleep(2**attempt)
                    continue
                break  # 404 / 400 / 403: this model is not usable, go to the next
            except Exception as e:  # network etc.
                errs[model] = str(e)[:60]
                break
    return None


def call_gemini(contents, schema=None, system=None):
    """Retry 503/429 with backoff, fall through the model list, then try discovered models.
    Returns (response, error_summary)."""
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json" if schema else None,
        response_schema=schema,
    )
    errs = {}
    res = _try_models(MODELS, contents, cfg, errs)
    if res is None and any(str(v).startswith("404") for v in errs.values()):
        extra = [m for m in discover_models(api_key) if m not in MODELS]
        if extra:
            res = _try_models(extra, contents, cfg, errs)
    if res is not None:
        return res, None
    return None, " | ".join(f"{m}: {e}" for m, e in errs.items()) or "unknown error"


def parse_struct(res, schema):
    parsed = getattr(res, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    for candidate in (res.text, (re.search(r"\{.*\}", res.text or "", re.DOTALL) or [None])[0]):
        if not candidate:
            continue
        try:
            return schema.model_validate_json(candidate)
        except Exception:
            continue
    return None


def chat_with_milo(items: list):
    history = st.session_state.get("history", [])[-6:]
    hist_txt = "\n".join(f"{h['role']}: {h['text']}" for h in history)
    system = (
        f"Current date: {today().isoformat()} ({today().strftime('%A')}).\n"
        "You are MILO, a fast, friendly study assistant for a college student.\n"
        "1. Reply naturally in 'assistant_reply'. For study doubts, explain clearly with a short example "
        "(under ~150 words). Use earlier conversation for follow-ups.\n"
        "2. If the input contains class notes, lecture content or assignments, extract them into 'topics' "
        "and 'dues'. Otherwise return empty lists.\n"
        "3. Resolve relative dates ('tomorrow', 'next Friday') into YYYY-MM-DD using the current date.\n"
        f"4. Default logged_date to {today().isoformat()}. Use 'TBD' for an unknown due_date. "
        "module defaults to 'General'.\n"
        "5. Make topic entries short and specific; do not invent assignments that were not mentioned."
    )
    contents = ([f"Recent conversation:\n{hist_txt}"] if hist_txt else []) + items
    res, err = call_gemini(contents, schema=MiloReply, system=system)
    if res is None:
        return None, err
    return parse_struct(res, MiloReply), None


def save_extraction(parsed: MiloReply):
    n_t = sum(add_topic(t.subject, t.topic, t.module, t.logged_date) for t in parsed.topics)
    n_d = sum(add_due(d.subject, d.task, d.due_date) for d in parsed.dues)
    return n_t, n_d


# ───────────────────────── Helpers ─────────────────────────
def when_label(due: str):
    """Human label + hot flag for a due date."""
    try:
        delta = (date.fromisoformat(str(due)) - today()).days
    except ValueError:
        return "no date set", False
    if delta < 0:
        return f"overdue by {-delta}d", True
    if delta == 0:
        return "due today", True
    if delta == 1:
        return "due tomorrow", True
    return f"in {delta} days", False


def read_attachment(f):
    try:
        f.seek(0)
    except Exception:
        pass
    name = getattr(f, "name", "").lower()
    ftype = getattr(f, "type", "").lower()
    try:
        if "image" in ftype or name.endswith((".png", ".jpg", ".jpeg")):
            return Image.open(f)
        if "pdf" in ftype or name.endswith(".pdf"):
            reader = PdfReader(f)
            pages = [p.extract_text() or "" for p in reader.pages[:MAX_PDF_PAGES]]
            text = "\n".join(pages).strip()
            return text[:MAX_TEXT_CHARS] or None
        return f.read().decode("utf-8", errors="ignore")[:MAX_TEXT_CHARS]
    except Exception:
        return None


def streak_days(dates: set) -> int:
    day = today() if today().isoformat() in dates else today() - timedelta(days=1)
    n = 0
    while day.isoformat() in dates:
        n += 1
        day -= timedelta(days=1)
    return n


def topics_text(df: pd.DataFrame) -> str:
    return "\n".join(
        f"- [{r.subject}] ({r.module}) {r.topic} ({r.logged_date})" for r in df.itertuples()
    )


# ───────────────────────── Header + chat ─────────────────────────
st.markdown(
    "<svg width='180' height='60' viewBox='0 0 240 75' style='user-select:none;display:block;margin-bottom:1rem'>"
    "<text x='10' y='58' fill='#ffffff' font-family='Georgia,serif' font-size='58' font-weight='700' letter-spacing='3'>MIL</text>"
    "<text x='142' y='58' fill='#ffffff' font-family='Georgia,serif' font-size='58' font-weight='700'>O</text>"
    "<ellipse cx='172' cy='38' rx='36' ry='11' fill='none' stroke='#ffffff' stroke-width='1.2' transform='rotate(-22 172 38)'/>"
    "<ellipse cx='172' cy='38' rx='36' ry='8' fill='none' stroke='#ffffff' stroke-width='0.9' transform='rotate(-15 172 38)'/>"
    "<text x='132' y='52' fill='#ffffff' font-size='12'>✦</text>"
    "<text x='202' y='32' fill='#ffffff' font-size='12'>✦</text></svg>",
    unsafe_allow_html=True,
)

st.session_state.setdefault("history", [])

_, chat_col, _ = st.columns([0.1, 3.8, 0.1])
with chat_col:
    prompt = st.chat_input(
        "Ask a doubt, paste lecture notes, or drop deadlines...",
        accept_file="multiple",
        file_type=["png", "jpg", "jpeg", "pdf", "txt"],
    )
    if st.session_state.get("reply"):
        safe = html.escape(str(st.session_state["reply"])).replace("\n", "<br>")
        st.markdown(f"<div class='reply-box'>💡 <b>MILO:</b> {safe}</div>", unsafe_allow_html=True)
    if st.session_state.get("notice"):
        st.markdown(f"<div class='notice'>{html.escape(st.session_state['notice'])}</div>", unsafe_allow_html=True)
    if st.session_state["history"]:
        with st.expander("💬 Conversation history"):
            for h in st.session_state["history"][-12:]:
                who = "You" if h["role"] == "Student" else "MILO"
                st.markdown(f"**{who}:** {h['text']}")
            if st.button("Clear conversation", key="clear_chat"):
                st.session_state["history"] = []
                st.session_state["reply"] = ""
                st.session_state["notice"] = ""
                st.rerun()

if prompt:
    if isinstance(prompt, str):
        txt, files = prompt, []
    else:
        txt = getattr(prompt, "text", "") or ""
        files = getattr(prompt, "files", []) or []

    items = [txt.strip()] if txt.strip() else []
    for f in files:
        part = read_attachment(f)
        if part is not None:
            items.append(part)

    if items:
        with st.spinner("MILO is thinking..."):
            parsed, err = chat_with_milo(items)
        if parsed is None:
            st.session_state["reply"] = (
                "MILO couldn't reach the AI service right now. Your message was not saved. "
                "Try again in a few seconds."
            )
            st.session_state["notice"] = f"Details: {err}" if err else ""
        else:
            n_t, n_d = save_extraction(parsed)
            st.session_state["reply"] = parsed.assistant_reply or "Done!"
            bits = []
            if n_t:
                bits.append(f"{n_t} topic{'s' if n_t != 1 else ''} logged")
            if n_d:
                bits.append(f"{n_d} due{'s' if n_d != 1 else ''} added")
            skipped = (len(parsed.topics) - n_t) + (len(parsed.dues) - n_d)
            if skipped > 0:
                bits.append(f"{skipped} duplicate{'s' if skipped != 1 else ''} skipped")
            st.session_state["notice"] = " · ".join(bits)
            st.session_state["history"] += [
                {"role": "Student", "text": txt.strip() or "(attachment)"},
                {"role": "MILO", "text": parsed.assistant_reply},
            ]
        st.rerun()

st.write("")

tab_dash, tab_study, tab_insights, tab_manage = st.tabs(
    ["📌 Dashboard", "🧠 Revise & Quiz", "📊 Insights", "⚙️ Manage"]
)

td = today()

# ───────────────────────── Dashboard ─────────────────────────
with tab_dash:
    left, right = st.columns([1, 1.8], gap="large")

    with left:
        dues_all = db_df(
            "SELECT * FROM dues WHERE status='Pending' "
            "ORDER BY CASE WHEN due_date='TBD' THEN 1 ELSE 0 END, due_date ASC, id ASC"
        )
        dues_top = dues_all.head(5)

        rows = []
        for r in dues_top.itertuples():
            label, hot = when_label(r.due_date)
            rows.append(
                "<div class='sitem'><b>[" + html.escape(str(r.subject)) + "]</b> "
                + html.escape(str(r.task))
                + "<br><span class='when " + ("hot" if hot else "ok") + "'>"
                + html.escape(str(r.due_date)) + " · " + label + "</span></div>"
            )
        body = (
            "<div class='sticky-list'>" + "".join(rows) + "</div>"
            if rows
            else "<div style='text-align:center;padding-top:70px'>Nothing pending. Enjoy the calm.</div>"
        )
        st.markdown(
            "<div class='sticky'><div class='pin'></div><div class='stitle'>UPCOMING DUES</div>"
            + body + "</div>",
            unsafe_allow_html=True,
        )

        if not dues_all.empty:
            with st.expander(f"Manage pending dues ({len(dues_all)})"):
                for r in dues_all.itertuples():
                    c1, c2, c3 = st.columns([4, 0.7, 0.7])
                    with c1:
                        st.write(f"**[{r.subject}]** {r.task} (Due: {r.due_date})")
                    with c2:
                        if st.button("✅", key=f"done_due_{r.id}", help="Mark complete", use_container_width=True):
                            db_exec("UPDATE dues SET status='Done' WHERE id=?", (int(r.id),))
                            st.rerun()
                    with c3:
                        if st.button("🗑️", key=f"del_due_{r.id}", help="Delete", use_container_width=True):
                            db_exec("DELETE FROM dues WHERE id=?", (int(r.id),))
                            st.rerun()

    with right:
        with st.container(border=True):
            st.markdown("<span class='slate-marker'></span>", unsafe_allow_html=True)
            mon = td - timedelta(days=td.weekday())
            sun = mon + timedelta(days=6)

            subjects = db_df("SELECT DISTINCT subject FROM topics ORDER BY subject")["subject"].tolist()
            sc1, sc2, sc3 = st.columns([1.2, 1.2, 1])
            with sc1:
                mode = st.selectbox(
                    "View",
                    ["Today's Discussion", "This Week (Mon - Sun)", "Custom Range"],
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
            with sc3:
                subj_filter = st.selectbox(
                    "Subject", ["All subjects"] + subjects, label_visibility="collapsed"
                )

            if mode == "Today's Discussion":
                start, end = td, td
            elif mode == "This Week (Mon - Sun)":
                start, end = mon, sun
            elif isinstance(drange, (tuple, list)) and len(drange) > 0:
                a = drange[0]
                b = drange[1] if len(drange) > 1 else a
                start, end = min(a, b), max(a, b)
            else:
                start, end = td, td

            sql = "SELECT * FROM topics WHERE logged_date BETWEEN ? AND ?"
            params = [str(start), str(end)]
            if subj_filter != "All subjects":
                sql += " AND subject=?"
                params.append(subj_filter)
            sql += " ORDER BY logged_date DESC, id DESC"
            df = db_df(sql, tuple(params))

            with st.container(height=235, border=False):
                if df.empty:
                    st.markdown(
                        "<p style='color:#64748b;padding-top:45px;text-align:center;font-size:0.88rem'>"
                        "No discussions logged for this period yet.</p>",
                        unsafe_allow_html=True,
                    )
                for r in df.itertuples():
                    c_item, c_del = st.columns([5, 0.6])
                    with c_item:
                        st.markdown(
                            f"<div class='drow'><b>[{html.escape(str(r.subject))}]</b> "
                            f"({html.escape(str(r.module))}): {html.escape(str(r.topic))} "
                            f"<small>[{html.escape(str(r.logged_date))}]</small></div>",
                            unsafe_allow_html=True,
                        )
                    with c_del:
                        if st.button("🗑️", key=f"del_top_{r.id}", use_container_width=True):
                            db_exec("DELETE FROM topics WHERE id=?", (int(r.id),))
                            st.rerun()

# ───────────────────────── Revise & Quiz ─────────────────────────
with tab_study:
    topics_all = db_df("SELECT * FROM topics")

    st.subheader("🔁 Revise today")
    st.caption("Topics hit their review checkpoints at 1, 3, 7, 14 and 30 days after you logged them.")
    if topics_all.empty:
        st.info("Log a few topics first, then MILO will tell you what to revisit and when.")
    else:
        ages = (pd.Timestamp(td) - pd.to_datetime(topics_all["logged_date"], errors="coerce")).dt.days
        due_rev = topics_all[ages.isin(REVISION_GAPS)].assign(days_ago=ages[ages.isin(REVISION_GAPS)])
        if due_rev.empty:
            st.success("Nothing due for revision today.")
        else:
            for r in due_rev.sort_values("days_ago").itertuples():
                st.markdown(f"- **[{r.subject}]** {r.topic} · logged {int(r.days_ago)} day(s) ago")

    st.divider()
    st.subheader("🧪 Quiz and cheat sheet")
    if topics_all.empty:
        st.caption("Quizzes are built from your logged topics.")
    else:
        q1, q2, q3 = st.columns([1.3, 1.2, 1])
        with q1:
            study_subj = st.selectbox("Subject", ["All subjects"] + sorted(topics_all["subject"].unique().tolist()), key="study_subj")
        with q2:
            study_win = st.selectbox("Period", ["Last 7 days", "Last 14 days", "Last 30 days", "All time"], index=1, key="study_win")
        with q3:
            n_q = st.slider("Questions", 3, 10, 5, key="study_nq")

        pool = topics_all.copy()
        if study_subj != "All subjects":
            pool = pool[pool["subject"] == study_subj]
        days = {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30}.get(study_win)
        if days:
            cutoff = (td - timedelta(days=days)).isoformat()
            pool = pool[pool["logged_date"] >= cutoff]
        pool = pool.tail(80)

        b1, b2 = st.columns(2)
        gen_quiz = b1.button("Generate quiz", use_container_width=True, disabled=pool.empty)
        gen_sheet = b2.button("Make cheat sheet", use_container_width=True, disabled=pool.empty)
        if pool.empty:
            st.caption("No topics in that selection.")

        if gen_quiz:
            with st.spinner("Writing questions..."):
                res, err = call_gemini(
                    [
                        f"Write exactly {n_q} multiple-choice questions that test understanding "
                        "(not just recall) of these topics. Each question has 4 options, one correct "
                        "answer given as answer_index (0-3), and a one-sentence explanation.\n\n"
                        + topics_text(pool)
                    ],
                    schema=Quiz,
                    system="You are a rigorous but friendly examiner. Only use the topics provided.",
                )
            quiz = parse_struct(res, Quiz) if res else None
            if quiz and quiz.questions:
                st.session_state["quiz"] = quiz
                st.session_state["quiz_id"] = st.session_state.get("quiz_id", 0) + 1
            else:
                st.error(f"Couldn't build a quiz right now ({err or 'bad response'}). Try again.")

        if gen_sheet:
            with st.spinner("Condensing..."):
                res, err = call_gemini(
                    [
                        "Create a compact revision cheat sheet in Markdown from these topics: group by subject, "
                        "give key points, definitions and one memory hook per topic. Keep it tight.\n\n"
                        + topics_text(pool)
                    ],
                    system="You are a study coach. Only use the topics provided; do not invent syllabus.",
                )
            if res:
                st.session_state["sheet"] = res.text
            else:
                st.error(f"Couldn't make a cheat sheet right now ({err}).")

        if st.session_state.get("sheet"):
            with st.expander("📄 Cheat sheet", expanded=True):
                st.markdown(st.session_state["sheet"])
                st.download_button("Download as .md", st.session_state["sheet"], "milo_cheatsheet.md")

        quiz = st.session_state.get("quiz")
        if quiz:
            qid = st.session_state["quiz_id"]
            with st.form("quiz_form"):
                picks = [
                    st.radio(f"**{i + 1}. {q.question}**", q.options, index=None, key=f"qz_{qid}_{i}")
                    for i, q in enumerate(quiz.questions)
                ]
                submitted = st.form_submit_button("Check answers")
            if submitted:
                score = 0
                for i, (q, pick) in enumerate(zip(quiz.questions, picks)):
                    idx = q.answer_index if 0 <= q.answer_index < len(q.options) else 0
                    correct = q.options[idx]
                    if pick == correct:
                        score += 1
                        st.success(f"Q{i + 1}: correct. {q.explanation}")
                    else:
                        st.error(f"Q{i + 1}: the answer is “{correct}”. {q.explanation}")
                st.metric("Score", f"{score}/{len(quiz.questions)}")

# ───────────────────────── Insights ─────────────────────────
with tab_insights:
    all_topics = db_df("SELECT * FROM topics")
    all_dues = db_df("SELECT * FROM dues")
    pending = all_dues[all_dues["status"] == "Pending"] if not all_dues.empty else all_dues
    overdue = 0
    for r in pending.itertuples():
        try:
            if date.fromisoformat(str(r.due_date)) < td:
                overdue += 1
        except ValueError:
            pass

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Topics logged", len(all_topics))
    m2.metric("Subjects", all_topics["subject"].nunique() if not all_topics.empty else 0)
    m3.metric("Pending dues", len(pending), delta=f"{overdue} overdue" if overdue else None, delta_color="inverse")
    m4.metric("Logging streak", f"{streak_days(set(all_topics['logged_date'])) if not all_topics.empty else 0} days")

    if not all_topics.empty:
        st.markdown("**Topics per subject**")
        st.bar_chart(all_topics.groupby("subject").size())
        st.markdown("**Last 4 weeks**")
        recent = all_topics[all_topics["logged_date"] >= (td - timedelta(days=27)).isoformat()]
        per_day = recent.groupby("logged_date").size().reindex(
            [(td - timedelta(days=i)).isoformat() for i in range(27, -1, -1)], fill_value=0
        )
        st.bar_chart(per_day)

    st.markdown("**Search your notes**")
    query = st.text_input("Search topics", placeholder="e.g. eigenvalues, Module 3, thermodynamics", label_visibility="collapsed")
    if query.strip() and not all_topics.empty:
        like = f"%{query.strip().lower()}%"
        hits = db_df(
            "SELECT subject, module, topic, logged_date FROM topics "
            "WHERE lower(topic) LIKE ? OR lower(subject) LIKE ? OR lower(module) LIKE ? "
            "ORDER BY logged_date DESC",
            (like, like, like),
        )
        st.caption(f"{len(hits)} match(es)")
        st.dataframe(hits, use_container_width=True, hide_index=True)

# ───────────────────────── Manage ─────────────────────────
with tab_manage:
    st.subheader("✍️ Add missed topic")
    with st.form("manual_entry_form", clear_on_submit=True):
        m1, m2, m3 = st.columns(3)
        m_date = m1.date_input("Date of lecture", value=td, max_value=td)
        m_subj = m2.text_input("Subject", placeholder="e.g., Mathematics")
        m_mod = m3.text_input("Module", placeholder="e.g., Module 2")
        m_top = st.text_area("Topic / notes", placeholder="What was covered?")
        if st.form_submit_button("Save topic"):
            if m_subj.strip() and m_top.strip():
                if add_topic(m_subj, m_top, m_mod, str(m_date)):
                    st.success(f"Saved for {m_date}.")
                    st.rerun()
                else:
                    st.info("That topic is already logged for that date.")
            else:
                st.warning("Fill in both Subject and Topic.")

    st.subheader("➕ Add a due manually")
    with st.form("manual_due_form", clear_on_submit=True):
        d1, d2, d3 = st.columns([1, 2, 1])
        dd_subj = d1.text_input("Subject", key="dd_subj")
        dd_task = d2.text_input("Task", key="dd_task", placeholder="e.g., Lab report 3")
        dd_date = d3.date_input("Due date", value=td + timedelta(days=1), key="dd_date")
        no_date = st.checkbox("No date yet")
        if st.form_submit_button("Add due"):
            if dd_subj.strip() and dd_task.strip():
                if add_due(dd_subj, dd_task, "TBD" if no_date else str(dd_date)):
                    st.success("Due added.")
                    st.rerun()
                else:
                    st.info("That due already exists.")
            else:
                st.warning("Fill in both Subject and Task.")

    st.subheader("📦 Export")
    e1, e2 = st.columns(2)
    e1.download_button(
        "Download topics (CSV)",
        db_df("SELECT subject, module, topic, logged_date FROM topics ORDER BY logged_date").to_csv(index=False),
        "milo_topics.csv",
        "text/csv",
        use_container_width=True,
    )
    e2.download_button(
        "Download dues (CSV)",
        db_df("SELECT subject, task, due_date, status FROM dues ORDER BY due_date").to_csv(index=False),
        "milo_dues.csv",
        "text/csv",
        use_container_width=True,
    )

    with st.expander("🧹 Cleanup"):
        if st.button("Remove completed dues"):
            db_exec("DELETE FROM dues WHERE status='Done'")
            st.rerun()
        confirm = st.checkbox("I understand this deletes everything")
        if st.button("Wipe all data", disabled=not confirm):
            db_exec("DELETE FROM topics")
            db_exec("DELETE FROM dues")
            st.rerun()
