import os
import json
import re
import time
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
    ".reply-txt {",
    "  text-align: center; color: #e2e8f0; font-size: 0.95rem;",
    "  margin: 0 auto 1rem auto; max-width: 820px;",
    "}",
    ".sticky {",
    "  position: relative; background: #fbf7dc; color: #27272a;",
    "  padding: 26px 18px 18px; border-radius: 4px;",
    "  box-shadow: 0 12px 24px rgba(0,0,0,0.5);",
    "  transform: rotate(-1.5deg); height: 340px;",
    "  box-sizing: border-box; font-family: cursive, sans-serif;",
    "  overflow: hidden;",
    "}",
    ".pin {",
    "  position: absolute; top: -11px; left: 50%;",
    "  transform: translateX(-50%); width: 22px; height: 22px;",
    "  background: radial-gradient(",
    "    circle at 35% 35%, #ff4b4b, #8a0b0b",
    "  );",
    "  border-radius: 50%;",
    "  box-shadow: 0 3px 6px rgba(0,0,0,0.5);",
    "}",
    ".stitle {",
    "  font-weight: 700; border-bottom: 2px dashed #d1c79f;",
    "  padding-bottom: 6px; margin-bottom: 10px;",
    "  text-align: center;",
    "}
