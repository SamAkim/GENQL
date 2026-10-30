import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

import io
import os
import sqlite3
import uuid
from fastapi.staticfiles import StaticFiles
import fitz
import pandas as pd
import numpy as np
import pickle
import json
import math
import random
import logging
from pathlib import Path
from dotenv import load_dotenv
from typing import Any, List, Dict, Literal, Optional, TypedDict, Union
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi import (
    Depends,
    FastAPI,
    Header,
    UploadFile,
    File,
    BackgroundTasks,
    Query,
    HTTPException,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from google import genai
import time
from PIL import Image
from docx import Document as DocxDocument
import requests
import re
from auth import router as auth_router, get_current_user
import warnings

warnings.filterwarnings("ignore", message="Unsupported Windows version")
import jira
from jira import JIRA
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()

# import nltk

# Add path to your offline nltk_data folder
# nltk.data.path.append(r"nltk_data")

# ----------------------------
# Directories & DB path
# ----------------------------
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "app_data.db")
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")
INTER_DIR = os.path.join(BASE_DIR, "data", "intermediate")
STORE_ROOT = os.path.join(BASE_DIR, "stores")
FEEDBACK_DIR = os.path.join(BASE_DIR, "data", "feedback")

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(INTER_DIR, exist_ok=True)
os.makedirs(STORE_ROOT, exist_ok=True)
os.makedirs(FEEDBACK_DIR, exist_ok=True)

# Path to feedback storage file
FEEDBACK_FILE = os.path.join(FEEDBACK_DIR, "feedback.json")

conversation_memory: Dict[str, List[Dict]] = {}
MAX_MEMORY_LENGTH = 20  # keep last 20 messages


# ----------------------------
# SQLite utilities & Auth helpers
# ----------------------------
from cryptography.fernet import Fernet
import base64
import hashlib

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    secret_key = os.getenv("SECRET_KEY", "your-secret-key-here-change-in-production")
    ENCRYPTION_KEY = base64.urlsafe_b64encode(
        hashlib.sha256(secret_key.encode()).digest()
    )
cipher_suite = Fernet(ENCRYPTION_KEY)


def encrypt_password(password: str) -> str:
    """Encrypt password before storing"""
    if not password:
        return ""
    return cipher_suite.encrypt(password.encode()).decode()


def decrypt_password(encrypted_password: str) -> str:
    """Decrypt password for execution"""
    if not encrypted_password:
        return ""
    try:
        return cipher_suite.decrypt(encrypted_password.encode()).decode()
    except Exception:
        return encrypted_password


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def user_has_project_access(user_id: str, project_id: int, user_role: str = "user", write_required: bool = False) -> bool:
    """Verify whether a user has read or write access to a project"""
    if user_role == "admin":
        return True
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT created_by FROM projects WHERE id=?", (project_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    if row[0] == user_id:
        conn.close()
        return True
    c.execute("SELECT role FROM user_project WHERE user_id=? AND project_id=?", (user_id, project_id))
    up_row = c.fetchone()
    conn.close()
    if not up_row:
        return False
    if write_required and up_row[0] not in ["admin", "editor", "user"]:
        return False
    return True


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,  -- UUID
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT DEFAULT 'user'  -- 'admin' or 'user'
    )
    """
    )

    # Optional: Queries table to track all queries
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS queries (
        id TEXT PRIMARY KEY,
        project_id INTEGER,
        query TEXT,
        answer TEXT,
        user_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        conversation_id TEXT,
        response_time FLOAT,
        attempts INTEGER DEFAULT 1,
        is_fallback BOOLEAN DEFAULT 0,
        precision1 REAL,
        precision3 REAL,
        precision5 REAL,
        claim_support REAL,
        static_exec REAL,
        query_coverage REAL,
        num_claims INTEGER,
        database_engine TEXT DEFAULT 'postgresql'
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS conversation_metadata (
        conversation_id TEXT PRIMARY KEY,
        project_id INTEGER,
        title TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects (id)
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by TEXT
    )
    """
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS user_project (
        user_id TEXT,
        project_id INTEGER,
        role TEXT DEFAULT 'user',
        PRIMARY KEY(user_id, project_id)
    )"""
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        name TEXT,
        path TEXT,
        type TEXT,
        status TEXT DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        uploaded_by TEXT
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS doc_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER,
        chunk_type TEXT,
        content TEXT,
        metadata TEXT
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS feedback (
        id TEXT PRIMARY KEY,
        project_id INTEGER,
        query TEXT,
        answer TEXT,
        contexts TEXT,
        feedback TEXT,
        comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        user_id TEXT,
        query_id TEXT
    )
    """
    )
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS jira_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        jira_url TEXT,
        username TEXT,
        api_token TEXT,
        is_default BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS jira_imported_issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        jira_issue_key TEXT,
        jira_issue_summary TEXT,
        jira_issue_description TEXT,
        jira_issue_type TEXT,
        jira_issue_status TEXT,
        jira_issue_assignee TEXT,
        jira_issue_attachments TEXT,  -- JSON array of attachment info
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects (id)
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS shared_conversations (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        project_id INTEGER NOT NULL,
        sharer_id TEXT NOT NULL,
        recipient_id TEXT NOT NULL,
        permission TEXT DEFAULT 'edit',  -- 'view' or 'edit'
        shared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sharer_id) REFERENCES users (id),
        FOREIGN KEY (recipient_id) REFERENCES users (id),
        FOREIGN KEY (project_id) REFERENCES projects (id)
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS database_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        name TEXT NOT NULL,
        db_type TEXT NOT NULL,
        host TEXT NOT NULL,
        port INTEGER,
        database_name TEXT,
        username TEXT,
        encrypted_password TEXT,
        ssl_enabled BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_default BOOLEAN DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    """
    )

    conn.commit()
    conn.close()


# ----------------------------
# Embedding model
# ----------------------------
embedding_model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")


# ----------------------------
# FAISS per-project cache
# ----------------------------
class ProjectIndex:
    def __init__(self, vectorstore: FAISS):
        self.vectorstore = vectorstore


project_cache: Dict[int, ProjectIndex] = {}


def project_dir(project_id: int) -> str:
    d = os.path.join(STORE_ROOT, str(project_id))
    os.makedirs(d, exist_ok=True)
    return d


def load_project_index(project_id: int) -> ProjectIndex:
    if project_id in project_cache:
        return project_cache[project_id]

    d = project_dir(project_id)
    vs_path = os.path.join(d, "faiss_store.pkl")
    vectorstore = None
    if os.path.exists(vs_path):
        try:
            with open(vs_path, "rb") as f:
                vectorstore = pickle.load(f)
        except Exception as e:
            print(f"Failed to load persisted vectorstore: {e}")
    pi = ProjectIndex(vectorstore=vectorstore)
    project_cache[project_id] = pi
    return pi


def persist_project_index(project_id: int):
    pi = project_cache.get(project_id)
    if not pi or not pi.vectorstore:
        return
    d = project_dir(project_id)
    try:
        with open(os.path.join(d, "faiss_store.pkl"), "wb") as f:
            pickle.dump(pi.vectorstore, f)
    except Exception as e:
        print(f"Error persisting vectorstore {project_id}: {e}")


# ----------------------------
# Model-Agnostic LLM setup
# ----------------------------
from llm_provider import get_llm_provider, generate_llm_response
llm_provider = get_llm_provider()


def redact_pii(text: str) -> str:
    """Simple regex-based PII redaction (email, phone, ID, etc)."""
    if not text.strip():
        return text

    # Common regex patterns for PII
    patterns = {
        "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "PHONE": r"""
            (?:(?:\+?\d{1,3}[\s\-]?)?       # Optional country code
            \(?\d{2,4}\)?[\s\-\.]?)?        # Optional area code
            \d{2,4}[\s\-\.]?\d{3,4}         # Main number
            [\s\-\.]?\d{0,4}                # Optional extension
            (?!\d*-\d*-\d*)                 # Negative lookahead for dates
        """,
        "ID": r"\b[A-Z]{2,3}\d{4,}\b",  # e.g., PAN/employee IDs
        "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    }

    redacted_text = text
    for label, pattern in patterns.items():
        redacted_text = re.sub(pattern, f"[{label}_REDACTED]", redacted_text)

    return redacted_text


def sanitize_user_query(query: str) -> str:

    # 1. Basic prompt injection check
    forbidden_phrases = [
        "ignore previous instructions",
        "forget your instructions",
        "bypass safety",
        "malicious",
        "--",  # comment injection
    ]
    for phrase in forbidden_phrases:
        query = re.sub(re.escape(phrase), "[REDACTED]", query, flags=re.IGNORECASE)

    # 2. Strip leading/trailing spaces
    return query.strip()


from unstructured.partition.auto import partition


# ----------------------------
# Load & split with Unstructured
# ----------------------------
def split_textual_document(filepath: str, max_chunk_size: int = 1000) -> List[Document]:
    """
    Improved chunking with smart grouping and relational metadata:
    - Groups related elements (table descriptions + tables)
    - Adds metadata linking for relational queries
    - Maintains clean, focused chunks
    - Preserves hierarchy and context
    """
    elements = partition(filename=filepath)
    documents = []

    hierarchy_stack = []  # Track active heading levels
    current_group = []  # Group related elements together
    chunk_counter = 0

    def flush_group():
        """Flush current group as one or more documents with relational metadata."""
        nonlocal current_group, chunk_counter
        if not current_group:
            return

        # Group 1: Table descriptions with their tables
        grouped_elements = []
        temp_group = []

        for el in current_group:
            if (
                el.category == "Table"
                and temp_group
                and temp_group[-1].category in ["UncategorizedText", "NarrativeText"]
            ):
                # Table follows a description - group them
                temp_group.append(el)
            else:
                if temp_group:
                    grouped_elements.append(temp_group)
                temp_group = [el]
        if temp_group:
            grouped_elements.append(temp_group)

        # Create documents for each group
        for group in grouped_elements:
            if len(group) == 1:
                # Single element - create individual chunk
                el = group[0]
                text = el.text.strip()
                if not text:
                    continue

                # Split large elements but maintain context
                if len(text) > max_chunk_size:
                    chunks = [
                        text[i : i + max_chunk_size]
                        for i in range(0, len(text), max_chunk_size)
                    ]
                    for idx, chunk in enumerate(chunks):
                        documents.append(
                            create_document(
                                chunk, el, hierarchy_stack, filepath, chunk_counter
                            )
                        )
                        chunk_counter += 1
                else:
                    documents.append(
                        create_document(
                            text, el, hierarchy_stack, filepath, chunk_counter
                        )
                    )
                    chunk_counter += 1
            else:
                # Multiple related elements - combine if they fit
                combined_text = "\n".join(
                    [el.text.strip() for el in group if el.text.strip()]
                )
                if len(combined_text) <= max_chunk_size:
                    # Combine into one chunk
                    primary_el = group[0]
                    documents.append(
                        create_document(
                            combined_text,
                            primary_el,
                            hierarchy_stack,
                            filepath,
                            chunk_counter,
                            related_elements=len(group),
                            element_types=[el.category for el in group],
                        )
                    )
                    chunk_counter += 1
                else:
                    # Keep separate but add linking metadata
                    for i, el in enumerate(group):
                        text = el.text.strip()
                        if not text:
                            continue

                        doc = create_document(
                            text,
                            el,
                            hierarchy_stack,
                            filepath,
                            chunk_counter,
                            group_id=chunk_counter
                            // len(group),  # Same group ID for related chunks
                            element_index=i,
                            total_elements=len(group),
                        )
                        documents.append(doc)
                        chunk_counter += 1

        current_group = []

    def create_document(
        content, element, hierarchy, filepath, chunk_id, **extra_metadata
    ):
        """Helper to create Document with consistent metadata."""
        metadata = {
            "category": element.category,
            "filename": Path(filepath).name,
            "hierarchy": hierarchy.copy(),
            "parent_heading": hierarchy[-1] if hierarchy else None,
            "chunk_id": chunk_id,
            "element_id": id(element),
        }
        metadata.update(extra_metadata)
        redacted_content = redact_pii(content)
        return Document(page_content=redacted_content, metadata=metadata)

    # Process elements
    for el in elements:
        if not el.text.strip():
            continue

        if el.category == "Title":
            # Flush current group before new heading
            flush_group()
            if hierarchy_stack:
                hierarchy_stack.pop()
            hierarchy_stack.append(el.text.strip())

            # Title gets its own chunk
            current_group = [el]
            flush_group()
        else:
            current_group.append(el)

    # Flush final group
    flush_group()

    # Add sequential linking metadata for navigation
    add_sequential_links(documents)

    return documents


def add_sequential_links(documents):
    """Add metadata to link sequential chunks for better relational retrieval."""
    for i in range(len(documents)):
        if i > 0:
            # Link to previous chunk
            prev_id = documents[i - 1].metadata.get("chunk_id")
            if prev_id is not None:
                documents[i].metadata["prev_chunk_id"] = prev_id
        if i < len(documents) - 1:
            # Link to next chunk
            next_id = documents[i + 1].metadata.get("chunk_id")
            if next_id is not None:
                documents[i].metadata["next_chunk_id"] = next_id

        # Add section context for relational queries
        if "group_id" in documents[i].metadata:
            documents[i].metadata["section_type"] = "related_group"


def split_tabular_document(filepath: str, batch_size: int = 50):
    """Parse CSV/XLSX into schema + row batches for embedding."""
    ext = Path(filepath).suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(filepath)
        sheets = {"Sheet1": df}
    else:  # Excel
        xls = pd.ExcelFile(filepath)
        sheets = {name: xls.parse(name) for name in xls.sheet_names}

    documents = []
    filename = Path(filepath).name
    for sheet, df in sheets.items():
        # Schema / header info
        documents.append(
            Document(
                page_content=f"Sheet: {sheet}\nColumns: {', '.join(df.columns)}",
                metadata={"category": "schema", "sheet": sheet, "filename": filename},
            )
        )

        # Row batches
        for start in range(0, len(df), batch_size):
            batch = df.iloc[start : start + batch_size]
            batch_text = f"Sheet: {sheet}\nRows {start}-{start+len(batch)-1}:\n{batch.to_csv(index=False)}"

            redacted_content = redact_pii(batch_text)

            documents.append(
                Document(
                    page_content=redacted_content,
                    metadata={
                        "category": "rows",
                        "sheet": sheet,
                        "batch": f"{start}-{start+len(batch)-1}",
                        "filename": filename,
                    },
                )
            )
    return documents


def split_pdf_document(filepath: str, max_chunk_size: int = 1000) -> List[Document]:
    """
    Extract text from PDF using PyMuPDF and split into Document chunks
    using same logic as split_textual_document.
    """

    # Step 1: Extract text from PDF
    elements = []
    try:
        with fitz.open(filepath) as pdf_doc:
            for page_num, page in enumerate(pdf_doc):
                text = page.get_text().strip()
                if text:
                    # Treat each page as an element
                    elements.append(
                        type("Element", (), {"text": text, "category": "Page"})
                    )
    except Exception as e:
        raise RuntimeError(f"Failed to extract text from PDF: {e}")

    # Step 2: Split like textual document
    documents = []
    hierarchy_stack = []
    current_group = []
    chunk_counter = 0

    def flush_group():
        nonlocal current_group, chunk_counter
        if not current_group:
            return
        grouped_elements = [current_group]  # simple grouping for PDF pages
        for group in grouped_elements:
            combined_text = "\n".join(
                [el.text.strip() for el in group if el.text.strip()]
            )
            if len(combined_text) <= max_chunk_size:
                documents.append(
                    create_document(
                        combined_text,
                        group[0],
                        hierarchy_stack,
                        filepath,
                        chunk_counter,
                    )
                )
                chunk_counter += 1
            else:
                # Split large chunks
                text = combined_text
                chunks = [
                    text[i : i + max_chunk_size]
                    for i in range(0, len(text), max_chunk_size)
                ]
                for chunk in chunks:
                    documents.append(
                        create_document(
                            chunk, group[0], hierarchy_stack, filepath, chunk_counter
                        )
                    )
                    chunk_counter += 1
        current_group.clear()

    def create_document(
        content, element, hierarchy, filepath, chunk_id, **extra_metadata
    ):
        metadata = {
            "category": element.category,
            "filename": Path(filepath).name,
            "hierarchy": hierarchy.copy(),
            "parent_heading": hierarchy[-1] if hierarchy else None,
            "chunk_id": chunk_id,
        }
        metadata.update(extra_metadata)
        redacted_content = redact_pii(content)
        return Document(page_content=redacted_content, metadata=metadata)

    # Add all pages to current group
    current_group = elements
    flush_group()
    add_sequential_links(documents)

    return documents


def load_and_split_document(filepath: str):
    """Dispatcher: pick the right splitting strategy based on file type."""
    ext = Path(filepath).suffix.lower()
    if ext in [".docx", ".doc", ".txt"]:
        return split_textual_document(filepath)
    elif ext == ".pdf":
        return split_pdf_document(filepath)
    elif ext in [".csv", ".xlsx"]:
        return split_tabular_document(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ----------------------------
# Save and index documents
# ----------------------------
def save_and_index(
    project_id: int, doc_id: int, doc_name: str, filepath: str, doc_type: str, documents
):
    pi = load_project_index(project_id)
    conn_thread = get_connection()
    c_thread = conn_thread.cursor()
    try:
        c_thread.execute(
            "UPDATE documents SET status=? WHERE id=?", ("indexing", doc_id)
        )
        if not pi.vectorstore:
            pi.vectorstore = FAISS.from_documents(documents, embedding_model)
        else:
            pi.vectorstore.add_documents(documents)
        for idx, doc in enumerate(documents, start=1):
            c_thread.execute(
                "INSERT INTO doc_chunks (doc_id, chunk_type, content, metadata) VALUES (?, ?, ?, ?)",
                (
                    doc_id,
                    doc.metadata.get("category") or "text_chunk",
                    doc.page_content,
                    json.dumps(doc.metadata, ensure_ascii=False),
                ),
            )
            progress_percent = int((idx / len(documents)) * 100)
            c_thread.execute(
                "UPDATE documents SET progress=? WHERE id=?", (progress_percent, doc_id)
            )
            if idx % 20 == 0 or idx == len(documents):
                conn_thread.commit()
        c_thread.execute(
            "UPDATE documents SET status=?, progress=? WHERE id=?",
            ("completed", 100, doc_id),
        )
        conn_thread.commit()
        persist_project_index(project_id)
    except Exception as e:
        print(f"Indexing error for doc {doc_id} in project {project_id}: {e}")
        c_thread.execute("UPDATE documents SET status=? WHERE id=?", ("error", doc_id))
        conn_thread.commit()
    finally:
        conn_thread.close()


# ----------------------------
# Rebuild FAISS after deletion
# ----------------------------
def rebuild_project_index_from_store(project_id: int):
    pi = load_project_index(project_id)
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
SELECT content, metadata 
FROM doc_chunks 
WHERE doc_id IN (SELECT id FROM documents WHERE project_id=?)
""",
        (project_id,),
    )
    rows = c.fetchall()
    conn.close()
    if rows:
        documents = [
            Document(page_content=r[0], metadata=json.loads(r[1] or "{}")) for r in rows
        ]
        pi.vectorstore = FAISS.from_documents(documents, embedding_model)
        persist_project_index(project_id)
    else:
        pi.vectorstore = None
        d = project_dir(project_id)
        vs_path = os.path.join(d, "faiss_store.pkl")
        if os.path.exists(vs_path):
            try:
                os.remove(vs_path)
            except Exception:
                pass


# ----------------------------
# FastAPI app
# ----------------------------

from fastapi.middleware.cors import CORSMiddleware

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://localhost:8000",
]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)


# ----------------------------
# API models
# ----------------------------
class ProjectRequest(BaseModel):
    project_name: str


class QueryRequest(BaseModel):
    project_id: int
    query: str
    top_k: int = 5


# ----------------------------
# Project endpoints
# ----------------------------
@app.post("/project")
def create_or_get_project(
    req: ProjectRequest, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM projects WHERE name=?", (req.project_name,))
    row = c.fetchone()
    if row:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="❌ Project name already exists. Please choose a different name.",
        )

    c.execute(
        "INSERT INTO projects (name, created_by) VALUES (?, ?)",
        (req.project_name, user_id),
    )
    pid = c.lastrowid
    conn.commit()
    # Get the user's role from users table
    c.execute("SELECT role FROM users WHERE id=?", (user_id,))
    user_role_row = c.fetchone()
    user_role = user_role_row[0] if user_role_row else "user"

    # Add entry to user_project table
    c.execute(
        "INSERT OR IGNORE INTO user_project (user_id, project_id, role) VALUES (?, ?, ?)",
        (user_id, pid, user_role),
    )

    c.execute("SELECT id FROM users WHERE role='admin'")
    admin_rows = c.fetchall()

    for admin in admin_rows:
        admin_id = admin[0]
        c.execute(
            "INSERT OR IGNORE INTO user_project (user_id, project_id, role) VALUES (?, ?, ?)",
            (admin_id, pid, "admin"),
        )

    conn.commit()
    conn.close()
    load_project_index(pid)
    return {"project_id": pid, "project_name": req.project_name, "user_id": user_id}


@app.get("/projects")
def list_projects(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    conn = get_connection()
    c = conn.cursor()

    if current_user["role"] == "admin":
        # Admins see all projects with creator info
        c.execute(
            """
            SELECT p.id, p.name, p.created_at, p.created_by, 
                   u.username as created_by_username,
                   COALESCE(up.role, 'admin') as access_level
            FROM projects p
            LEFT JOIN users u ON p.created_by = u.id
            LEFT JOIN user_project up ON p.id = up.project_id AND up.user_id = ?
            ORDER BY p.created_at DESC
        """,
            (user_id,),
        )
    else:
        # Regular users see projects they have access to
        c.execute(
            """
            SELECT p.id, p.name, p.created_at, p.created_by,
                   creator.username as created_by_username,
                   up.role as access_level
            FROM projects p
            JOIN user_project up ON p.id = up.project_id
            LEFT JOIN users creator ON p.created_by = creator.id
            WHERE up.user_id = ?
            ORDER BY p.created_at DESC
        """,
            (user_id,),
        )

    rows = [
        {
            "project_id": r[0],
            "project_name": r[1],
            "created_at": r[2],
            "created_by": r[3],
            "created_by_username": r[4],
            "access_level": r[5],  # 'admin' or 'user'
        }
        for r in c.fetchall()
    ]

    conn.close()
    return rows


# ----------------------------
# Delete Project Endpoint
# ----------------------------
# ----------------------------
# Delete Project Endpoint - FIXED VERSION
# ----------------------------
@app.delete("/project/{project_id}")
async def delete_project(
    project_id: int, current_user: dict = Depends(get_current_user)
):
    """
    Delete a project and all associated data.
    Only project creator, admin users, or users with admin access can delete.
    """
    user_id = current_user["user_id"]
    conn = get_connection()
    c = conn.cursor()

    try:
        print(f"🔍 Attempting to delete project {project_id} by user {user_id}")

        # Check if user has permission to delete this project
        c.execute(
            """
            SELECT p.id, p.name, p.created_by, up.role 
            FROM projects p
            LEFT JOIN user_project up ON p.id = up.project_id AND up.user_id = ?
            WHERE p.id = ?
            """,
            (user_id, project_id),
        )

        project = c.fetchone()
        if not project:
            conn.close()
            print(f"❌ Project {project_id} not found")
            raise HTTPException(status_code=404, detail="Project not found")

        project_db_id, project_name, created_by, user_project_role = project
        print(
            f"📋 Project details: name={project_name}, created_by={created_by}, user_role={user_project_role}"
        )

        # Check permissions
        can_delete = (
            created_by == user_id  # Project creator
            or user_project_role == "admin"  # Has admin role in project
            or current_user["role"] == "admin"  # Global admin
        )

        print(f"🔐 Permission check: can_delete={can_delete}")

        if not can_delete:
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="You don't have permission to delete this project",
            )

        # Get all document paths before deletion
        c.execute("SELECT path FROM documents WHERE project_id = ?", (project_id,))
        document_paths = [row[0] for row in c.fetchall() if row[0]]
        print(f"📁 Found {len(document_paths)} document files to delete")

        # Disable foreign key checks
        c.execute("PRAGMA foreign_keys = OFF")
        print("🔓 Foreign key checks disabled")

        # Delete in correct order (from child tables to parent)

        # 1. Delete doc_chunks (depends on documents)
        c.execute(
            "DELETE FROM doc_chunks WHERE doc_id IN (SELECT id FROM documents WHERE project_id = ?)",
            (project_id,),
        )
        print(f"🗑️ Deleted {c.rowcount} records from doc_chunks")

        # 2. Delete documents
        c.execute("DELETE FROM documents WHERE project_id = ?", (project_id,))
        print(f"🗑️ Deleted {c.rowcount} records from documents")

        # 3. Delete shared_conversations
        c.execute(
            "DELETE FROM shared_conversations WHERE project_id = ?", (project_id,)
        )
        print(f"🗑️ Deleted {c.rowcount} records from shared_conversations")

        # 4. Delete jira_imported_issues
        c.execute(
            "DELETE FROM jira_imported_issues WHERE project_id = ?", (project_id,)
        )
        print(f"🗑️ Deleted {c.rowcount} records from jira_imported_issues")

        # 5. Delete queries
        c.execute("DELETE FROM queries WHERE project_id = ?", (project_id,))
        print(f"🗑️ Deleted {c.rowcount} records from queries")

        # 6. Delete conversation_metadata
        c.execute(
            "DELETE FROM conversation_metadata WHERE project_id = ?", (project_id,)
        )
        print(f"🗑️ Deleted {c.rowcount} records from conversation_metadata")

        # 7. Delete user_project
        c.execute("DELETE FROM user_project WHERE project_id = ?", (project_id,))
        print(f"🗑️ Deleted {c.rowcount} records from user_project")

        # 8. Delete feedback
        c.execute("DELETE FROM feedback WHERE project_id = ?", (project_id,))
        print(f"🗑️ Deleted {c.rowcount} records from feedback")

        # 9. Finally delete the project itself
        c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        print(f"🗑️ Deleted {c.rowcount} records from projects")

        # Commit the transaction
        conn.commit()
        print("✅ Transaction committed successfully")

        # Re-enable foreign key checks
        c.execute("PRAGMA foreign_keys = ON")
        print("🔒 Foreign key checks re-enabled")

        # Delete physical files after successful database deletion
        print("🗑️ Deleting physical files...")

        # Delete FAISS store directory
        project_store_dir = os.path.join(STORE_ROOT, str(project_id))
        if os.path.exists(project_store_dir):
            import shutil

            try:
                shutil.rmtree(project_store_dir)
                print(f"✅ Deleted FAISS directory: {project_store_dir}")
            except Exception as e:
                print(f"⚠️ Could not delete FAISS directory: {e}")

        # Delete raw project directory
        raw_project_dir = os.path.join(RAW_DIR, str(project_id))
        if os.path.exists(raw_project_dir):
            try:
                import shutil

                shutil.rmtree(raw_project_dir)
                print(f"✅ Deleted raw directory: {raw_project_dir}")
            except Exception as e:
                print(f"⚠️ Could not delete raw directory: {e}")

        # Delete document files
        for file_path in document_paths:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"✅ Deleted file: {file_path}")
                except Exception as e:
                    print(f"⚠️ Could not delete file {file_path}: {e}")

        # Remove from cache
        if project_id in project_cache:
            del project_cache[project_id]
            print("✅ Removed from cache")

        return {
            "status": "success",
            "message": f"Project '{project_name}' and all associated data deleted successfully",
        }

    except HTTPException:
        conn.rollback()
        print("❌ HTTP Exception, rolling back transaction")
        # Re-enable foreign key checks on error
        try:
            c.execute("PRAGMA foreign_keys = ON")
        except:
            pass
        raise
    except Exception as e:
        conn.rollback()
        print(f"❌ Error deleting project {project_id}: {e}")
        import traceback

        traceback.print_exc()
        # Re-enable foreign key checks on error
        try:
            c.execute("PRAGMA foreign_keys = ON")
        except:
            pass
        raise HTTPException(
            status_code=500, detail=f"Failed to delete project: {str(e)}"
        )
    finally:
        conn.close()
        print("🔚 Database connection closed")


@app.get("/project/{project_id}")
def project_details(project_id: int, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    if not user_has_project_access(user_id, project_id, current_user.get("role", "user")):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, path, type, status, progress FROM documents WHERE project_id=?",
        (project_id,),
    )
    docs = [
        {
            "doc_id": r[0],
            "doc_name": r[1],
            "path": r[2],
            "type": r[3],
            "status": r[4],
            "progress": r[5],
        }
        for r in c.fetchall()
    ]
    conn.close()
    return {"project_id": project_id, "documents": docs}


from pydantic import Field


class ShareProjectRequest(BaseModel):
    project_id: int
    shared_with_usernames: List[str] = Field(..., min_items=1)
    permission: Literal["user", "admin"] = "user"


@app.get("/project/{project_id}/shared-users")
def get_shared_users(project_id: int, current_user: dict = Depends(get_current_user)):
    """Get users who have access to this project"""
    user_id = current_user["user_id"]

    # Check if user has access to this project
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        """
        SELECT 1 FROM user_project 
        WHERE user_id = ? AND project_id = ?
    """,
        (user_id, project_id),
    )

    if not c.fetchone() and current_user["role"] != "admin":
        conn.close()
        raise HTTPException(
            status_code=403, detail="You don't have access to this project"
        )

    # Get shared users (excluding the current user)
    c.execute(
        """
        SELECT u.id, u.username, up.role
        FROM users u
        JOIN user_project up ON u.id = up.user_id
        WHERE up.project_id = ?
        AND u.id != ?
        ORDER BY u.username
    """,
        (project_id, user_id),
    )

    shared_users = [
        {"id": row[0], "username": row[1], "role": row[2]} for row in c.fetchall()
    ]

    conn.close()
    return {"users": shared_users}


@app.post("/project/share")
async def share_project(
    req: ShareProjectRequest, current_user: dict = Depends(get_current_user)
):
    """Share a project with other users"""
    user_id = current_user["user_id"]

    conn = get_connection()
    c = conn.cursor()

    try:
        # ... permission verification code ...

        # Get user IDs for the usernames
        placeholders = ",".join(["?"] * len(req.shared_with_usernames))
        c.execute(
            f"""
            SELECT id, username FROM users 
            WHERE username IN ({placeholders}) 
            AND id != ?
        """,
            (*req.shared_with_usernames, user_id),
        )

        users_to_share = c.fetchall()

        if not users_to_share:
            conn.close()
            raise HTTPException(
                status_code=404, detail="No valid users found to share with"
            )

        shared_count = 0

        for user_id_to_share, username in users_to_share:
            # Use INSERT OR REPLACE to always set the permission
            c.execute(
                """
                INSERT OR REPLACE INTO user_project (user_id, project_id, role)
                VALUES (?, ?, ?)
            """,
                (user_id_to_share, req.project_id, req.permission),
            )
            shared_count += 1

        conn.commit()
        conn.close()

        message = f"Project successfully shared with {shared_count} user(s)"

        return {"status": "success", "message": message, "shared_count": shared_count}

    except Exception as e:
        conn.rollback()
        print(f"ERROR in share_project: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ----------------------------
# Upload endpoint
# ----------------------------
@app.post("/upload")
async def upload_files(
    project_id: int = Query(...),
    files: List[UploadFile] = File(...),
    background_tasks: BackgroundTasks = None,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    if not user_has_project_access(user_id, project_id, current_user.get("role", "user"), write_required=True):
        return JSONResponse(
            status_code=403, content={"status": "error", "message": "Access denied to this project"}
        )

    conn_main = get_connection()
    c_main = conn_main.cursor()
    c_main.execute("SELECT id FROM projects WHERE id=?", (project_id,))
    if not c_main.fetchone():
        conn_main.close()
        return JSONResponse(
            status_code=404, content={"status": "error", "message": "Project not found"}
        )
    conn_main.close()

    results = []
    proj_dir = os.path.join(RAW_DIR, str(project_id))
    os.makedirs(proj_dir, exist_ok=True)

    for file in files:
        original_name = os.path.basename(file.filename or "uploaded_file")
        filename = original_name
        filepath = os.path.join(proj_dir, filename)
        # Automatically rename if file already exists
        counter = 1
        name, ext = os.path.splitext(original_name)
        while os.path.exists(filepath):
            filename = f"{name} ({counter}){ext}"
            filepath = os.path.join(proj_dir, filename)
            counter += 1

        # Normalize to absolute + forward slashes
        filepath = os.path.abspath(filepath).replace("\\", "/")

        try:
            with open(filepath, "wb") as f:
                shutil.copyfileobj(file.file, f)

            conn_thread = get_connection()
            c_thread = conn_thread.cursor()
            c_thread.execute(
                "INSERT INTO documents (project_id, name, path, type, status, uploaded_by) VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, filename, filepath, "pending", "pending", user_id),
            )
            doc_id = c_thread.lastrowid
            conn_thread.commit()
            conn_thread.close()

            documents = load_and_split_document(filepath)

            if background_tasks:
                background_tasks.add_task(
                    save_and_index,
                    project_id,
                    doc_id,
                    filename,
                    filepath,
                    "doc",
                    documents,
                )
            else:
                save_and_index(project_id, doc_id, filename, filepath, "doc", documents)

            results.append(
                {
                    "status": "processing",
                    "doc_id": doc_id,
                    "doc_name": filename,
                    "chunks": len(documents),
                }
            )

        except Exception as e:
            results.append({"status": "error", "doc_name": filename, "message": str(e)})

    return results


# ----------------------------
# Download/Open a document
# ----------------------------
@app.get("/project/{project_id}/documents/{doc_id}/download")
def download_document(
    project_id: int, doc_id: int, current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    c = conn.cursor()

    # First, verify the user has access to this project
    user_id = current_user["user_id"]
    if current_user["role"] != "admin":
        c.execute(
            """
            SELECT 1 FROM user_project 
            WHERE user_id = ? AND project_id = ?
        """,
            (user_id, project_id),
        )
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=403, detail="Access denied to this project")

    c.execute(
        "SELECT id, name, path FROM documents WHERE id=? AND project_id=?",
        (doc_id, project_id),
    )
    r = c.fetchone()
    if not r:
        conn.close()
        raise HTTPException(status_code=404, detail="Document not found in project")

    doc_id, doc_name, file_path = r
    conn.close()

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    # Return the file for download
    return FileResponse(
        path=file_path, filename=doc_name, media_type="application/octet-stream"
    )


# ----------------------------
# Status endpoint
# ----------------------------
@app.get("/status/{doc_id}")
async def check_status(doc_id: int, current_user: dict = Depends(get_current_user)):
    conn_thread = get_connection()
    c_thread = conn_thread.cursor()
    c_thread.execute("SELECT status, progress, project_id FROM documents WHERE id=?", (doc_id,))
    row = c_thread.fetchone()
    conn_thread.close()
    if row:
        if not user_has_project_access(current_user["user_id"], row[2], current_user.get("role", "user")):
            raise HTTPException(status_code=403, detail="Access denied to this document")
        return {"doc_id": doc_id, "status": row[0], "progress": row[1]}
    else:
        return {"doc_id": doc_id, "status": "not_found", "progress": 0}


# ----------------------------
# List documents for a project
# ----------------------------
@app.get("/project/{project_id}/documents")
def list_project_documents(project_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_project_access(current_user["user_id"], project_id, current_user.get("role", "user")):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, path, type, status, progress FROM documents WHERE project_id=?",
        (project_id,),
    )
    items = [
        {
            "doc_id": r[0],
            "doc_name": r[1],
            "path": r[2],
            "type": r[3],
            "status": r[4],
            "progress": r[5],
        }
        for r in c.fetchall()
    ]
    conn.close()
    return {"project_id": project_id, "documents": items}


# ----------------------------
# Delete a document
# ----------------------------
@app.delete("/project/{project_id}/documents/{doc_id}")
def delete_document(project_id: int, doc_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_project_access(current_user["user_id"], project_id, current_user.get("role", "user"), write_required=True):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, path FROM documents WHERE id=? AND project_id=?",
        (doc_id, project_id),
    )
    r = c.fetchone()
    if not r:
        conn.close()
        raise HTTPException(status_code=404, detail="Document not found in project")

    try:
        c.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(
            status_code=500, detail=f"Failed to delete document rows: {e}"
        )
    conn.close()

    try:
        if os.path.exists(r[1]):
            os.remove(r[1])
    except Exception:
        pass

    if project_id in project_cache:
        del project_cache[project_id]

    d = project_dir(project_id)
    vs_path = os.path.join(d, "faiss_store.pkl")
    if os.path.exists(vs_path):
        os.remove(vs_path)

    rebuild_project_index_from_store(project_id)
    return {"status": "deleted", "doc_id": doc_id}


def load_data():
    """
    Load data from SQLite database for analytics dashboard
    """
    conn = sqlite3.connect(DB_PATH)
    queries = pd.read_sql("SELECT * FROM queries", conn)
    feedback = pd.read_sql("SELECT * FROM feedback", conn)
    projects = pd.read_sql("SELECT * FROM projects", conn)
    documents = pd.read_sql("SELECT * FROM documents", conn)
    users = pd.read_sql("SELECT * FROM users", conn)
    conn.close()
    return queries, feedback, projects, documents, users


# ----------------------------
# GEMINI LLM
# ----------------------------
from google.genai import types
from google.genai.types import (
    HttpOptions,
    Tool,
    ToolCodeExecution,
    GenerateContentConfig,
)


def call_gemini(
    prompt: str, retries: int = 3, base_delay: int = 2, stream: bool = False
):
    """
    Call Model-Agnostic LLM with fallback models and error recovery.
    """
    return generate_llm_response(prompt=prompt, temperature=0.3, retries=retries, base_delay=base_delay)


def call_llm(prompt: str, retries: int = 3, base_delay: int = 2) -> str:
    """
    Simple synchronous LLM API call for planning and retrieval agents.
    Returns only the text response.
    """
    text, _, _ = generate_llm_response(prompt=prompt, temperature=0.1, retries=retries, base_delay=base_delay)
    return text


def get_project_documents(project_id: int) -> list:
    """Return a list of document names for the given project_id."""
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT name FROM documents WHERE project_id=?", (project_id,))
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows] if rows else []
    except Exception as e:
        print(f"⚠️ Error fetching project documents: {e}")
        return []


# ---------------------------------------
# Document Detection in the User's Query
# ---------------------------------------

import re


def extract_doc_names_from_query(project_id: int, query: str) -> List[str]:
    """
    Detect document names mentioned in the user query with flexible matching.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT name FROM documents WHERE project_id=?", (project_id,))
    doc_names = [r[0] for r in c.fetchall()]
    conn.close()

    if not doc_names:
        return []

    # Normalize query for better matching
    query_lower = query.lower()

    mentioned = []
    for doc_name in doc_names:
        doc_name_lower = doc_name.lower()

        # Multiple matching strategies:
        # 1. Exact word match (original behavior)
        if re.search(rf"\b{re.escape(doc_name_lower)}\b", query_lower):
            mentioned.append(doc_name)
            continue

        # 2. Partial match (handles extensions, spaces, etc.)
        # Remove file extensions for matching
        doc_base = re.sub(r"\.(pdf|docx?|txt|csv|xlsx?)$", "", doc_name_lower)
        query_clean = re.sub(
            r"[^\w\s]", " ", query_lower
        )  # Replace punctuation with spaces

        if doc_base in query_clean.split():
            mentioned.append(doc_name)
            continue

        # 3. Handle cases like "sample.pdf" → "sample"
        if re.search(rf"\b{re.escape(doc_base)}\b", query_lower):
            mentioned.append(doc_name)

    print(f"🔍 Document extraction: query='{query}' → found={mentioned}")
    return mentioned


# ---------------------------------------------
# Retrieval based on Document in the User Query
# ---------------------------------------------


def retrieve_from_doc_by_name(project_id: int, doc_name: str) -> List[Document]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id FROM documents WHERE project_id=? AND LOWER(name)=?",
        (project_id, doc_name.lower()),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return []
    doc_id = row[0]
    c.execute("SELECT content, metadata FROM doc_chunks WHERE doc_id=?", (doc_id,))
    rows = c.fetchall()
    conn.close()

    docs = []
    for content, metadata_json in rows:
        metadata = json.loads(metadata_json) if metadata_json else {}
        docs.append(Document(page_content=content, metadata=metadata))
    return docs


# ----------------------------
# Retrieval based on User's Query
# ----------------------------
from rank_bm25 import BM25Okapi
import numpy as np
from typing import List


def retrieve_relevant_chunks(
    project_id: int,
    query: str,
    keyword_terms: List[str],
    threshold: float = 0.7,
    max_candidates: int = 30,
    alpha: float = 0.5,
) -> List[Document]:
    """
    Hybrid retrieval using FAISS (semantic) + BM25 (keyword) with relational awareness.
    """

    def normalize_key(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip()).lower()

    pi = load_project_index(project_id)
    if not pi.vectorstore:
        return []

    # Step 1: Embed query for FAISS
    query_embedding = pi.vectorstore.embedding_function.embed_query(query)
    query_embedding = np.array([query_embedding]).astype("float32")

    # Step 2: FAISS retrieval
    faiss_index = pi.vectorstore.index
    scores, indices = faiss_index.search(query_embedding, max_candidates)

    faiss_docs = []
    faiss_scores = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        doc_id = pi.vectorstore.index_to_docstore_id[idx]
        doc = pi.vectorstore.docstore.search(doc_id)
        if not doc or not getattr(doc, "page_content", "").strip():
            continue
        faiss_docs.append(doc)
        faiss_scores.append(score)

    # Step 3: BM25 retrieval
    all_docs = [
        d
        for d in pi.vectorstore.docstore._dict.values()
        if getattr(d, "page_content", "").strip()
    ]
    if keyword_terms:
        # Use only the extracted keywords for BM25
        keyword_query = " ".join(keyword_terms)
        print(f"🔍 BM25 using ONLY keywords: {keyword_terms}")

        tokenized_corpus = [d.page_content.split() for d in all_docs]
        bm25 = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
        bm25_scores = (
            bm25.get_scores(keyword_query.split()) if bm25 else [0] * len(all_docs)
        )
    else:
        tokenized_corpus = [d.page_content.split() for d in all_docs]
        bm25 = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
        bm25_scores = bm25.get_scores(query.split()) if bm25 else [0] * len(all_docs)

    # Step 4: Normalize scores
    def normalize(arr):
        arr = np.array(arr)
        if arr.max() == arr.min():
            return np.ones_like(arr)
        return (arr - arr.min()) / (arr.max() - arr.min())

    faiss_scores_norm = (
        normalize(np.array(faiss_scores)) if faiss_scores else np.array([])
    )
    bm25_scores_norm = (
        normalize(np.array(bm25_scores))
        if bm25_scores is not None and bm25_scores.size > 0
        else np.array([])
    )

    # Step 5: Fuse scores with relational awareness
    final_scores = {}

    def add_doc(doc, score):
        key = normalize_key(doc.page_content)
        if key in final_scores:
            final_scores[key] += score
        else:
            final_scores[key] = score

    # FAISS top docs
    for doc, f_score in zip(faiss_docs, faiss_scores_norm):
        add_doc(doc, alpha * f_score)

        # Add related group chunks
        if "group_id" in doc.metadata:
            group_id = doc.metadata["group_id"]
            for d in all_docs:
                if (
                    d.metadata.get("group_id") == group_id
                    and d.page_content != doc.page_content
                ):
                    add_doc(d, alpha * f_score * 0.8)

        # Add sequential neighbors
        prev_id = doc.metadata.get("prev_chunk_id")
        next_id = doc.metadata.get("next_chunk_id")
        for neighbor in all_docs:
            if neighbor.metadata.get("chunk_id") in [prev_id, next_id]:
                add_doc(neighbor, alpha * f_score * 0.6)

    # BM25 scores
    for doc, b_score in zip(all_docs, bm25_scores_norm):
        add_doc(doc, (1 - alpha) * b_score)

    # Step 6: Rank & Deduplicate
    ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)

    unique_docs, seen = [], set()
    for content, _ in ranked:
        key = normalize_key(content)
        if key not in seen:
            for d in all_docs:
                if normalize_key(d.page_content) == key:
                    unique_docs.append(d)
                    break
            seen.add(key)

    return unique_docs[:max_candidates]


def get_sqlglot_dialect(database_engine):
    """Map database engine names to sqlglot dialect names."""
    dialect_map = {
        "postgresql": "postgres",
        "postgres": "postgres",
        "mysql": "mysql",
        "mariadb": "mysql",
        "sqlserver": "tsql",
        "mssql": "tsql",
        "tsql": "tsql",
        "oracle": "oracle",
        "snowflake": "snowflake",
        "bigquery": "bigquery",
        "sqlite": "sqlite",
        "redshift": "redshift",
        "hive": "hive",
        "presto": "presto",
        "spark": "spark",
        "clickhouse": "clickhouse",
        "databricks": "databricks",
        "trino": "trino",
        "duckdb": "duckdb",
    }
    return dialect_map.get(database_engine.lower(), "postgres")


def evaluate_query_rag(
    project_id, user_query, sql_gen, docs, database_engine="postgresql"
):
    import sqlglot
    import re

    # Ensure docs is a list
    if not isinstance(docs, list):
        docs = [docs]

    # Normalize all docs to dicts with 'content'
    normalized_docs = []
    for d in docs:
        if hasattr(d, "page_content"):
            normalized_docs.append({"content": d.page_content})
        elif isinstance(d, dict):
            if "page_content" in d:
                normalized_docs.append({"content": d["page_content"]})
            elif "content" in d:
                normalized_docs.append({"content": d["content"]})
            else:
                normalized_docs.append({"content": str(d)})
        elif isinstance(d, str):
            normalized_docs.append({"content": d})
        else:
            normalized_docs.append({"content": str(d)})

    if not normalized_docs:
        normalized_docs = [{"content": ""}]

    def sanitize_sql_block(sql_text: str) -> str:
        """
        Remove unsafe lines that break sqlglot parsing: SQL comments (-- ...), Oracle-style "/" delimiters, Placeholder ellipses, Empty lines
        """
        cleaned_lines = []
        for line in sql_text.splitlines():
            s = line.strip()

            # Skip comments
            if s.startswith("--"):
                continue

            # Skip Oracle delimiters or placeholders
            if s == "/" or "..." in s:
                continue

            # Skip empty lines
            if not s:
                continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    # --- Extract SQL from sql blocks ---
    def extract_sql_blocks(text):
        pattern = r"```sql(.*?)```"
        blocks = re.findall(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        if not blocks:
            sql_keywords = r"(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP).*?;"
            blocks = re.findall(sql_keywords, text, flags=re.DOTALL | re.IGNORECASE)
        return [sanitize_sql_block(b) for b in blocks if sanitize_sql_block(b).strip()]

    # --- Extract SQL claims ---
    def extract_sql_claims(sql_text):
        import re
        import sqlglot
        from sqlglot.expressions import Table, Column, Join

        claims = []
        trees = []

        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]

        for stmt in statements:
            try:
                dialect = get_sqlglot_dialect(database_engine)
                parsed_trees = sqlglot.parse(
                    stmt, dialect=dialect
                )  # Use appropriate dialect

                if not parsed_trees:
                    claims.append(f"parse_error:{database_engine}:No valid SQL parsed")
                    continue
            except Exception as e:
                # ✅ SYNTAX ERROR
                claims.append(f"parse_error:{database_engine}:{e}")
                continue

            for tree in parsed_trees:
                if not tree:
                    continue
                trees.append(tree)

                for t in tree.find_all(Table):
                    if t.name:
                        claims.append(f"table:{t.name.lower()}")

                for c in tree.find_all(Column):
                    if c.name:
                        col_name = c.name.lower().split(".")[-1]
                        claims.append(f"column:{col_name}")

                for j in tree.find_all(Join):
                    try:
                        on_expr = j.on() if callable(j.on) else j.on
                        if on_expr:
                            join_clause = re.sub(
                                r"\s+", " ", on_expr.sql().lower()
                            ).strip()
                            claims.append(f"join:{join_clause}")
                    except Exception:
                        continue

        return claims, trees

    # --- Combine all SQL blocks ---
    sql_blocks = extract_sql_blocks(sql_gen)
    if not sql_blocks:
        print("[RAG DEBUG] No SQL blocks found in answer.")
    else:
        print("[RAG DEBUG] SQL Blocks Found:", sql_blocks)
    all_claims = []
    all_trees = []
    for block in sql_blocks:
        claims, trees = extract_sql_claims(block)
        all_claims.extend(claims)
        all_trees.extend(trees)

    # --- Precision @ K ---
    def precision_at_k(normalized_docs, claims, k=5):
        if not claims or not normalized_docs:
            return 0.0
        claim_names = [c.split(":", 1)[-1].lower() for c in claims if ":" in c]
        relevant_count = 0
        top_docs = normalized_docs[:k]
        for d in top_docs:
            content_lower = d["content"].lower()
            if any(cn in content_lower for cn in claim_names):
                relevant_count += 1
        return relevant_count / k

    # --- Debug ---
    print("Top 3 chunk texts:", [d["content"][:100] for d in normalized_docs[:3]])
    print("Extracted SQL claims:", all_claims)

    if all_claims and all_trees:
        precision1 = precision_at_k(normalized_docs, all_claims, 1)
        precision3 = precision_at_k(normalized_docs, all_claims, 3)
        precision5 = precision_at_k(normalized_docs, all_claims, 5)
    else:
        precision1 = precision3 = precision5 = None

    return {
        "Precision@1": precision1,
        "Precision@3": precision3,
        "Precision@5": precision5,
        "NumClaims": len(all_claims),
        "claims": all_claims,
    }


# ----------------------------
# State Definition for LangGraph
# ----------------------------
class AgentState(TypedDict, total=False):
    project_id: int
    user_query: str
    conversation_id: str
    user_id: str
    memory: List[Dict[str, str]]
    database_engine: str
    query_intent: str
    retrieval_mode: str
    required_documents: List[str]
    next_steps: List[str]
    keyword_search_words: List[str]
    contexts: List[Dict]
    generated_response: str
    attempt_count: int
    is_fallback: bool
    current_step: str
    response_type: str
    generation_prompt: str
    rag_metrics: Dict[str, Any]
    validation_result: str
    needs_improvement: bool
    retry_target: str  # "retrieval", "generation", or "none"
    improvement_suggestions: List[str]
    reflection_count: int
    stream_chunks: Optional[Any]
    current_status: str
    sql_complexity_score: float  # 0-100 score
    complexity_reasons: List[str]  # List of complexity issues
    needs_simplification: bool  # Whether simplification is needed


# ----------------------------
# Agent Functions
# ----------------------------
def planning_agent(state: AgentState) -> AgentState:
    """Analyze user query and plan the approach."""
    print("🔍 Planning Agent: Analyzing query intent...")
    prompt = f"""
    ## ROLE
    You analyze the user query and decide how it should be handled.

    ## INPUT
    **USER QUERY:** {state['user_query']}
    **CONVERSATION HISTORY:** {state['memory'][-20:] if state['memory'] else 'No history'}

    ## TASK
    Classify the query intent and decide the next step.

    ## INTENT RULES
    - **sql_query**
    - Any data request, report, list, count, analysis, metric
    - Generate SQL queries to retrieve, validate, or analyze data
    - Mentions tables, columns, schemas, SQL, records, databases
    - Phrases like: "show", "list", "find", "get", "how many", "what is" (data-related)

    - **sql_optimization**
    - Mentions optimize, tune, performance, slow query, explain, analyze
    - AND includes or implies an existing SQL query

    - **conversational**
    - Greetings, thanks, casual chat, assistant-related questions only

    ## OUTPUT FORMAT (JSON ONLY)

    ### Conversational
    {{
    "query_intent": "conversational",
    "next_steps": ["generation"]
    }}

    ### SQL Query
    {{
    "query_intent": "sql_query",
    "next_steps": ["retrieval"]
    }}

    ### SQL Optimization
    {{
    "query_intent": "sql_optimization",
    "next_steps": ["optimization"]
    }}

    Return ONLY valid JSON. No extra text.


    """

    try:
        # Use non-streaming call for planning
        result = call_llm(prompt)

        # Handle the return format properly
        if isinstance(result, tuple) and len(result) == 3:
            response_text = result[
                0
            ]  # Extract the text from tuple (text, attempt_count, is_fallback)
        else:
            # Fallback if call_gemini returns something unexpected
            response_text = (
                str(result)
                if result
                else '{"query_intent": "unknown", "next_steps": ["retrieval"]}'
            )

        # Clean the response - remove any markdown formatting
        response_text = response_text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()

        try:
            planning_data = json.loads(response_text)
        except json.JSONDecodeError:
            # Extract JSON from malformed response or use fallback
            import re

            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                planning_data = json.loads(json_match.group())
            else:
                planning_data = {"query_intent": "unknown"}

        state.update(
            {
                "query_intent": planning_data.get("query_intent", "unknown"),
                "next_steps": planning_data.get("next_steps", []),
                "current_step": "planning_complete",
            }
        )

        print(
            f"📋 Planning Result: {state['query_intent']}, Steps: {state['next_steps']}"
        )

    except Exception as e:
        print(f"❌ Planning agent error: {e}")
        print(
            f"Raw response was: {response_text if 'response_text' in locals() else 'No response'}"
        )

        # Default fallback based on query content
        user_query_lower = state["user_query"].lower()
        conversational_keywords = [
            "hello",
            "hi",
            "hey",
            "thanks",
            "thank you",
            "bye",
            "goodbye",
            "how are you",
        ]
        optimization_keywords = [
            "optimize",
            "optimise",
            "tune",
            "tuning",
            "slow",
            "performance",
            "make faster",
            "improve query",
            "explain plan",
            "explain analyze",
        ]

        if any(keyword in user_query_lower for keyword in conversational_keywords):
            state.update(
                {
                    "query_intent": "conversational",
                    "next_steps": ["generation"],
                    "current_step": "planning_complete",
                }
            )
        elif any(k in user_query_lower for k in optimization_keywords):
            state.update(
                {
                    "query_intent": "sql_optimization",
                    "next_steps": ["optimization"],
                    "current_step": "planning_complete",
                }
            )
        else:
            state.update(
                {
                    "query_intent": "sql_query",
                    "next_steps": ["retrieval"],
                    "current_step": "planning_complete",
                }
            )

        print(f"🔄 Using fallback planning: {state['query_intent']}")

    return state


# ----------------------------
# Retrieval Agent
# ----------------------------
def retrieval_agent(state: AgentState) -> AgentState:
    """Use LLM to analyze retrieval needs and fetch relevant documents."""
    print("🔍 Retrieval Agent: Analyzing retrieval needs...")

    # 🧩 Feedback-aware logic
    validation_feedback = state.get("rag_metrics", {}).get("LLMReasoning", "")
    validation_suggestions = state.get("improvement_suggestions", [])
    retry_target = state.get("retry_target", "none")
    reflection_count = state.get("reflection_count", 0)

    # Include validation insights if this is a retry after validation
    feedback_section = ""
    if retry_target == "retrieval" and (validation_feedback or validation_suggestions):
        feedback_section = f"""
        The previous validation step suggested retrieval improvement.
        REASONING: {validation_feedback}
        SUGGESTIONS: {', '.join(validation_suggestions) if validation_suggestions else 'None'}
        
        Based on this feedback, refine your retrieval analysis to better target the relevant information.
        """
        print("🧠 Applying validation feedback to refine retrieval...")

    prompt = f"""
    ## ROLE
    Determine what documents and content are needed to answer the query.

    ## INPUT
    **USER QUERY:** {state['user_query']}
    {feedback_section}

    ## TASK
    Decide retrieval strategy and extract search metadata.

    ## OUTPUT FORMAT (JSON ONLY)
    {{
    "retrieval_mode": "document_level | chunk_level",
    "required_documents": [],
    "keyword_search_words": []
    }}

    ## RULES

    ### RETRIEVAL MODE
    - **document_level**
    - Full document analysis
    - Explicit file names mentioned (pdf, csv, xlsx, docx, etc.)

    - **chunk_level**
    - Specific questions or partial analysis

    ### REQUIRED DOCUMENTS
    - Extract ONLY explicitly mentioned file names
    - Must match exact document names
    - If none mentioned → []

    ### KEYWORD SEARCH WORDS
    - Extract concrete nouns/entities only
    - Skip generic words (data, info, details)
    - Examples:
    - "sales report Q4" → ["sales", "Q4"]
    - "customer orders last month" → ["customers", "orders"]

    Return ONLY valid JSON. No extra text.

    """

    try:
        actual_doc_names = get_project_documents(state["project_id"])
        if not actual_doc_names:
            print("⚠️ No documents found in this project. Skipping retrieval.")

            state.update(
                {
                    "contexts": [],
                    "retrieval_mode": "none",
                    "required_documents": [],
                    "keyword_search_words": [],
                    "retrieval_metrics": {
                        "documents_found": 0,
                        "keywords_used": 0,
                        "chunks_retrieved": 0,
                    },
                    "current_step": "retrieval_complete",
                    "retry_target": None,
                }
            )

            return state
        # Get LLM analysis for retrieval parameters
        result = call_llm(prompt)

        # Handle response format
        response_text = ""
        if isinstance(result, tuple) and len(result) == 3:
            response_text = result[0]
        elif isinstance(result, str):
            response_text = result
        else:
            response_text = (
                str(result)
                if result
                else '{"required_documents": [], "keyword_search_words": []}'
            )

        if (
            "⚠️ All Gemini models are busy" in response_text
            or "❌ Something went wrong" in response_text
        ):
            print("⚠️ LLM returned error message, using default retrieval parameters")
            retrieval_plan = {
                "retrieval_mode": "chunk_level",
                "required_documents": [],
                "keyword_search_words": [],
            }
        else:
            # Clean the response
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            try:
                retrieval_plan = json.loads(response_text)
            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse LLM response as JSON: {e}")
                print(f"Raw response: {response_text[:200]}...")
                # Use default values if JSON parsing fails
                retrieval_plan = {
                    "retrieval_mode": "chunk_level",
                    "required_documents": [],
                    "keyword_search_words": [],
                }

        # Extract the LLM reasoning results
        retrieval_mode = retrieval_plan.get("retrieval_mode", "chunk_level")
        required_documents = retrieval_plan.get("required_documents", [])
        keyword_search_words = retrieval_plan.get("keyword_search_words", [])

        actual_doc_names = extract_doc_names_from_query(
            state["project_id"], state["user_query"]
        )
        required_documents = [
            doc for doc in required_documents if doc in actual_doc_names
        ]

        print(
            f"📋 Retrieval Plan:  Mode={retrieval_mode}, Docs={required_documents}, Keywords={keyword_search_words}"
        )

        # Use existing logic to retrieve documents based on LLM analysis
        contexts = []

        # 1. Retrieve the whole document if user mentioned specific documents for the entire content
        if retrieval_mode == "document_level" and required_documents:
            print(f"📄 Full-document retrieval mode selected for: {required_documents}")
            for doc_name in required_documents:
                doc_chunks = retrieve_from_doc_by_name(state["project_id"], doc_name)
                contexts.extend(doc_chunks)

        # 2. Adjust retrieval parameters based on complexity
        # if complexity == "high":
        #     threshold = 0.6  # Stricter similarity for complex queries
        #     max_candidates = 100  # More candidates for complex queries
        # elif complexity == "low":
        #     threshold = 0.8  # Higher threshold for simple queries
        #     max_candidates = 20   # Fewer candidates for simple queries
        # else:  # medium
        #     threshold = 0.7
        #     max_candidates = 50

        # 3. Retrieve project-wide relevant chunks using existing logic
        project_chunks = retrieve_relevant_chunks(
            state["project_id"],
            state["user_query"],
            keyword_terms=keyword_search_words,
            threshold=0.7,
            max_candidates=30,
        )
        contexts.extend(project_chunks)

        if not contexts:
            print("⚠️ Retrieval completed but no matching chunks found.")
            state.update(
                {
                    "contexts": [],
                    "retrieval_mode": retrieval_mode,
                    "required_documents": required_documents,
                    "keyword_search_words": keyword_search_words,
                    "retrieval_metrics": {
                        "documents_found": len(required_documents),
                        "keywords_used": len(keyword_search_words),
                        "chunks_retrieved": 0,
                    },
                    "current_step": "retrieval_complete",
                }
            )
            return state

        # Remove duplicates using existing logic
        seen_texts = set()
        unique_contexts = []
        for doc in contexts:
            if doc.page_content not in seen_texts:
                unique_contexts.append(doc)
                seen_texts.add(doc.page_content)

        # Convert to context format
        context_json = []
        for chunk in unique_contexts:
            context_json.append(
                {
                    "content": chunk.page_content,
                    "source": chunk.metadata.get("filename", "unknown"),
                    "section": chunk.metadata.get("hierarchy", ""),
                    "chunk_index": chunk.metadata.get("chunk_index", 0),
                }
            )

        # Update state with retrieval results
        state.update(
            {
                "retrieval_mode": retrieval_mode,
                "required_documents": required_documents,
                "keyword_search_words": keyword_search_words,
                "contexts": context_json,
                "retrieval_metrics": {
                    "documents_found": len(required_documents),
                    "keywords_used": len(keyword_search_words),
                    "chunks_retrieved": len(context_json),
                },
                "current_step": "retrieval_complete",
            }
        )

        print(f"✅ Retrieval complete: {len(context_json)} chunks retrieved")

    except Exception as e:
        print(f"❌ Retrieval agent error: {e}")
        # Fallback to basic retrieval without LLM analysis
        print("🔄 Using fallback retrieval...")

        # Use existing basic retrieval as fallback
        project_chunks = retrieve_relevant_chunks(
            state["project_id"], state["user_query"], keyword_terms=[]
        )

        # Convert to context format
        context_json = []
        for chunk in project_chunks:
            context_json.append(
                {
                    "content": chunk.page_content,
                    "source": chunk.metadata.get("filename", "unknown"),
                    "section": chunk.metadata.get("hierarchy", ""),
                    "chunk_index": chunk.metadata.get("chunk_index", 0),
                }
            )

        print(f"✅ Retrieval complete: {len(context_json)} chunks retrieved")

        state.update(
            {
                "retrieval_mode": "chunk_level",
                "required_documents": [],
                "keyword_search_words": [],
                "contexts": context_json,
                "current_step": "retrieval_complete",
            }
        )

    return state


# ----------------------------
# Generation Agent
# ----------------------------


def generation_agent(state: AgentState) -> AgentState:
    """Generate response based on query intent and available contexts."""
    print("🤖 Generation Agent: Generating response...")

    try:
        database_engine = state.get("database_engine", "postgresql")
        validation_feedback = state.get("rag_metrics", {}).get("LLMReasoning", "") or ""
        validation_suggestions = state.get("improvement_suggestions", []) or [""]
        retry_target = state.get("retry_target", "none")
        reflection_count = state.get("reflection_count", 0)

        retrieval_mode = state.get("retrieval_mode", "unknown")
        retrieval_metrics = state.get("retrieval_metrics", {})
        documents_found = retrieval_metrics.get("documents_found", 0)
        no_docs_present = retrieval_mode == "none" or documents_found == 0

        # Include validation insights if this is a retry after validation
        feedback_section = ""
        if retry_target == "generation" and (
            validation_feedback or validation_suggestions
        ):
            feedback_section = f"""
            The previous validation step suggested generation improvement.
            REASONING: {validation_feedback}
            SUGGESTIONS: {', '.join(validation_suggestions) if validation_suggestions else 'None'}
            
            Based on this feedback, refine your generation.
            """
            print("🧠 Applying validation feedback to refine generation...")

        if state.get("query_intent") == "conversational":
            # Handle conversational queries
            print("💬 Generating conversational response...")
            conversational_prompt = f"""
            You are a friendly AI assistant. Respond naturally to the user's message.
            Always stay within the conversation topic or SQL/data-related context.
            Never fabricate information, speculate, or respond to out-of-scope topics (e.g., personal opinions, politics, weather, jokes).
            If the user asks something out of scope, politely decline and redirect back to SQL or data-related help.
            
            USER MESSAGE: {state['user_query']}
            CONVERSATION HISTORY: {state['memory'][-20:] if state['memory'] else 'No recent history'}
            
            {feedback_section}
            Respond in a friendly, helpful manner. Keep it concise and natural.
            """

            result = call_llm(conversational_prompt)
            if isinstance(result, tuple) and len(result) == 3:
                response_text = result[0]
            else:
                response_text = (
                    str(result)
                    if result
                    else "I'm here to help! How can I assist you today?"
                )

            state.update(
                {
                    "generated_response": response_text,
                    "response_type": "conversational",
                    "current_step": "generation_complete",
                }
            )

        elif state.get("query_intent") == "sql_query":
            # Handle SQL queries with contexts
            print("📊 Generating SQL response with contexts...")
            print("Current Database Engine:", database_engine)

            # Prepare contexts for the prompt
            contexts = state.get("contexts", [])
            if contexts is None:
                contexts = []
            final_json = (
                json.dumps(contexts, separators=(",", ":")) if contexts else "[]"
            )

            # Use your existing structured prompt
            structured_prompt = f"""
            ## ROLE
            Pragmatic SQL Developer

            ## DATABASE
            {database_engine.upper()}

            ## CONTEXT
            {feedback_section} from Validation Agent  
            
            **CONVERSATION HISTORY:** {state['memory'][-20:] if state['memory'] else 'No history'}

            ## AVAILABLE DOCUMENTATION
            {final_json or '[]'}

            ## USER QUERY
            "{state['user_query']}"

            ## PROJECT STATUS
            {"No documents available." if no_docs_present else "Documents available."}

            --------------------------------------------------
            
            # INTERNAL ANALYSIS INSTRUCTIONS (DO NOT OUTPUT)
            You MUST perform the following steps internally, but NONE of these sections should appear in the final answer:

            ## INTENT ANALYSIS
            Classify as:
            - **SQL_QUERY**
            - **GENERAL_QUESTION**
            - **OUT_OF_SCOPE**

            Identify entities, metrics, filters, and relationships.
            

            ## SCHEMA UNDERSTANDING RULES
            - Use documentation strictly
            - Extract tables, columns, PKs, FKs, relationships
            - If info is missing, make minimal assumptions
            - List all assumptions in **Notes**
            - Follow cross-document references if mentioned
            

            ## SQL GENERATION RULES
            - Use **{database_engine.upper()}-specific syntax**
            - Generate SQL that is syntactically valid, semantically correct, and logically consistent
            - Use exact table and column names
            - Start with the **simplest correct SQL**
            - Avoid over-engineering

            ### STRICT DIALECT MODE
            - Native functions only
            - Executable SQL
            - Write SQL dialect specific for {database_engine.upper()}.
            - No comments inside SQL blocks
            

            ## EXPLANATION DIAGRAM
            - ASCII diagram showing ER relationship diagrams with tables, relationships, and key operations "```  Diagram ```".
            - Only tables used in SQL
            - Each table must be drawn inside its own box using (┌ ┐ └ ┘ │ ─).
            - Table names and columns MUST appear inside the box (never outside).
            - PK marked as `PK:`, FK as `[FK]`
            - Show relationships using ◄────► arrows.
            
            
            ## HARD CONSTRAINTS
            - No comments inside SQL blocks
            - No repeated SQL
            - No narrative text before first SQL block
            - DO NOT mention validation steps, feedback, agents, scenarios, internal decisions or print case names or labels in the output.
            - All explanations must be user-facing and business-oriented
            - If violated, regenerate silently

            --------------------------------------------------
            
            # OUTPUT FORMAT (ONLY THIS SHOULD BE RETURNED)

            - If multiple SQL queries are needed for user queries, repeat the TEMPLATE of natural-language summary, sql, explanation diagram, notes for each SQL.

            ### If the query requires SQL_QUERY:
            - Brief natural-language summary that answers the user question.

            **SQL Query**
            ```sql
            <Executable SQL only>
            ```
            
            **Alternative Approaches**
            <Executable SQL only>

            **Explanation Diagram**
            <ASCII ER diagram>

            **Notes**
            <List any relevant notes here>
            <Assumptions>
            <Limitations>
            
            ### If the query is GENERAL_QUESTION:
            - No SQL
            - Clear, concise explanation

            ### If the query is OUT_OF_SCOPE:
            - Polite response
            - No technical output
            
            """

            ## Stream the response and collect chunks
            print("🔄 Streaming response from LLM...")
            stream_chunks = []
            full_response = ""
            attempt_count = 0
            is_fallback = False
            response_text, attempt_count, is_fallback = call_gemini(
                structured_prompt, stream=False
            )
            safe_response = "" if response_text is None else str(response_text)

            full_response += safe_response
            stream_chunks.append(safe_response)
            state["attempt_count"] = attempt_count
            state["is_fallback"] = is_fallback
            state["generated_response"] = full_response

            print(
                f"✅ Collected {len(stream_chunks)} chunks, total length: {len(full_response)}"
            )
            print("Response:", full_response)

            # Store everything in state
            state.update(
                {
                    "generation_prompt": structured_prompt,
                    "generated_response": full_response,
                    "stream_chunks": stream_chunks,
                    "response_type": "sql",
                    "contexts_used": contexts,
                    "current_step": "generation_complete",
                }
            )

        print(f"✅ Generation complete. Type: {state.get('response_type', 'unknown')}")
        return state

    except Exception as e:
        print(f"❌ Generation agent error: {e}")
        # Fallback response
        state.update(
            {
                "generated_response": "I apologize, but I encountered an error while generating the response. Please try again.",
                "response_type": "error",
                "current_step": "generation_complete",
            }
        )

    return state


# ----------------------------
# Complexity Guard (Rule-Based)
# ----------------------------
def complexity_guard(state: AgentState) -> AgentState:
    """
    Rule-based complexity checker that analyzes SQL for unnecessary complexity.
    only rule-based analysis.
    """
    print("🛡️ Complexity Guard: Analyzing SQL complexity...")

    response_type = state.get("response_type", "")
    generated_response = state.get("generated_response", "")

    # Only check SQL responses
    if response_type not in ["sql", "sql_optimization"]:
        print("⏭️  Not a SQL response, skipping complexity check")
        state.update(
            {
                "sql_complexity_score": 0,
                "complexity_reasons": [],
                "needs_simplification": False,
                "current_step": "complexity_check_complete",
            }
        )
        return state

    # Extract SQL from response
    import re

    def extract_sql_blocks(text: str) -> List[str]:
        """Extract SQL code blocks from response."""
        pattern = r"```sql\s*(.*?)\s*```"
        blocks = re.findall(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        if not blocks:
            # Try to find SQL without code blocks
            sql_keywords = r"(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP).*?;"
            blocks = re.findall(sql_keywords, text, flags=re.DOTALL | re.IGNORECASE)
        return blocks

    sql_blocks = extract_sql_blocks(generated_response)

    if not sql_blocks:
        print("⏭️  No SQL blocks found, skipping complexity check")
        state.update(
            {
                "sql_complexity_score": 0,
                "complexity_reasons": [],
                "needs_simplification": False,
                "current_step": "complexity_check_complete",
            }
        )
        return state

    # RULE-BASED COMPLEXITY ANALYSIS
    complexity_score = 0
    complexity_reasons = []
    needs_simplification = False

    for i, sql in enumerate(sql_blocks):
        sql_upper = sql.upper()

        # RULE 1: Count CTEs (Common Table Expressions)
        cte_count = sql_upper.count("WITH ") + sql_upper.count("),")
        if cte_count > 1:
            complexity_score += 20
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Too many CTEs ({cte_count}) - consider simplifying"
            )

        # RULE 2: Count subqueries in FROM clause
        from_subqueries = len(re.findall(r"FROM\s*\([^)]*\)", sql_upper, re.IGNORECASE))
        if from_subqueries > 1:
            complexity_score += 15
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Multiple nested subqueries in FROM clause"
            )

        # RULE 3: Check for excessive window functions
        window_functions = len(
            re.findall(r"(ROW_NUMBER|RANK|DENSE_RANK|NTILE|LEAD|LAG)\s*\(", sql_upper)
        )
        if window_functions > 1:
            complexity_score += 15
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Excessive window functions ({window_functions})"
            )

        # RULE 4: Check for complex CASE WHEN nesting
        case_when_depth = 0
        lines = sql.split("\n")
        for line in lines:
            case_count = line.upper().count("CASE ")
            when_count = line.upper().count("WHEN ")
            end_count = line.upper().count("END")
            if case_count > 1 or when_count > 4:
                case_when_depth += 1

        if case_when_depth > 1:
            complexity_score += 10
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Deeply nested CASE WHEN statements"
            )

        # RULE 5: Check for excessive JOINs
        join_count = sql_upper.count(" JOIN ")
        if join_count > 5:
            complexity_score += 15
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Too many JOINs ({join_count}) - consider breaking into smaller queries"
            )

        # RULE 6: Check for UNION/UNION ALL complexity
        union_count = sql_upper.count(" UNION ") + sql_upper.count(" UNION ALL ")
        if union_count > 1:
            complexity_score += 10
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Multiple UNION operations - can be simplified"
            )

        # RULE 7: Check query length
        sql_length = len(sql)
        if sql_length > 2000:  # 2000 characters
            complexity_score += 10
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Query is very long ({sql_length} chars)"
            )

        # RULE 8: Check for complex string manipulation functions
        complex_funcs = len(
            re.findall(r"(REGEXP|CONCAT_WS|STRING_AGG|JSON|ARRAY)", sql_upper)
        )
        if complex_funcs > 2:
            complexity_score += 10
            needs_simplification = True
            complexity_reasons.append(
                f"SQL block {i+1}: Complex string/JSON/array functions"
            )

    # Normalize score to 0-100
    complexity_score = min(100, complexity_score)

    print(f"📊 Complexity Score: {complexity_score}/100")
    if complexity_reasons:
        print(f"📝 Complexity Reasons: {complexity_reasons}")
    print(f"🔄 Needs Simplification: {needs_simplification}")

    state.update(
        {
            "sql_complexity_score": complexity_score,
            "complexity_reasons": complexity_reasons,
            "needs_simplification": needs_simplification,
            "current_step": "complexity_check_complete",
        }
    )

    return state


# ----------------------------
# Simplification Agent
# ----------------------------
def simplification_agent(state: AgentState) -> AgentState:
    """
    Simplify SQL queries that are overly complex.
    Uses LLM to generate simpler, equivalent SQL.
    """
    print("🔄 Simplification Agent: Simplifying complex SQL...")

    generated_response = state.get("generated_response", "")
    complexity_reasons = state.get("complexity_reasons", [])
    database_engine = state.get("database_engine", "postgresql")

    if not generated_response or not complexity_reasons:
        print("⏭️  No complexity issues to simplify")
        state.update(
            {
                "generated_response": generated_response,
                "current_step": "simplification_complete",
            }
        )
        return state

    # Prepare prompt for simplification
    prompt = f"""
    ## ROLE
    SQL Simplification Expert
    
    ## USER QUERY
    "{state['user_query']}"
    
    ## TASK
    Simplify the following SQL query.
    The query is currently too complex for the following reasons:
    {', '.join(complexity_reasons)}
    
    ## ORIGINAL SQL RESPONSE
    {generated_response}
    
    ## DATABASE ENGINE
    {database_engine.upper()}
    
    ## SIMPLIFICATION GUIDELINES

    Analyze the given SQL and Check whether the query can be further simplified, simplify it to the **least complex form**.

    ### SIMPLIFICATION GOALS (EXAMPLES)
    - Reduce unnecessary CTEs
    - Merge CTEs that only filter or rename columns
    - Remove redundant CASE expressions
    - Reduce nested and unnecessarily used window functions
    - Apply filters as early as possible
    - Prefer simpler logic over layered transformations

    ### WHAT TO AVOID
    - Over-engineering
    - Extra abstraction layers
    - Repeating the same calculation across steps
    
    ### OUTPUT FORMAT:
    - Brief natural-language summary about user query

    **SQL Query**
    ```sql
    <Executable and simplified SQL only>
    ```
    
    **Alternative Approaches** (optional)
    <Executable and simplified SQL only>

    **Explanation Diagram**
    <ASCII ER diagram>

    **Notes**
    <List any relevant notes here>
    <Assumptions>
    <Limitations>
    
    ### CRITICAL CONSTRAINTS:
    - No comments inside SQL blocks
    - SQL must be syntactically valid for {database_engine.upper()}
    - Do NOT remove necessary functionality
    
    Return the FULL simplified response. If the query cannot be simplified further, return it unchanged.
    """

    try:
        # Call LLM for simplification
        result = call_llm(prompt)

        if isinstance(result, tuple) and len(result) == 3:
            simplified_text = result[0]
        else:
            simplified_text = str(result)

        # Clean the response
        simplified_text = simplified_text.strip()

        # Verify we got a meaningful response
        if (
            len(simplified_text) < 50
            or "⚠️" in simplified_text
            or "❌" in simplified_text
        ):
            print("⚠️ Simplification failed, using original response")
            simplified_text = generated_response

        print(
            f"✅ Simplification complete. Original length: {len(generated_response)}, Simplified: {len(simplified_text)}"
        )

        state.update(
            {
                "generated_response": simplified_text,
                "current_step": "simplification_complete",
            }
        )

    except Exception as e:
        print(f"❌ Simplification agent error: {e}")
        # Fallback to original response
        state.update(
            {
                "generated_response": generated_response,
                "current_step": "simplification_complete",
            }
        )

    return state


# ----------------------------
# Validation Agent
# ----------------------------
def validation_agent(state: AgentState) -> AgentState:
    """Evaluate the RAG response quality and provide improvement suggestions."""
    print("🔍 Validation Agent: Evaluating response quality...")

    try:
        # Only validate SQL responses that have been generated
        if state.get("response_type") in ["sql", "sql_optimization"]:
            retrieval_mode = state.get("retrieval_mode", "unknown")
            retrieval_metrics = state.get("retrieval_metrics", {})
            documents_found = retrieval_metrics.get("documents_found", 0)
            no_docs_present = retrieval_mode == "none" or documents_found == 0
            generated_response = state["generated_response"]
            contexts = state.get("contexts", [])
            user_query = state["user_query"]
            project_id = state["project_id"]

            # Get database_engine from state
            database_engine = state.get("database_engine", "postgresql")

            # Run basic RAG evaluation (only reliable rule-based metrics)
            rag_metrics = evaluate_query_rag(
                project_id,
                user_query,
                generated_response,
                contexts,
                database_engine=database_engine,
            )

            # ==========================================================
            # FORCE RETRY ON SQL SYNTAX / SEMANTIC ERRORS
            # ==========================================================

            claims = rag_metrics.get("claims", [])

            sql_errors = [
                c
                for c in claims
                if c.startswith("parse_error") or c.startswith("semantic_error")
            ]

            if sql_errors:
                print("🚨 SQL validation failure detected:", sql_errors)
                # Get current reflection count
                current_count = state.get("reflection_count", 0)

                if current_count < 2:
                    # Increment reflection count
                    new_count = current_count + 1
                    state["reflection_count"] = new_count

                    state.update(
                        {
                            "rag_metrics": rag_metrics,
                            "validation_result": f"SQL validation failed (attempt {new_count}/2): "
                            + "; ".join(sql_errors),
                            "needs_improvement": True,
                            "retry_target": (
                                "optimization"
                                if state.get("query_intent") == "sql_optimization"
                                else "generation"
                            ),
                            "improvement_suggestions": sql_errors,
                            "current_step": "validation_complete",
                        }
                    )

                    print(f"🔄 Will retry (attempt {new_count}/2)")
                else:
                    # Max retries reached - force stop
                    state.update(
                        {
                            "rag_metrics": rag_metrics,
                            "validation_result": f"❌ Max retries ({current_count}) reached. SQL validation failed: "
                            + "; ".join(sql_errors),
                            "needs_improvement": False,  # IMPORTANT: Force stop
                            "retry_target": "none",  # IMPORTANT: Force stop
                            "improvement_suggestions": [
                                "Max retry attempts reached. Please check your SQL syntax manually."
                            ],
                            "current_step": "validation_complete",
                        }
                    )
                    print(f"⏹️ Max retries ({current_count}) reached, stopping loop")

                return state

            # Add LLM reasoning for other metrics
            llm_eval_prompt = f"""
            ## ROLE
            You are a SQL quality, correctness, and simplicity evaluator.

            ## INPUT
            **DATABASE ENGINE:** {database_engine}

            **USER QUERY:** "{user_query}"

            **GENERATED RESPONSE:**
            {generated_response}

            **AVAILABLE CONTEXTS:**
            {json.dumps(contexts, indent=2) if contexts else 'No contexts available'}

            **PROJECT DOCUMENT STATUS:**
            {"No documents available. Retrieval was skipped." if no_docs_present else "Documents were available for retrieval."}

            ## RETRIEVAL METRICS (Rule-Based)
            - Precision@1: {rag_metrics.get('Precision@1', 'N/A')}
            - Precision@3: {rag_metrics.get('Precision@3', 'N/A')}
            - Precision@5: {rag_metrics.get('Precision@5', 'N/A')}
            - SQL Elements Found: {rag_metrics.get('NumClaims', 'N/A')}

            --------------------------------------------------

            ## EVALUATION CRITERIA

            ### 1. QUERY COVERAGE (0.0 – 1.0)
            - Does the response fully satisfy the user's intent?
            - Consider explicit and implicit requirements.

            ### 2. CLAIM SUPPORT RATE (0.0 – 1.0)
            - Do table names, column names, and relationships match the retrieved context?
            - Ignore stylistic or structural preferences.

            ### 3. STATIC EXECUTION SCORE (0.0 – 1.0)
            - Is the SQL syntactically valid, semantically correct, and logically consistent?
            - Is the logic correct?

            ### 4. APPROPRIATE SQL COMPLEXITY (0.0 – 1.0)
            - Is the SQL as simple as possible while remaining correct?
            - Penalize unnecessary abstraction, layering, or over-engineering.
            - Scoring guidance:
            - 1.0 → perfectly simple and correct
            - 0.8 → minor unnecessary complexity
            - 0.6 → could be simplified
            - 0.3 → clearly over-engineered
            - 0.0 → extremely over-engineered

            --------------------------------------------------

            ## RETRY DECISION ANALYSIS
            Determine the primary issue:
            - **retrieval** → missing or incorrect contexts
            - **generation** → incorrect SQL despite good context
            - **optimization** → ONLY if query_intent is "sql_optimization" and AND SQL needs performance tuning
            - **none** → SQL is correct, minimal, and acceptable

            ### CRITICAL RULES
            - If NO documents exist and retrieval was skipped → DO NOT suggest retrieval.
            - Default to **retrieval** if unclear.

            --------------------------------------------------

            ## IMPROVEMENT GUIDELINES
            - Evaluate EACH SQL query independently.
            - Decide improvement_suggestions by considering ALL evaluation scores together.
            - Suggest changes when one or more scores clearly justify it, such as:
                - Incorrect business logic
                - Real SQL errors
                - Clear inefficiencies
                - Legitimate simplification opportunities
            - Do NOT suggest adding CTEs, window functions, or structure for style.
            - If SQL is correct, simple, and scores are consistently high → ACCEPT AS-IS.
            - If no improvements are needed → return an empty list [].

            --------------------------------------------------

            ## OUTPUT FORMAT (JSON ONLY)
            {{
            "QueryCoverage": 0.0,
            "ClaimSupportRate": 0.0,
            "StaticExecScore": 0.0,
            "AppropriateComplexity": 0.0,
            "needs_improvement": "true | false"
            "retry_target": "retrieval | generation | optimization | none",
            "improvement_suggestions": ["suggestion 1", "suggestion 2", "suggestion 3"],
            "reasoning": "Clear explanation of scores and retry decision"
            }}

            --------------------------------------------------

            ## HARD OUTPUT CONSTRAINTS (NON-NEGOTIABLE)
            - SQL code blocks MUST NOT contain comments (-- or /* */)
            - SQL blocks MUST NOT contain explanatory text
            - The same SQL must NOT be repeated
            - Narrative text MUST NOT appear before the first SQL block

            If any constraint is violated, regenerate silently and return ONLY the corrected JSON.

            """

            try:
                result = call_llm(llm_eval_prompt)
                response_text = result[0] if isinstance(result, tuple) else str(result)

                # Clean response
                response_text = response_text.strip()
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
                response_text = response_text.strip()

                llm_analysis = json.loads(response_text)

                # Add LLM metrics to rag_metrics with same names
                rag_metrics.update(
                    {
                        "QueryCoverage": llm_analysis.get("QueryCoverage", 0.5),
                        "ClaimSupportRate": llm_analysis.get("ClaimSupportRate", 0.5),
                        "StaticExecScore": llm_analysis.get("StaticExecScore", 0.5),
                        "AppropriateComplexity": llm_analysis.get(
                            "AppropriateComplexity", 0.5
                        ),
                        "LLMReasoning": llm_analysis.get(
                            "reasoning", "No detailed reasoning"
                        ),
                        "needs_improvement": llm_analysis.get(
                            "needs_improvement", False
                        ),
                        "retry_target": llm_analysis.get("retry_target", "none"),
                        "improvement_suggestions": llm_analysis.get(
                            "improvement_suggestions", []
                        ),
                    }
                )

                print(
                    f"🤖 LLM Analysis: Needs improvement = {rag_metrics['needs_improvement']}, Retry target = {rag_metrics['retry_target']}"
                )
                print(
                    f"🧠 LLM Reasoning: {rag_metrics['LLMReasoning']}, Suggestions: {rag_metrics['improvement_suggestions']}"
                )

            except Exception as e:
                print(f"❌ LLM evaluation error: {e}")
                # Fallback scores for LLM metrics
                rag_metrics.update(
                    {
                        "QueryCoverage": None,
                        "ClaimSupportRate": None,
                        "StaticExecScore": None,
                        "AppropriateComplexity": None,
                        "LLMReasoning": f"LLM evaluation failed: {str(e)}",
                        "needs_improvement": False,  # Default to improvement needed on error
                        "retry_target": "none",  # Default to retrieval retry
                        "improvement_suggestions": [
                            "Check system configuration and try again"
                        ],
                    }
                )

            # Generate validation summary
            validation_summary = f"""
            ## RAG Quality Assessment

            ### 📊 Metrics Summary:
            - **Precision@1**: {rag_metrics['Precision@1'] or 'N/A'} (Retrieval Quality)
            - **Precision@3**: {rag_metrics['Precision@3'] or 'N/A'} (Retrieval Quality) 
            - **Precision@5**: {rag_metrics['Precision@5'] or 'N/A'} (Retrieval Quality)
            - **Claim Support Rate**: {rag_metrics['ClaimSupportRate'] or 'N/A'} (LLM: Context Support)
            - **Static Execution Score**: {rag_metrics['StaticExecScore'] or 'N/A'} (LLM: SQL Quality)
            - **Query Coverage**: {rag_metrics['QueryCoverage'] or 'N/A'} (LLM: Intent Match)
            - **Appropriate Complexity**: {rag_metrics['AppropriateComplexity'] or 'N/A'} (LLM: Complexity Assessment)
            - **SQL Elements Found**: {rag_metrics['NumClaims']} (Rule-based)

            ### 🧠 LLM Analysis:
            {rag_metrics.get('LLMReasoning', 'No analysis available')}

            ### 🔄 Retry Decision:
            - **Needs Improvement**: {rag_metrics.get('needs_improvement', False)}
            - **Retry Target**: {rag_metrics.get('retry_target', 'none')}
            - **Suggestions**: {', '.join(rag_metrics.get('improvement_suggestions', ['No specific suggestions']))}

            ### 🎯 Status: {'⚠️ Needs Improvement' if rag_metrics.get('needs_improvement', False) else '✅ Quality Acceptable'}
            """
            current_count = state.get("reflection_count", 0)
            if rag_metrics.get("needs_improvement") and current_count < 2:
                current_count = current_count + 1
                state["reflection_count"] = current_count
                print(f"✅ Final reflection count: {current_count}")
            elif current_count >= 2:
                rag_metrics["needs_improvement"] = False
                rag_metrics["retry_target"] = None
                print(f"✅ Final reflection count: {current_count}")

            state.update(
                {
                    "rag_metrics": rag_metrics,
                    "validation_result": validation_summary,
                    "needs_improvement": rag_metrics.get("needs_improvement", False),
                    "retry_target": rag_metrics.get("retry_target", "none"),
                    "improvement_suggestions": rag_metrics.get(
                        "improvement_suggestions", []
                    ),
                    "current_step": "validation_complete",
                }
            )

            if state["query_intent"] == "sql_optimization":
                if state["retry_target"] == "generation":
                    rag_metrics["retry_target"] = "optimization"
                    state["retry_target"] = "optimization"
            print(
                f"✅ Hybrid validation complete. Needs improvement: {rag_metrics.get('needs_improvement', False)}"
            )
            print(f"🎯 Retry target: {rag_metrics.get('retry_target', 'none')}")

        else:
            state.update(
                {
                    "rag_metrics": {},
                    "validation_result": "Validation skipped - not a SQL response",
                    "needs_improvement": False,
                    "retry_target": "none",
                    "improvement_suggestions": [],
                    "current_step": "validation_complete",
                }
            )
            print("⏭️  Validation skipped (not a SQL response)")

    except Exception as e:
        print(f"❌ Validation agent error: {e}")
        current_count = state.get("reflection_count", 0) + 1
        state.update(
            {
                "rag_metrics": {},
                "reflection_count": current_count,
                "validation_result": f"Validation failed: {str(e)}",
                "needs_improvement": current_count < 2,
                "retry_target": "retrieval" if current_count < 2 else "none",
                "improvement_suggestions": [
                    "System error occurred - retry recommended"
                ],
                "current_step": "validation_complete",
            }
        )

    return state


# ----------------------------
# Optimization Agent
# ----------------------------
def optimization_agent(state: AgentState) -> AgentState:
    """
    Handles SQL optimization requests using LLM reasoning only.
    """

    print("⚡ Optimization Agent: Processing SQL optimization request...")

    database_engine = state.get("database_engine", "postgresql")

    validation_feedback = state.get("rag_metrics", {}).get("LLMReasoning", "")
    validation_suggestions = state.get("improvement_suggestions", [])
    retry_target = state.get("retry_target", "none")
    reflection_count = state.get("reflection_count", 0)

    retrieval_mode = state.get("retrieval_mode", "unknown")
    retrieval_metrics = state.get("retrieval_metrics", {})
    documents_found = retrieval_metrics.get("documents_found", 0)
    no_docs_present = retrieval_mode == "none" or documents_found == 0

    # Include validation insights if this is a retry after validation
    feedback_section = ""
    if retry_target == "optimization" and (
        validation_feedback or validation_suggestions
    ):
        feedback_section = f"""
        The previous validation step suggested optimization improvement.
        REASONING: {validation_feedback}
        SUGGESTIONS: {', '.join(validation_suggestions) if validation_suggestions else 'None'}
        
        Based on this feedback, refine your optimization.
        """
        print("🧠 Applying validation feedback to refine optimization...")

    engine_lower = database_engine.lower()

    # Determine which prompt file to use
    if any(engine in engine_lower for engine in ["oracle", "ora"]):
        prompt_file = "oracle_optimization.md"
    else:
        prompt_file = "generic_optimization.md"

    # Load the prompt file
    prompt_path = os.path.join(BASE_DIR, "prompts", "optimization_agent", prompt_file)

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            base_prompt = f.read().strip()
    except FileNotFoundError:
        # Fallback to default prompt
        print(f"⚠️ Prompt file not found: {prompt_file}, using default")
        base_prompt = """Provide optimized SQL with minimal changes first.
Check for unnecessary sorts, non-SARGable predicates, and suboptimal joins."""

    prompt = f"""
## ROLE
Senior SQL Performance Engineer

## DATABASE
{database_engine.upper()}

## CONTEXT
{feedback_section} from Validation Agent  
**USER REQUEST:** "{state['user_query']}"  
**CONVERSATION HISTORY:** {state['memory'][-20:] if state['memory'] else 'No history'}  
**SCHEMA / DOCUMENTATION:** {json.dumps(state.get("contexts", []), indent=2)}

## PROJECT STATUS
{"No documents available – infer schema." if no_docs_present else "Documentation available."}

--------------------------------------------------

{base_prompt}

    """

    try:
        response = call_llm(prompt)

        if isinstance(response, tuple) and len(response) == 3:
            response_text = response[0]
        else:
            response_text = str(response)

        # Save response
        state.update(
            {
                "generated_response": response_text.strip(),
                "response_type": "sql_optimization",
                "current_step": "optimization_complete",
            }
        )
        print(f"Generated Response: {state['generated_response']}")
        print("✅ Optimization processing complete")

    except Exception as e:
        print("❌ Optimization agent failed:", e)
        state.update(
            {
                "generated_response": (
                    "I ran into an error while analyzing your query. "
                    "Please try again or re-send the SQL you want optimized."
                ),
                "response_type": "error",
                "current_step": "optimization_complete",
            }
        )

    return state


def update_status_before_node(state: AgentState, node_name: str):

    retry_target = state.get("retry_target")

    # Convert normal node into retry node if this is a retry
    if retry_target == node_name:
        node_name = f"retry_{node_name}"

    status_messages = {
        "planning": "🔍 Analyzing query...",
        "retrieval": "📋 Retrieving information...",
        "generation": "🤖 Generating response...",
        "optimization": "⚡ Optimizing SQL...",
        "complexity_guard": "🛡️ Checking complexity...",
        "simplification": "🔄 Simplifying SQL...",
        "validation": "✅ Validating answer...",
        "retry_retrieval": "🔄 Improving retrieval...",
        "retry_generation": "🔄 Improving response...",
        "retry_optimization": "🔄 Reworking optimization...",
    }
    state["current_status"] = status_messages.get(node_name, "Processing...")
    return state


from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()


# ----------------------------
# LangGraph Workflow Construction
# ----------------------------
def create_agent_workflow():
    """Create the LangGraph workflow with agentic decision making."""

    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node(
        "planning",
        lambda state: planning_agent(update_status_before_node(state, "planning")),
    )
    workflow.add_node(
        "retrieval",
        lambda state: retrieval_agent(update_status_before_node(state, "retrieval")),
    )
    workflow.add_node(
        "generation",
        lambda state: generation_agent(update_status_before_node(state, "generation")),
    )
    workflow.add_node(
        "complexity_guard",
        lambda state: complexity_guard(
            update_status_before_node(state, "complexity_guard")
        ),
    )
    workflow.add_node(
        "simplification",
        lambda state: simplification_agent(
            update_status_before_node(state, "simplification")
        ),
    )
    workflow.add_node(
        "validation",
        lambda state: validation_agent(update_status_before_node(state, "validation")),
    )
    workflow.add_node(
        "optimization",
        lambda state: optimization_agent(
            update_status_before_node(state, "optimization")
        ),
    )

    # Define workflow edges
    workflow.set_entry_point("planning")
    workflow.add_conditional_edges(
        "planning",
        lambda state: (
            "generation" if state["query_intent"] == "conversational" else "retrieval"
        ),
        {
            "generation": "generation",
            "retrieval": "retrieval",
        },
    )
    workflow.add_conditional_edges(
        "retrieval",
        lambda state: (
            "optimization"
            if state["query_intent"] == "sql_optimization"
            else "generation"
        ),
        {"optimization": "optimization", "generation": "generation"},
    )
    workflow.add_edge("optimization", "validation")
    workflow.add_conditional_edges(
        "generation",
        lambda state: (
            "complexity_guard"
            if state.get("response_type") in ["sql", "sql_optimization"]
            else "validation"
        ),
        {"complexity_guard": "complexity_guard", "validation": "validation"},
    )
    workflow.add_conditional_edges(
        "complexity_guard",
        lambda state: (
            "simplification"
            if state.get("needs_simplification", False)
            else "validation"
        ),
        {"simplification": "simplification", "validation": "validation"},
    )

    # After simplification, go to validation
    workflow.add_edge("simplification", "validation")

    workflow.add_conditional_edges(
        "validation",
        lambda state: (
            state.get("retry_target")
            if (
                state.get("needs_improvement", False)
                and state.get("retry_target")
                in ["retrieval", "optimization", "generation"]
                and state.get("reflection_count", 0) < 2
            )
            else END
        ),
        {
            "retrieval": "retrieval",
            "optimization": "optimization",
            "generation": "generation",
            END: END,
        },
    )

    return workflow.compile(checkpointer=checkpointer)


# Create the workflow
agent_workflow = create_agent_workflow()


# ----------------------------
# Chat Model
# ----------------------------


class ChatRequest(BaseModel):
    project_id: int
    query: str
    conversation_id: Optional[str] = None  # optional
    top_k: int = 5
    original_query_id: Optional[str] = None  # << added
    database_engine: Optional[str] = None


@app.post("/chat")
async def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    if not user_has_project_access(user_id, req.project_id, current_user.get("role", "user")):
        raise HTTPException(status_code=403, detail="Access denied to this project")
    conversation_id = req.conversation_id or str(uuid.uuid4())
    safe_query = redact_pii(req.query)
    safe_query = sanitize_user_query(safe_query)
    start_time = time.time()

    # Store query in database initially — reuse original_query_id if provided
    query_id = req.original_query_id or str(uuid.uuid4())

    conn = get_connection()
    c = conn.cursor()

    if req.original_query_id:
        # Check that user owns this query (or is admin) before allowing update
        c.execute(
            "SELECT id FROM queries WHERE id=? AND (user_id=? OR ?='admin')",
            (req.original_query_id, user_id, current_user.get("role", "user")),
        )
        if c.fetchone():
            c.execute(
                """
                UPDATE queries
                SET query = ?, answer = ?, response_time = ?, attempts = ?, is_fallback = ?, database_engine = ?
                WHERE id = ?
            """,
                (
                    safe_query,
                    "",
                    0,
                    0,
                    False,
                    req.database_engine,
                    req.original_query_id,
                ),
            )
        else:
            query_id = str(uuid.uuid4())
            c.execute(
                """
                INSERT INTO queries (id, project_id, query, answer, user_id, conversation_id, response_time, database_engine)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    query_id,
                    req.project_id,
                    safe_query,
                    "",
                    user_id,
                    conversation_id,
                    0,
                    req.database_engine,
                ),
            )
    else:
        # original not provided — insert new as before
        c.execute(
            """
            INSERT INTO queries (id, project_id, query, answer, user_id, conversation_id, response_time, database_engine)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                query_id,
                req.project_id,
                safe_query,
                "",
                user_id,
                conversation_id,
                0,
                req.database_engine,
            ),
        )

    conn.commit()
    conn.close()

    # Initialize memory if missing
    if conversation_id not in conversation_memory:
        conversation_memory[conversation_id] = []
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
    SELECT DISTINCT q.id, q.query, q.answer, q.user_id, u.username
    FROM queries q
    LEFT JOIN users u ON q.user_id = u.id
    WHERE q.conversation_id = ? AND q.project_id = ?
    AND (
        q.user_id = ? 
        OR EXISTS (
            SELECT 1 FROM shared_conversations sc
            WHERE sc.conversation_id = q.conversation_id 
            AND sc.project_id = q.project_id
            AND sc.recipient_id = ?
            AND sc.sharer_id = q.user_id
            AND sc.sharer_id != ?
        )
        OR EXISTS (
            SELECT 1 FROM shared_conversations sc2
            WHERE sc2.conversation_id = q.conversation_id 
            AND sc2.project_id = q.project_id
            AND sc2.sharer_id = ?
            AND q.user_id != ?
        )
    )
    ORDER BY q.created_at
""",
            (
                conversation_id,
                req.project_id,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
            ),
        )

        rows = c.fetchall()
        conn.close()
        for q_id, q, a, user_id_row, username in rows:
            conversation_memory[conversation_id].append({"role": "user", "content": q})
            conversation_memory[conversation_id].append(
                {"role": "assistant", "content": a}
            )

    memory = conversation_memory[conversation_id]

    # ✅ ADD DEBUG: Show full conversation history
    print(f"🧠 FULL Conversation History for {conversation_id}: {len(memory)} messages")
    for i, msg in enumerate(memory):
        print(f"  {i}: {msg['role']}: {msg['content'][:80]}...")

    memory.append({"role": "user", "content": safe_query})

    # Initialize state for LangGraph
    initial_state = AgentState(
        user_query=safe_query,
        project_id=req.project_id,
        conversation_id=conversation_id,
        user_id=user_id,
        database_engine=req.database_engine,
        memory=memory,
        query_intent="",
        next_steps=[],
        retrieval_mode="",
        required_documents=[],
        keyword_search_words=[],
        generated_response="",
        contexts=[],
        attempt_count=0,
        is_fallback=False,
        current_step="start",
        rag_metrics={},
        validation_result="",
        needs_improvement=False,
        retry_target="none",
        improvement_suggestions=[],
        reflection_count=0,
        sql_complexity_score=0,  # 0-100 score
        complexity_reasons=[],  # List of complexity issues
        needs_simplification=False,  # Whether simplification is needed
    )
    config = {"configurable": {"thread_id": conversation_id}}  # ✅ Only needed

    # Streaming response generator
    async def stream_and_save():
        # Send initial status
        yield "[[[STATUS]]]🚀 Starting processing...---SEPARATOR---"

        config = {"configurable": {"thread_id": conversation_id}}

        # Track previous status to avoid duplicates
        previous_status = ""

        # Stream status updates during workflow execution
        for current_state in agent_workflow.stream(initial_state, config=config):
            for node_name, state in current_state.items():
                if (
                    "current_status" in state
                    and state["current_status"] != previous_status
                ):
                    yield f"[[[STATUS]]]{state['current_status']}---SEPARATOR---"
                    await asyncio.sleep(0.01)
                    previous_status = state["current_status"]
                final_state = state

        workflow_state = (
            final_state
            if final_state
            else agent_workflow.invoke(initial_state, config=config)
        )

        # Get the final response
        full_answer = workflow_state.get("generated_response", "")

        # Calculate response time
        response_time = time.time() - start_time

        # Store assistant response in memory
        memory.append({"role": "assistant", "content": full_answer})

        # Truncate memory if needed
        if len(memory) > MAX_MEMORY_LENGTH:
            conversation_memory[conversation_id] = memory[-MAX_MEMORY_LENGTH:]

        rag_metrics = workflow_state.get("rag_metrics", {})

        # Update database with final answer
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
            UPDATE queries
            SET answer = ?, response_time = ?, attempts = ?, is_fallback = ?, 
                precision1 = ?, precision3 = ?, precision5 = ?, 
                claim_support = ?, static_exec = ?, query_coverage = ?, num_claims = ?
            WHERE id = ?
        """,
            (
                full_answer,
                response_time,
                workflow_state.get("attempt_count", 1),
                workflow_state.get("is_fallback", False),
                rag_metrics.get("Precision@1"),
                rag_metrics.get("Precision@3"),
                rag_metrics.get("Precision@5"),
                rag_metrics.get("ClaimSupportRate"),
                rag_metrics.get("StaticExecScore"),
                rag_metrics.get("QueryCoverage"),
                rag_metrics.get("NumClaims"),
                query_id,
            ),
        )
        conn.commit()
        conn.close()

        conn = get_connection()
        c = conn.cursor()
        # Check if this is the FIRST message in the conversation
        # Count how many queries exist for this conversation_id
        c.execute(
            "SELECT COUNT(*) FROM queries WHERE conversation_id = ?", (conversation_id,)
        )
        query_count = c.fetchone()[0]

        # Initialize meta dict
        meta = {
            "contexts": workflow_state.get("contexts", []),
            "query_id": query_id,
            "conversation_id": conversation_id,
            "conversation_title": "",  # Default empty
        }

        # Replace the title generation section with this:
        if query_count == 1:
            try:
                print(
                    f"🎯 First message in conversation {conversation_id}, generating title..."
                )

                # Get existing titles in this project for uniqueness check
                c.execute(
                    "SELECT title FROM conversation_metadata WHERE project_id=?",
                    (req.project_id,),
                )
                existing_titles = [row[0] for row in c.fetchall() if row[0]]

                # Generate unique title
                title = generate_unique_conversation_title(
                    req.query, full_answer[:200], existing_titles
                )

                # Store the title
                c.execute(
                    """
                    INSERT INTO conversation_metadata (conversation_id, project_id, title)
                    VALUES (?, ?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET 
                    title = excluded.title,
                    last_updated = CURRENT_TIMESTAMP
                """,
                    (conversation_id, req.project_id, title),
                )

                # Add title to metadata
                meta["conversation_title"] = title
                print(f"✅ Generated title: {title}")

            except Exception as e:
                print(f"Failed to save conversation title: {e}")
                # Still return the title even if metadata save fails
                title = generate_fallback_unique_title(req.query)
                meta["conversation_title"] = title

        else:
            # For subsequent messages, fetch the existing title
            try:
                c.execute(
                    "SELECT title FROM conversation_metadata WHERE conversation_id=?",
                    (conversation_id,),
                )
                title_row = c.fetchone()
                if title_row and title_row[0]:
                    meta["conversation_title"] = title_row[0]
                    print(f"📝 Using existing title: {title_row[0]}")
                else:
                    # If no title exists, generate one
                    title = generate_fallback_unique_title(req.query)
                    c.execute(
                        """
                        INSERT INTO conversation_metadata (conversation_id, project_id, title)
                        VALUES (?, ?, ?)
                        ON CONFLICT(conversation_id) DO UPDATE SET 
                        title = excluded.title,
                        last_updated = CURRENT_TIMESTAMP
                    """,
                        (conversation_id, req.project_id, title),
                    )
                    meta["conversation_title"] = title
            except Exception as e:
                print(f"Failed to fetch existing title: {e}")
                meta["conversation_title"] = generate_fallback_unique_title(req.query)

        conn.commit()
        conn.close()

        # Send the COMPLETE response at the end (not in chunks)
        yield f"{full_answer}---SEPARATOR---"

        # Send metadata at the end
        yield f"[[[META]]]{json.dumps(meta)}---SEPARATOR---"

    return StreamingResponse(stream_and_save(), media_type="text/plain")


# ----------------------------
# Update Query Endpoint
# ----------------------------
class QueryUpdateRequest(BaseModel):
    query: Optional[str] = None
    answer: Optional[str] = None


@app.put("/queries/{query_id}")
async def update_query(
    query_id: str,
    req: QueryUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Update an existing query in the database.
    Users can edit their own queries OR queries in conversations shared with them with 'edit' permission.
    """
    conn = get_connection()
    c = conn.cursor()

    try:
        # First, check if user can edit this query
        # User can edit if:
        # 1. They own the query (user_id matches)
        # 2. OR the query is in a conversation shared with them with 'edit' permission
        # 3. OR they are the owner of the conversation (sharer) and have shared it with edit permission
        c.execute(
            """
            SELECT q.id, q.conversation_id, q.user_id as query_user_id
            FROM queries q
            LEFT JOIN shared_conversations sc ON q.conversation_id = sc.conversation_id 
                AND q.project_id = sc.project_id
            WHERE q.id = ?
            AND (
                q.user_id = ?  -- User owns the query
                OR (
                    sc.recipient_id = ?  -- Query is in a conversation shared with user
                    AND sc.permission = 'edit'  -- AND user has edit permission
                )
                OR (
                    sc.sharer_id = ?  -- User is the sharer/owner of the conversation
                    AND sc.permission = 'edit'  -- AND they gave edit permission
                )
            )
        """,
            (
                query_id,
                current_user["user_id"],
                current_user["user_id"],
                current_user["user_id"],
            ),
        )

        existing_query = c.fetchone()

        if not existing_query:
            conn.close()
            raise HTTPException(
                status_code=403,  # Forbidden, not 404
                detail="You don't have permission to edit this query or query not found",
            )

        query_id_db, conversation_id, query_user_id = existing_query

        # Build update query dynamically based on provided fields
        update_fields = []
        update_values = []

        if req.query is not None:
            update_fields.append("query = ?")
            update_values.append(req.query)

        if req.answer is not None:
            update_fields.append("answer = ?")
            update_values.append(req.answer)

        if not update_fields:
            conn.close()
            raise HTTPException(status_code=400, detail="No fields to update")

        # Add query_id to values
        update_values.append(query_id)

        # Execute update
        update_query_sql = f"UPDATE queries SET {', '.join(update_fields)} WHERE id = ?"
        c.execute(update_query_sql, update_values)
        conn.commit()

        # Remove conversation from memory cache so it gets reloaded from DB
        if conversation_id in conversation_memory:
            del conversation_memory[conversation_id]

        conn.close()
        return {"status": "success", "message": "Query updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        conn.close()
        print(f"Error updating query {query_id}: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to update query: {str(e)}")


# ----------------------------
# Regeneration Endpoint
# ----------------------------
class RegenerateRequest(BaseModel):
    project_id: int
    query: str
    query_id: str  # Existing query ID to update
    conversation_id: Optional[str] = None
    database_engine: Optional[str] = None


@app.post("/chat/regenerate")
async def regenerate_chat(
    req: RegenerateRequest, current_user: dict = Depends(get_current_user)
):
    """
    Regenerate response for an existing query (updates existing record instead of creating new one)
    User can regenerate if:
    1. They own the query
    2. OR they have edit permission on the conversation
    3. OR they are the sharer/owner of the conversation with edit permission
    """
    user_id = current_user["user_id"]
    conversation_id = req.conversation_id or str(uuid.uuid4())

    # Verify the query exists and user has permission to regenerate it
    conn = get_connection()
    c = conn.cursor()

    # Check if user has permission to regenerate this query
    c.execute(
        """
        SELECT q.id, q.created_at, q.user_id as query_user_id
        FROM queries q
        LEFT JOIN shared_conversations sc ON q.conversation_id = sc.conversation_id 
            AND q.project_id = sc.project_id
        WHERE q.id = ?
        AND q.project_id = ?
        AND (
            q.user_id = ?  -- User owns the query
            OR (
                sc.recipient_id = ?  -- Query is in a conversation shared with user
                AND sc.permission = 'edit'  -- AND user has edit permission
            )
            OR (
                sc.sharer_id = ?  -- User is the sharer/owner of the conversation
                AND sc.permission = 'edit'  -- AND they gave edit permission
            )
        )
    """,
        (req.query_id, req.project_id, user_id, user_id, user_id),
    )

    existing_query = c.fetchone()

    if not existing_query:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Query not found or you don't have permission to regenerate it",
        )

    query_id, query_created_at, query_user_id = existing_query
    conn.close()

    safe_query = redact_pii(req.query)
    safe_query = sanitize_user_query(safe_query)
    start_time = time.time()

    # Initialize memory if missing
    conversation_memory[conversation_id] = []
    conn = get_connection()
    c = conn.cursor()

    # Get conversation history - include queries that user has access to
    c.execute(
        """
    SELECT DISTINCT q.id, q.query, q.answer, q.user_id, u.username
    FROM queries q
    LEFT JOIN users u ON q.user_id = u.id
    WHERE q.conversation_id = ? AND q.project_id = ?
    AND q.created_at < ?
    AND (
        q.user_id = ? 
        OR EXISTS (
            SELECT 1 FROM shared_conversations sc
            WHERE sc.conversation_id = q.conversation_id 
            AND sc.project_id = q.project_id
            AND sc.recipient_id = ?
            AND sc.sharer_id = q.user_id
            AND sc.permission IN ('view', 'edit')
        )
        OR EXISTS (
            SELECT 1 FROM shared_conversations sc2
            WHERE sc2.conversation_id = q.conversation_id 
            AND sc2.project_id = q.project_id
            AND sc2.sharer_id = ?  -- User is the sharer
            AND sc2.permission IN ('view', 'edit')  -- They gave view or edit permission
            AND q.user_id != ?  -- Query belongs to someone else
        )
    )
    ORDER BY q.created_at
""",
        (
            conversation_id,
            req.project_id,
            query_created_at,
            user_id,
            user_id,
            user_id,
            user_id,
        ),
    )

    rows = c.fetchall()
    conn.close()

    for q_id, q, a, user_id_row, username in rows:
        conversation_memory[conversation_id].append({"role": "user", "content": q})
        conversation_memory[conversation_id].append({"role": "assistant", "content": a})

    memory = conversation_memory[conversation_id]
    memory.append({"role": "user", "content": safe_query})

    # Initialize state for LangGraph
    initial_state = AgentState(
        user_query=safe_query,
        project_id=req.project_id,
        conversation_id=conversation_id,
        user_id=user_id,
        database_engine=req.database_engine,
        memory=memory,
        query_intent="",
        next_steps=[],
        retrieval_mode="",
        required_documents=[],
        keyword_search_words=[],
        generated_response="",
        contexts=[],
        attempt_count=0,
        is_fallback=False,
        current_step="start",
        rag_metrics={},
        validation_result="",
        needs_improvement=False,
        retry_target="none",
        improvement_suggestions=[],
        reflection_count=0,
        sql_complexity_score=0,  # 0-100 score
        complexity_reasons=[],  # List of complexity issues
        needs_simplification=False,  # Whether simplification is needed
    )

    config = {"configurable": {"thread_id": conversation_id}}

    # Streaming response generator for regeneration
    async def stream_and_update():
        # Send initial status
        yield "[[[STATUS]]]🔄 Regenerating response...---SEPARATOR---"

        # Track previous status to avoid duplicates
        previous_status = ""

        # Stream status updates during workflow execution
        for current_state in agent_workflow.stream(initial_state, config=config):
            for node_name, state in current_state.items():
                if (
                    "current_status" in state
                    and state["current_status"] != previous_status
                ):
                    yield f"[[[STATUS]]]{state['current_status']}---SEPARATOR---"
                    await asyncio.sleep(0.01)  # allow flush to client
                    previous_status = state["current_status"]
            final_state = state

        workflow_state = (
            final_state
            if final_state
            else agent_workflow.invoke(initial_state, config=config)
        )

        # Get the final response
        full_answer = workflow_state.get("generated_response", "")

        # Calculate response time
        response_time = time.time() - start_time

        # Store assistant response in memory
        memory.append({"role": "assistant", "content": full_answer})

        # Truncate memory if needed
        if len(memory) > MAX_MEMORY_LENGTH:
            conversation_memory[conversation_id] = memory[-MAX_MEMORY_LENGTH:]

        rag_metrics = workflow_state.get("rag_metrics", {})

        # UPDATE existing query instead of creating new one
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
            UPDATE queries 
            SET query = ?, answer = ?, response_time = ?, attempts = ?, is_fallback = ?,
                precision1 = ?, precision3 = ?, precision5 = ?, 
                claim_support = ?, static_exec = ?, query_coverage = ?, num_claims = ?,
                database_engine = ?
            WHERE id = ?
        """,
            (
                safe_query,  # Update the query (in case it was edited)
                full_answer,  # Update the answer
                response_time,
                workflow_state.get("attempt_count", 1),
                workflow_state.get("is_fallback", False),
                rag_metrics.get("Precision@1"),
                rag_metrics.get("Precision@3"),
                rag_metrics.get("Precision@5"),
                rag_metrics.get("ClaimSupportRate"),
                rag_metrics.get("StaticExecScore"),
                rag_metrics.get("QueryCoverage"),
                rag_metrics.get("NumClaims"),
                req.database_engine,  # Update the database engine if changed
                req.query_id,  # The existing query ID
            ),
        )
        conn.commit()
        conn.close()

        # Send the COMPLETE response at the end
        yield f"{full_answer}---SEPARATOR---"

        # Send metadata at the end (with the same query_id)
        meta = {
            "contexts": workflow_state.get("contexts", []),
            "query_id": req.query_id,
            "conversation_id": conversation_id,
        }
        yield f"[[[META]]]{json.dumps(meta)}---SEPARATOR---"

    return StreamingResponse(stream_and_update(), media_type="text/plain")


# ----------------------------
# Feedback model
# ----------------------------
class Feedback(BaseModel):
    query_id: str
    project_id: int
    query: str = ""
    answer: str
    contexts: Any = []
    feedback: Literal["up", "down"]  # "up" or "down"
    comment: str = ""
    user_id: str


# ----------------------------
# Load existing feedback
# ----------------------------
if os.path.exists(FEEDBACK_FILE):
    with open(FEEDBACK_FILE, "r") as f:
        feedback_store = json.load(f)
else:
    feedback_store = []


# ----------------------------
# Feedback endpoint
# ----------------------------
@app.post("/feedback")
async def submit_feedback(
    item: Feedback, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        contexts = item.contexts
        if isinstance(contexts, str):
            try:
                contexts = json.loads(contexts)  # parse JSON string
            except json.JSONDecodeError:
                contexts = [contexts]  # wrap raw string in list
        elif contexts is None:
            contexts = []
        elif not isinstance(contexts, list):
            contexts = [contexts]  # wrap anything else in list

        item.contexts = contexts
        # --- Load existing feedback ---
        if os.path.exists(FEEDBACK_FILE):
            with open(FEEDBACK_FILE, "r") as f:
                feedback_store = json.load(f)
        else:
            feedback_store = []

        # --- Save new feedback entry ---
        fb_entry = {
            "id": str(uuid.uuid4()),
            "query_id": item.query_id,
            "project_id": item.project_id,
            "query": item.query,
            "answer": item.answer,
            "contexts": item.contexts,
            "feedback": item.feedback,
            "comment": item.comment,
            "user_id": user_id,
        }
        feedback_store.append(fb_entry)

        with open(FEEDBACK_FILE, "w") as f:
            json.dump(feedback_store, f, indent=2)

        # --- Save feedback to SQLite ---
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO feedback (id, query_id, project_id, answer, contexts, feedback, comment, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                fb_entry["id"],
                fb_entry["query_id"],
                fb_entry["project_id"],
                fb_entry["answer"],
                json.dumps(fb_entry["contexts"], separators=(",", ":")),
                fb_entry["feedback"],
                fb_entry["comment"],
                fb_entry["user_id"],
            ),
        )
        conn.commit()
        conn.close()

        return {"status": "success", "message": "Feedback stored", "id": fb_entry["id"]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error storing feedback: {e}")


# ----------------------------
# Get Conversation title
# ----------------------------

# Add these imports at the top if not present
import hashlib
import re

# Store generated titles to avoid duplicates
conversation_titles_cache = {}


def generate_unique_conversation_title(
    user_query: str, assistant_response: str = "", existing_titles: list = None
) -> str:
    """Generate a unique, descriptive title for conversation."""

    # Create a hash of the conversation for caching
    query_hash = hashlib.md5(user_query.encode()).hexdigest()[:8]

    # Check cache first
    if query_hash in conversation_titles_cache:
        return conversation_titles_cache[query_hash]

    # Prepare context for LLM
    prompt = f"""
    Create a UNIQUE, descriptive title for this SQL/data conversation.
    
    USER QUERY: "{user_query[:200]}"
    
    ASSISTANT TASK: {assistant_response[:150] if assistant_response else "Generating SQL query"}
    
    Title Guidelines:
    1. Be 3-6 words maximum
    2. Make it specific and memorable
    3. Include the main topic/entity
    4. Use creative but professional language
    5. NO generic words like "Conversation", "Chat", "Query"
    
    Generate a title that would help someone remember THIS specific conversation.
    Return ONLY the title text.
    """

    try:
        result = call_llm(prompt)

        # Clean up response
        title = result.strip()
        title = re.sub(r'^["\'«»]|["\'»]$', "", title)  # Remove quotes
        title = re.sub(r"^Title[:]?\s*", "", title, flags=re.IGNORECASE)
        title = title.split("\n")[0].strip()

        # Ensure uniqueness
        existing_titles = existing_titles or []
        base_title = title

        # Add counter if duplicate
        counter = 1
        while title in existing_titles:
            title = f"{base_title} #{counter}"
            counter += 1
            if counter > 10:  # Safety limit
                break

        # Validate and store
        if 5 <= len(title) <= 80:  # Reasonable length
            conversation_titles_cache[query_hash] = title
            return title
        else:
            return generate_fallback_unique_title(user_query, existing_titles)

    except Exception as e:
        print(f"Title generation error: {e}")
        return generate_fallback_unique_title(user_query, existing_titles)


def generate_fallback_unique_title(query: str, existing_titles: list = None) -> str:
    """Generate unique fallback title."""

    # Extract entities from query
    entities = []

    # Look for table mentions
    table_match = re.search(r"\b(from|table|join)\s+(\w+)", query.lower())
    if table_match:
        entities.append(table_match.group(2).title())

    # Look for metrics
    metrics = re.findall(r"\b(count|sum|avg|average|total|max|min)\b", query.lower())
    if metrics:
        entities.append(metrics[0].title())

    # Look for time periods
    time_periods = re.findall(
        r"\b(daily|weekly|monthly|quarterly|yearly|202\d|q[1-4])\b", query.lower()
    )
    if time_periods:
        entities.append(time_periods[0].title())

    # Look for business terms
    business_terms = re.findall(
        r"\b(sales|revenue|customer|product|order|user|employee|inventory)\b",
        query.lower(),
    )
    if business_terms:
        entities.append(business_terms[0].title())

    # Build title
    if len(entities) >= 2:
        base_title = f"{entities[0]} {entities[1]} Analysis"
    elif entities:
        base_title = f"{entities[0]} Data Exploration"
    else:
        # Use query words
        words = [w for w in query.split() if len(w) > 3][:2]
        if words:
            base_title = f"{' '.join(words).title()} Investigation"
        else:
            base_title = "Data Intelligence Session"

    # Ensure uniqueness
    existing_titles = existing_titles or []
    title = base_title
    counter = 1

    while title in existing_titles:
        title = f"{base_title} #{counter}"
        counter += 1

    return title


# ----------------------------
# API Endpoint: Get all conversations/queries
# ----------------------------
@app.get("/queryhistory/{project_id}")
def query_history(project_id: str, current_user: dict = Depends(get_current_user)):
    """
    Fetch all queries (conversation history) for the user in a specific project.
    """
    conn = get_connection()
    c = conn.cursor()
    user_id = current_user["user_id"]

    try:
        project_id_int = int(project_id)

        c.execute(
            """
        SELECT DISTINCT 
            q.id, 
            q.conversation_id, 
            q.project_id, 
            q.query, 
            q.answer, 
            q.user_id,
            q.response_time, 
            q.created_at, 
            q.attempts, 
            q.is_fallback, 
            q.database_engine,
            u.username as query_username,
            -- Get conversation title from conversation_metadata
            COALESCE(
                cm.title,
                (SELECT query FROM queries q2 
                WHERE q2.conversation_id = q.conversation_id 
                AND q2.project_id = q.project_id 
                ORDER BY q2.created_at ASC LIMIT 1),
                'Untitled Conversation'
            ) as conversation_title,
            -- Access type (permission) - THIS SHOULD BE 'edit' or 'view'
            CASE 
                WHEN q.user_id = ? THEN 'edit'
                WHEN EXISTS (
                    SELECT 1 FROM shared_conversations sc 
                    WHERE sc.conversation_id = q.conversation_id 
                    AND sc.project_id = q.project_id
                    AND sc.recipient_id = ?
                    AND sc.permission = 'edit'
                ) THEN 'edit'
                WHEN EXISTS (
                    SELECT 1 FROM shared_conversations sc 
                    WHERE sc.conversation_id = q.conversation_id 
                    AND sc.project_id = q.project_id
                    AND sc.recipient_id = ?
                    AND sc.permission = 'view'
                ) THEN 'view'
                ELSE 'edit'
            END as access_type,
            -- Who shared this conversation (username or NULL)
            CASE 
                WHEN q.user_id = ? THEN NULL
                ELSE (
                    SELECT sharer.username 
                    FROM shared_conversations sc2 
                    JOIN users sharer ON sc2.sharer_id = sharer.id
                    WHERE sc2.conversation_id = q.conversation_id 
                    AND sc2.project_id = q.project_id
                    AND sc2.recipient_id = ?
                    LIMIT 1
                )
            END as shared_by_username
        FROM queries q
        LEFT JOIN users u ON q.user_id = u.id
        LEFT JOIN conversation_metadata cm ON q.conversation_id = cm.conversation_id 
            AND q.project_id = cm.project_id
        WHERE q.project_id = ?
        AND (
            q.user_id = ? 
            OR EXISTS (
                SELECT 1 FROM shared_conversations sc3
                WHERE sc3.conversation_id = q.conversation_id 
                AND sc3.project_id = q.project_id
                AND sc3.recipient_id = ?
            )
            OR EXISTS (
                SELECT 1 FROM shared_conversations sc4
                WHERE sc4.conversation_id = q.conversation_id 
                AND sc4.project_id = q.project_id
                AND sc4.sharer_id = ?
            )
        )
        ORDER BY q.created_at DESC
    """,
            (
                user_id,  # For owner check
                user_id,  # For edit permission check
                user_id,  # For view permission check
                user_id,  # For shared_by check
                user_id,  # For shared_by check
                project_id_int,  # WHERE project_id
                user_id,  # WHERE user's own queries
                user_id,  # WHERE shared WITH user
                user_id,  # WHERE user is sharer
            ),
        )

        rows = c.fetchall()

        # After fetching rows, update the conversation building:
        conversations = [
            {
                "id": row[0],
                "conversation_id": row[1],
                "project_id": row[2],
                "query": row[3] or "",
                "answer": row[4] or "",
                "user_id": row[5],
                "response_time": row[6],
                "created_at": row[7],
                "attempts": row[8],
                "is_fallback": bool(row[9]) if row[9] is not None else False,
                "database_engine": row[10],
                "query_username": row[11],
                "conversation_title": (
                    row[12] if row[12] and row[12] not in ["edit", "view"] else ""
                ),  # Filter out permission strings
                "access_type": (
                    row[13] if row[13] in ["edit", "view"] else "edit"
                ),  # Ensure valid access_type
                "shared_by_username": (
                    row[14] if row[14] and row[14] not in ["edit", "view"] else ""
                ),  # Filter out permission strings
            }
            for row in rows
        ]

        # Debug: Print sample data
        if conversations:
            print(f"DEBUG: Sample conversation data:")
            print(f"  Title: {conversations[0].get('conversation_title', 'NO TITLE')}")
            print(f"  Access Type: {conversations[0].get('access_type', 'NO ACCESS')}")
            print(
                f"  Shared By: {conversations[0].get('shared_by_username', 'NOT SHARED')}"
            )
            print(f"  Query: {conversations[0].get('query', 'NO QUERY')[:50]}...")

        return conversations

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching conversations: {str(e)}"
        )
    finally:
        conn.close()


@app.get("/analytics-data")
def get_analytics_data(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access forbidden: Admins only.")

    try:
        queries, feedback, projects, documents, users = load_data()

        def clean_records(df):
            if df is None or (hasattr(df, "empty") and df.empty):
                return []
            try:
                records = json.loads(df.to_json(orient="records"))
            except Exception:
                records = df.replace({np.nan: None}).to_dict(orient="records")

            def sanitize_value(v):
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    return None
                if isinstance(v, dict):
                    return {k: sanitize_value(val) for k, val in v.items()}
                if isinstance(v, list):
                    return [sanitize_value(val) for val in v]
                return v

            return [sanitize_value(r) for r in records]

        users_subset = (
            users[["id", "username", "role"]]
            if ("id" in users.columns and "username" in users.columns and "role" in users.columns)
            else users
        )

        return {
            "queries": clean_records(queries),
            "feedback": clean_records(feedback),
            "projects": clean_records(projects),
            "documents": clean_records(documents),
            "users": clean_records(users_subset),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to load analytics data: {str(e)}"
        )


# Add Jira connection model
import subprocess
import asyncio
from contextlib import asynccontextmanager

# Global MCP process
mcp_process = None
mcp_client = None


class JiraConnectionRequest(BaseModel):
    jira_url: str
    username: str
    api_token: str
    is_default: bool = False


class JiraImportRequest(BaseModel):
    issue_key: str
    target_project_id: Optional[int] = None
    new_project_name: Optional[str] = None


class MCPJiraClient:
    """MCP Client for Jira with proper protocol handling"""

    def __init__(self, mcp_server_path: str = None):
        self.mcp_server_path = mcp_server_path or os.path.join(BASE_DIR, "jira_mcp_server.py")
        self.process = None
        self.connection_ids: dict[str, str] = {}
        self.initialized = False

    async def _read_stderr(self):
        while True:
            if not self.process:
                break
            line = await self.process.stderr.readline()
            if line is None:  # Process ended
                break
            if not line:  # Empty line
                continue
            print("MCP STDERR:", line.decode().strip())

    async def start_server(self):
        """Start MCP server"""
        try:
            if self.process and self.process.returncode is None:
                return
            print("Starting MCP server...")
            import sys

            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                self.mcp_server_path,
                stdout=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            asyncio.create_task(self._read_stderr())
            print("MCP server started")

            # Initialize the connection
            await self._initialize_connection()

        except Exception as e:
            print(f"Failed to start MCP server: {e}")
            raise

    async def _initialize_connection(self):
        """Initialize MCP connection with proper handshake"""
        try:
            # Determine supported MCP protocol version
            try:
                import mcp.types as mcp_types
                proto_ver = getattr(mcp_types, "LATEST_PROTOCOL_VERSION", "2025-11-25")
            except Exception:
                proto_ver = "2025-11-25"

            # Send initialize request
            init_id = str(uuid.uuid4())
            init_rpc = {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": proto_ver,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "jira-fastapi-client", "version": "1.0.0"},
                },
            }

            print(f"SENDING INITIALIZE: {init_id} with protocolVersion: {proto_ver}")
            init_line = json.dumps(init_rpc) + "\n"

            if self.process.stdin:
                self.process.stdin.write(init_line.encode())
                await self.process.stdin.drain()

                # Wait for initialize response
                init_response = await self._wait_for_response(init_id, timeout=10)
                if "error" in init_response:
                    print(f"Initialize failed: {init_response['error']}")
                    return False

                print("✅ MCP server initialized successfully")
                self.initialized = True

                # Send notifications/initialized per MCP spec
                notif_rpc = {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }
                self.process.stdin.write((json.dumps(notif_rpc) + "\n").encode())
                await self.process.stdin.drain()

                # Optional: List tools to verify connection
                list_id = str(uuid.uuid4())
                list_rpc = {
                    "jsonrpc": "2.0",
                    "id": list_id,
                    "method": "tools/list",
                    "params": {},
                }

                print(f"SENDING TOOLS/LIST: {list_id}")
                list_line = json.dumps(list_rpc) + "\n"
                self.process.stdin.write(list_line.encode())
                await self.process.stdin.drain()

                # Wait for tools/list response
                list_response = await self._wait_for_response(list_id, timeout=10)
                if "error" not in list_response:
                    print("✅ Tools listed successfully")
                else:
                    print(
                        f"Tools/list warning: {list_response.get('error', 'Unknown error')}"
                    )

                return True
            else:
                print("❌ Process stdin not available")
                return False

        except Exception as e:
            print(f"❌ Initialization error: {e}")
            return False

    async def _wait_for_response(self, req_id: str, timeout: int = 30) -> dict:
        """Wait for a specific response by ID"""
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if self.process.stdout:
                try:
                    raw_line = await asyncio.wait_for(
                        self.process.stdout.readline(), timeout=5
                    )
                except asyncio.TimeoutError:
                    continue

                if raw_line is None:
                    print("❌ Process stdout returned None - process may have ended")
                    return {"error": "MCP server process ended unexpectedly"}

                if not raw_line:
                    await asyncio.sleep(0.1)
                    continue

                line_str = raw_line.decode().strip()
                # print(f"RAW RESPONSE: {line_str}")

                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                # Check if this is our response
                if msg.get("id") == req_id:
                    print(f"✅ MATCHED RESPONSE for {req_id}")

                    if "error" in msg:
                        return {"error": msg["error"]}

                    result = msg.get("result", {})

                    # Extract content from result
                    content = result.get("content", [])

                    if content:
                        # Handle different content formats
                        text_content = ""
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    text_content = item.get("text", "")
                                elif "text" in item:
                                    text_content = item["text"]
                            elif isinstance(item, str):
                                text_content = item

                        if text_content:
                            try:
                                parsed = json.loads(text_content)
                                return parsed
                            except json.JSONDecodeError:
                                return {"raw": text_content}

                    return {"result": result}

        return {"error": "Timeout waiting for response"}

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call MCP tool with proper initialization"""
        print(f"🔧 Calling MCP tool: {tool_name}")
        print(f"📋 Arguments: {arguments}")

        try:
            # Ensure server is running and initialized
            if not self.process or self.process.returncode is not None:
                await self.start_server()

            # Check if process is still alive
            if self.process and self.process.returncode is not None:
                print("❌ MCP server process has terminated")
                return {"error": "MCP server process terminated"}

            # Ensure we're initialized
            if not self.initialized:
                print("🔄 Re-initializing connection...")
                if not await self._initialize_connection():
                    return {"error": "Failed to initialize MCP connection"}

            # Send tool call
            req_id = str(uuid.uuid4())
            rpc = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }

            print(f"📤 SENDING TOOL CALL: {tool_name} (ID: {req_id})")
            line = json.dumps(rpc) + "\n"

            if self.process.stdin:
                self.process.stdin.write(line.encode())
                await self.process.stdin.drain()
            else:
                return {"error": "Process stdin not available"}

            # Wait for response
            response = await self._wait_for_response(req_id, timeout=30)
            print(f"📥 TOOL RESPONSE: {response}")
            return response

        except Exception as e:
            print(f"❌ Error in call_tool: {str(e)}")
            import traceback

            traceback.print_exc()
            return {"error": str(e)}

    async def stop_server(self):
        """Stop MCP server"""
        if self.process:
            try:
                self.process.terminate()
                await self.process.wait()
                self.initialized = False
                print("MCP server stopped")
            except Exception as e:
                print(f"Error stopping MCP server: {e}")


# Initialize MCP client
mcp_jira_client = MCPJiraClient()


@app.on_event("startup")
async def startup():
    try:
        print("Initializing database...")
        init_db()
        # Just start the server, initialization will happen on first tool call
        await mcp_jira_client.start_server()
        print("MCP server startup initiated")
    except Exception as e:
        print(f"startup failed: {e}")


# API Endpoints


@app.post("/jira/connect")
async def connect_jira(
    req: JiraConnectionRequest, current_user: dict = Depends(get_current_user)
):
    """Connect to Jira via MCP"""
    user_id = current_user["user_id"]

    try:
        print(f"Connecting to Jira: {req.jira_url}")

        # Call MCP tool
        result = await mcp_jira_client.call_tool(
            "connect_jira",
            {
                "jira_url": req.jira_url,
                "username": req.username,
                "api_token": req.api_token,
            },
        )
        print(f"MCP connect_jira result: {result}")
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        connection_id_mcp = result["connection_id"]
        mcp_jira_client.connection_ids[str(req.jira_url)] = connection_id_mcp

        # Save connection to database
        conn = get_connection()
        c = conn.cursor()

        if req.is_default:
            c.execute(
                "UPDATE jira_connections SET is_default=0 WHERE user_id=?", (user_id,)
            )

        encrypted_token = encrypt_password(req.api_token)
        c.execute(
            """
            INSERT INTO jira_connections (user_id, jira_url, username, api_token, is_default)
            VALUES (?, ?, ?, ?, ?)
        """,
            (user_id, req.jira_url, req.username, encrypted_token, req.is_default),
        )

        conn.commit()
        conn.close()

        return {
            "status": "connected",
            "message": "Successfully connected to Jira via MCP",
        }

    except Exception as e:
        print(f"Connection error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/jira/connections")
async def get_jira_connections(current_user: dict = Depends(get_current_user)):
    """Get user's Jira connections"""
    user_id = current_user["user_id"]
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        "SELECT id, jira_url, username, is_default, created_at FROM jira_connections WHERE user_id=?",
        (user_id,),
    )
    connections = [
        {
            "id": row[0],
            "jira_url": row[1],
            "username": row[2],
            "is_default": bool(row[3]),
            "created_at": row[4],
        }
        for row in c.fetchall()
    ]

    conn.close()
    return connections


@app.get("/jira/projects")
async def get_jira_projects(
    connection_id: int = Query(...), current_user: dict = Depends(get_current_user)
):
    """Get projects from Jira via MCP"""
    user_id = current_user["user_id"]
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        "SELECT jira_url, username, api_token FROM jira_connections WHERE id=? AND user_id=?",
        (connection_id, user_id),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Jira connection not found")

    jira_url, username, api_token = row
    api_token = decrypt_password(api_token)
    conn.close()

    try:
        if jira_url in mcp_jira_client.connection_ids:
            connection_id_mcp = mcp_jira_client.connection_ids[jira_url]
        else:
            # Connect via MCP
            result = await mcp_jira_client.call_tool(
                "connect_jira",
                {"jira_url": jira_url, "username": username, "api_token": api_token},
            )

            if "error" in result:
                raise HTTPException(status_code=400, detail=result["error"])

            connection_id_mcp = result.get("connection_id")
            mcp_jira_client.connection_ids[jira_url] = connection_id_mcp

        # Get projects
        projects_result = await mcp_jira_client.call_tool(
            "get_projects", {"connection_id": connection_id_mcp}
        )
        print("GET_PROJECTS RAW:", projects_result)
        return projects_result.get("projects", [])

    except Exception as e:
        print(f"Error getting projects: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/jira/issues")
async def get_jira_issues(
    connection_id: int = Query(...),
    project_key: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get issues from a Jira project via MCP with full details for filtering"""
    user_id = current_user["user_id"]
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        "SELECT jira_url, username, api_token FROM jira_connections WHERE id=? AND user_id=?",
        (connection_id, user_id),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Jira connection not found")

    jira_url, username, api_token = row
    api_token = decrypt_password(api_token)
    conn.close()

    try:
        if jira_url in mcp_jira_client.connection_ids:
            connection_id_mcp = mcp_jira_client.connection_ids[jira_url]
        else:
            # Connect via MCP
            result = await mcp_jira_client.call_tool(
                "connect_jira",
                {"jira_url": jira_url, "username": username, "api_token": api_token},
            )

            if "error" in result:
                raise HTTPException(status_code=400, detail=result["error"])

            connection_id_mcp = result.get("connection_id")
            mcp_jira_client.connection_ids[jira_url] = connection_id_mcp

        # Get issues with more details for filtering
        issues_result = await mcp_jira_client.call_tool(
            "get_issues",
            {
                "connection_id": connection_id_mcp,
                "project_key": project_key,
                "include_details": True,  # Add this parameter to get more fields
            },
        )

        if "error" in issues_result:
            raise HTTPException(status_code=400, detail=issues_result["error"])

        issues = issues_result.get("issues", [])

        # Enhanced issue data structure for filtering
        enhanced_issues = []
        for issue in issues:
            enhanced_issue = {
                "key": issue.get("key"),
                "summary": issue.get("summary"),
                "description": issue.get("description", ""),
                "issue_type": issue.get("issue_type"),
                "status": issue.get("status"),
                "priority": issue.get("priority"),
                "assignee": issue.get("assignee"),
                "reporter": issue.get("reporter"),
                "labels": issue.get("labels", []),
                "created": issue.get("created"),
                "updated": issue.get("updated"),
                "duedate": issue.get("duedate"),
                "attachments": issue.get("attachments", []),
                "comments": issue.get("comments", []),
                "components": issue.get("components", []),
                "fix_versions": issue.get("fix_versions", []),
                "affects_versions": issue.get("affects_versions", []),
                "resolution": issue.get("resolution"),
            }
            enhanced_issues.append(enhanced_issue)

        return {"issues": enhanced_issues}

    except Exception as e:
        print(f"Error getting issues: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/jira/import-issue")
async def import_jira_issue(
    req: JiraImportRequest,
    connection_id: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Import a Jira issue into the application via MCP - attachments are optional"""
    user_id = current_user["user_id"]

    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT jira_url, username, api_token FROM jira_connections WHERE id=? AND user_id=?",
        (connection_id, user_id),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Jira connection not found")

    jira_url, username, api_token = row
    api_token = decrypt_password(api_token)

    try:
        if jira_url in mcp_jira_client.connection_ids:
            connection_id_mcp = mcp_jira_client.connection_ids[jira_url]
        else:
            # Connect via MCP
            result = await mcp_jira_client.call_tool(
                "connect_jira",
                {"jira_url": jira_url, "username": username, "api_token": api_token},
            )

            if "error" in result:
                raise HTTPException(status_code=400, detail=result["error"])

            connection_id_mcp = result.get("connection_id")
            mcp_jira_client.connection_ids[jira_url] = connection_id_mcp

        # Get issue details
        issue_result = await mcp_jira_client.call_tool(
            "get_issue",
            {"connection_id": connection_id_mcp, "issue_key": req.issue_key},
        )

        if "error" in issue_result:
            raise HTTPException(status_code=404, detail=issue_result["error"])

        issue = issue_result.get("issue", {})

        # Determine target project
        target_project_id = req.target_project_id
        if not target_project_id and req.new_project_name:
            c.execute(
                "INSERT INTO projects (name, created_by) VALUES (?, ?)",
                (req.new_project_name, user_id),
            )
            target_project_id = c.lastrowid
            c.execute(
                "INSERT INTO user_project (user_id, project_id, role) VALUES (?, ?, ?)",
                (user_id, target_project_id, "user"),
            )
            conn.commit()

        if not target_project_id:
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="Either target_project_id or new_project_name must be provided",
            )

        # Save imported issue (ALWAYS import the issue, even if attachments fail)
        attachments = issue.get("attachments", [])
        c.execute(
            """
            INSERT INTO jira_imported_issues 
            (project_id, jira_issue_key, jira_issue_summary, jira_issue_description, 
             jira_issue_type, jira_issue_status, jira_issue_assignee, jira_issue_attachments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                target_project_id,
                issue.get("key"),
                issue.get("summary"),
                issue.get("description", ""),
                issue.get("issue_type"),
                issue.get("status"),
                issue.get("assignee", "Unassigned"),
                json.dumps(attachments),
            ),
        )
        imported_issue_id = c.lastrowid

        # Download and process attachments - BUT DON'T FAIL THE WHOLE IMPORT IF ATTACHMENTS FAIL
        successful_attachments = 0
        failed_attachments = []

        if attachments:
            proj_dir = os.path.join(RAW_DIR, str(target_project_id))
            os.makedirs(proj_dir, exist_ok=True)

            for attachment in attachments:
                print(f"🔄 Processing attachment: {attachment['filename']}")

                # Download via MCP
                download_result = await mcp_jira_client.call_tool(
                    "download_attachment",
                    {
                        "connection_id": connection_id_mcp,
                        "attachment_url": attachment["url"],
                        "filename": attachment["filename"],
                    },
                )

                # Check if download was successful
                if (
                    "error" not in download_result
                    and download_result.get("status") == "success"
                ):
                    import base64

                    file_content_b64 = download_result.get("file_content")
                    original_name = attachment["filename"]
                    filename = original_name
                    filepath = os.path.join(proj_dir, filename)

                    counter = 1
                    name, ext = os.path.splitext(original_name)
                    while os.path.exists(filepath):
                        filename = f"{name} ({counter}){ext}"
                        filepath = os.path.join(proj_dir, filename)
                        counter += 1
                    attachment["filename"] = filename  # Update to final filename

                    try:
                        with open(filepath, "wb") as f:
                            f.write(base64.b64decode(file_content_b64))

                        # Save document reference even if we can't process it fully
                        c.execute(
                            """
                            INSERT INTO documents (project_id, name, path, type, status, uploaded_by)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """,
                            (
                                target_project_id,
                                attachment["filename"],
                                filepath,
                                "jira_attachment",
                                "processing",
                                user_id,
                            ),
                        )
                        doc_id = c.lastrowid
                        conn.commit()

                        # Process document in background
                        try:
                            documents = load_and_split_document(filepath)

                            import threading

                            def background_index():
                                try:
                                    save_and_index(
                                        target_project_id,
                                        doc_id,
                                        attachment["filename"],
                                        filepath,
                                        "jira_attachment",
                                        documents,
                                    )
                                    print(
                                        f"✅ Successfully indexed: {attachment['filename']}"
                                    )
                                except Exception as e:
                                    print(
                                        f"⚠️ Indexing failed but file saved: {attachment['filename']} - {e}"
                                    )
                                    # Update status to indicate partial failure
                                    c.execute(
                                        "UPDATE documents SET status=? WHERE id=?",
                                        ("indexing_failed", doc_id),
                                    )
                                    conn.commit()

                            thread = threading.Thread(target=background_index)
                            thread.daemon = True
                            thread.start()

                        except Exception as processing_error:
                            print(
                                f"⚠️ Processing failed but file saved: {attachment['filename']} - {processing_error}"
                            )
                            c.execute(
                                "UPDATE documents SET status=? WHERE id=?",
                                ("processing_failed", doc_id),
                            )
                            conn.commit()

                        successful_attachments += 1

                    except Exception as e:
                        print(
                            f"❌ Failed to save attachment: {attachment['filename']} - {e}"
                        )
                        failed_attachments.append(attachment["filename"])

                else:
                    print(f"🚫 Attachment Failed: {attachment['filename']}")
                    failed_attachments.append(attachment["filename"])

                    # === DUPLICATE-SAFE RENAME FOR FAILED PLACEHOLDER ===
                    original_name = f"{attachment['filename']}"
                    filename = original_name
                    filepath = os.path.join(proj_dir, filename)

                    counter = 1
                    name, ext = os.path.splitext(original_name)
                    while os.path.exists(filepath):
                        filename = f"{name} ({counter}){ext}"
                        filepath = os.path.join(proj_dir, filename)
                        counter += 1

                    attachment["filename"] = filename  # Update to final filename

                    # Still create a placeholder document record for failed attachments
                    try:
                        placeholder_path = os.path.join(
                            proj_dir, f"{attachment['filename']}"
                        )
                        with open(placeholder_path, "w") as f:
                            f.write(f"Attachment Failed: {attachment['url']}")

                        c.execute(
                            """
                            INSERT INTO documents (project_id, name, path, type, status, uploaded_by)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """,
                            (
                                target_project_id,
                                f"{attachment['filename']}",
                                placeholder_path,
                                "jira_attachment",
                                "failed",
                                user_id,
                            ),
                        )
                        conn.commit()
                    except Exception as placeholder_error:
                        print(
                            f"⚠️ Could not create placeholder for failed attachment: {attachment['filename']}"
                        )

        conn.commit()
        conn.close()
        imported_attachments = [a["filename"] for a in attachments]

        return {
            "status": "imported",
            "project_id": target_project_id,
            "imported_issue_id": imported_issue_id,
            "issue_details": {
                "key": issue.get("key"),
                "summary": issue.get("summary"),
                "status": issue.get("status"),
            },
            "attachments": imported_attachments,
            "attachments_summary": {
                "successful": successful_attachments,
                "failed": failed_attachments,
                "total": len(attachments),
            },
            "message": f"Successfully imported issue {issue.get('key')} - {successful_attachments}/{len(attachments)} attachments processed",
        }

    except Exception as e:
        conn.close()
        print(f"❌ Import error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# In app_backend.py, update the ShareConversationRequest model:

from typing import List, Literal
from pydantic import BaseModel, Field


class ShareConversationRequest(BaseModel):
    conversation_id: str
    project_id: int
    shared_with_usernames: List[str] = Field(..., min_items=1)
    permission: Literal["view", "edit"] = "view"

    class Config:
        # This makes the model more flexible with extra fields
        extra = "ignore"


# Add this endpoint after other endpoints
@app.post("/conversations/share")
async def share_conversation(
    req: ShareConversationRequest, current_user: dict = Depends(get_current_user)
):
    """Share a conversation with other users"""
    print(f"DEBUG: Received share request: {req.dict()}")

    conn = get_connection()
    c = conn.cursor()

    try:
        # First, verify the table exists
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shared_conversations'"
        )
        table_exists = c.fetchone()
        print(f"DEBUG: shared_conversations table exists: {table_exists}")

        if not table_exists:
            # Try to create the table with correct column names
            print("DEBUG: Creating shared_conversations table...")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_conversations (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    project_id INTEGER NOT NULL,
                    sharer_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    permission TEXT DEFAULT 'edit',
                    shared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (sharer_id) REFERENCES users (id),
                    FOREIGN KEY (recipient_id) REFERENCES users (id),
                    FOREIGN KEY (project_id) REFERENCES projects (id)
                )
            """
            )
            conn.commit()
            print("DEBUG: Table created successfully")

        # Get the user IDs for the usernames
        if not req.shared_with_usernames:
            raise HTTPException(
                status_code=400, detail="No users specified to share with"
            )

        placeholders = ",".join(["?"] * len(req.shared_with_usernames))
        c.execute(
            f"""
            SELECT id, username FROM users 
            WHERE username IN ({placeholders}) 
            AND id != ?
        """,
            (*req.shared_with_usernames, current_user["user_id"]),
        )

        users_to_share = c.fetchall()
        print(f"DEBUG: Found users to share with: {users_to_share}")

        if not users_to_share:
            raise HTTPException(
                status_code=404, detail="No valid users found to share with"
            )

        # Verify the conversation exists
        c.execute(
            """
            SELECT 1 FROM queries 
            WHERE conversation_id = ? AND project_id = ?
            LIMIT 1
        """,
            (req.conversation_id, req.project_id),
        )

        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Conversation not found")

        shared_count = 0
        already_same_permission_count = 0

        for user_id, username in users_to_share:
            # Check if already shared - USING CORRECT COLUMN NAME 'recipient_id'
            c.execute(
                """
                SELECT id, permission FROM shared_conversations 
                WHERE conversation_id=? AND project_id=? AND recipient_id=?
            """,
                (req.conversation_id, req.project_id, user_id),
            )

            existing_share = c.fetchone()

            if existing_share:
                # Update existing share if permission is different
                existing_id, existing_permission = existing_share
                if existing_permission != req.permission:
                    c.execute(
                        """
                        UPDATE shared_conversations 
                        SET permission = ?
                        WHERE id = ?
                    """,
                        (req.permission, existing_id),
                    )
                    shared_count += 1
                    print(
                        f"DEBUG: Updated permission for user {username} from {existing_permission} to {req.permission}"
                    )
                else:
                    already_same_permission_count += 1
                    print(
                        f"DEBUG: Already shared with user {username} with same permission"
                    )
            else:
                # Create new share
                share_id = str(uuid.uuid4())
                c.execute(
                    """
                    INSERT INTO shared_conversations 
                    (id, conversation_id, project_id, sharer_id, recipient_id, permission)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        share_id,
                        req.conversation_id,
                        req.project_id,
                        current_user["user_id"],
                        user_id,
                        req.permission,
                    ),
                )
                shared_count += 1
                print(f"DEBUG: Shared with user {username} (ID: {user_id})")

        conn.commit()
        print(f"DEBUG: Successfully shared/updated {shared_count} user(s)")

        if shared_count > 0 and already_same_permission_count > 0:
            message = f"Shared with {shared_count} user(s). {already_same_permission_count} user(s) already had same permission."
        elif shared_count > 0:
            message = f"Shared with {shared_count} user(s)"
        elif already_same_permission_count > 0:
            message = f"All {already_same_permission_count} user(s) already had same permission"
        else:
            message = "No users were shared with"

        return {"status": "success", "message": message, "shared_count": shared_count}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        print(f"ERROR in share_conversation: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# =============================================
# DATABASE CONNECTION MANAGEMENT
# =============================================

from cryptography.fernet import Fernet
import base64
import hashlib
import psycopg2
import pymysql
import sqlite3 as sqlite_driver
import pyodbc
import snowflake.connector
from google.cloud import bigquery
from sqlalchemy import create_engine
from datetime import datetime
import time

# Encryption setup
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    # Generate a key from a secret (in production, use a fixed environment variable)
    secret_key = os.getenv("SECRET_KEY", "your-secret-key-here-change-in-production")
    ENCRYPTION_KEY = base64.urlsafe_b64encode(
        hashlib.sha256(secret_key.encode()).digest()
    )
cipher_suite = Fernet(ENCRYPTION_KEY)


def encrypt_password(password: str) -> str:
    """Encrypt password before storing"""
    return cipher_suite.encrypt(password.encode()).decode()


def decrypt_password(encrypted_password: str) -> str:
    """Decrypt password for execution"""
    return cipher_suite.decrypt(encrypted_password.encode()).decode()


# ----------------------------
# Database Connection Models
# ----------------------------
class DatabaseConnectionRequest(BaseModel):
    name: str
    db_type: Literal[
        "postgresql",
        "mysql",
        "sqlserver",
        "oracle",
        "sqlite",
        "snowflake",
        "bigquery",
        "redshift",
    ]
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl_enabled: bool = False
    is_default: bool = False


class DatabaseConnectionResponse(BaseModel):
    id: int
    name: str
    db_type: str
    host: Optional[str]
    port: Optional[int]
    database_name: Optional[str]
    username: Optional[str]
    ssl_enabled: bool
    is_default: bool
    created_at: str


class SQLExecutionRequest(BaseModel):
    connection_id: int
    sql: str
    limit_rows: Optional[int] = 100


class SQLExecutionResponse(BaseModel):
    success: bool
    columns: Optional[List[str]] = None
    rows: Optional[List[List[Any]]] = None
    row_count: Optional[int] = None
    error: Optional[str] = None
    execution_time: Optional[float] = None


# ----------------------------
# Database Connection CRUD
# ----------------------------
@app.post("/database/connections", response_model=DatabaseConnectionResponse)
async def create_database_connection(
    req: DatabaseConnectionRequest, current_user: dict = Depends(get_current_user)
):
    """Save a new database connection"""
    conn = get_connection()
    c = conn.cursor()

    try:
        # Encrypt password
        encrypted_password = encrypt_password(req.password) if req.password else None

        # If this is set as default, clear other defaults
        if req.is_default:
            c.execute(
                """
                UPDATE database_connections 
                SET is_default = 0 
                WHERE user_id = ? AND db_type = ?
            """,
                (current_user["user_id"], req.db_type),
            )

        # Insert new connection
        c.execute(
            """
            INSERT INTO database_connections 
            (user_id, name, db_type, host, port, database_name, username, encrypted_password, ssl_enabled, is_default)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                current_user["user_id"],
                req.name,
                req.db_type,
                req.host,
                req.port,
                req.database_name,
                req.username,
                encrypted_password,
                req.ssl_enabled,
                req.is_default,
            ),
        )

        connection_id = c.lastrowid
        conn.commit()

        return {
            "id": connection_id,
            "name": req.name,
            "db_type": req.db_type,
            "host": req.host,
            "port": req.port,
            "database_name": req.database_name,
            "username": req.username,
            "ssl_enabled": req.ssl_enabled,
            "is_default": req.is_default,
            "created_at": datetime.now().isoformat(),
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(
            status_code=400, detail=f"Failed to save connection: {str(e)}"
        )
    finally:
        conn.close()


@app.get("/database/connections")
async def get_database_connections(
    db_type: Optional[str] = None, current_user: dict = Depends(get_current_user)
):
    """Get all database connections for the user"""
    conn = get_connection()
    c = conn.cursor()

    try:
        if db_type:
            c.execute(
                """
                SELECT id, name, db_type, host, port, database_name, username, ssl_enabled, 
                       is_default,
                       created_at
                FROM database_connections 
                WHERE user_id = ? AND db_type = ?
                ORDER BY is_default DESC, created_at DESC
            """,
                (current_user["user_id"], db_type),
            )
        else:
            c.execute(
                """
                SELECT id, name, db_type, host, port, database_name, username, ssl_enabled,
                       is_default,
                       created_at
                FROM database_connections 
                WHERE user_id = ?
                ORDER BY db_type, is_default DESC, created_at DESC
            """,
                (current_user["user_id"],),
            )

        connections = []
        for row in c.fetchall():
            connections.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "db_type": row[2],
                    "host": row[3],
                    "port": row[4],
                    "database_name": row[5],
                    "username": row[6],
                    "ssl_enabled": bool(row[7]),
                    "is_default": bool(row[8]),
                    "created_at": row[9],
                }
            )

        return connections
    except Exception as e:
        print(f"Error fetching database connections: {e}")
        return []  # Return empty list instead of 500 error
    finally:
        conn.close()


@app.delete("/database/connections/{connection_id}")
async def delete_database_connection(
    connection_id: int, current_user: dict = Depends(get_current_user)
):
    """Delete a database connection"""
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        """
        DELETE FROM database_connections 
        WHERE id = ? AND user_id = ?
    """,
        (connection_id, current_user["user_id"]),
    )

    deleted = c.rowcount
    conn.commit()
    conn.close()

    if not deleted:
        raise HTTPException(status_code=404, detail="Connection not found")

    return {"status": "deleted", "id": connection_id}


@app.put("/database/connections/{connection_id}")
async def update_database_connection(
    connection_id: int,
    req: DatabaseConnectionRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update an existing database connection"""
    conn = get_connection()
    c = conn.cursor()

    try:
        # Check if connection exists and belongs to user
        c.execute(
            "SELECT id FROM database_connections WHERE id = ? AND user_id = ?",
            (connection_id, current_user["user_id"]),
        )
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Connection not found")

        # Encrypt password if provided
        encrypted_password = encrypt_password(req.password) if req.password else None

        # If this is set as default, clear other defaults
        if req.is_default:
            c.execute(
                """
                UPDATE database_connections 
                SET is_default = 0 
                WHERE user_id = ? AND db_type = ?
            """,
                (current_user["user_id"], req.db_type),
            )

        # Build update query dynamically
        update_fields = []
        update_values = []

        fields = [
            ("name", req.name),
            ("db_type", req.db_type),
            ("host", req.host),
            ("port", req.port),
            ("database_name", req.database_name),
            ("username", req.username),
            ("ssl_enabled", req.ssl_enabled),
            ("is_default", req.is_default),
        ]

        for field, value in fields:
            update_fields.append(f"{field} = ?")
            update_values.append(value)

        # Only update password if provided
        if encrypted_password:
            update_fields.append("encrypted_password = ?")
            update_values.append(encrypted_password)

        # Add connection_id to values
        update_values.append(connection_id)
        update_values.append(current_user["user_id"])

        # Execute update
        c.execute(
            f"""
            UPDATE database_connections 
            SET {', '.join(update_fields)}
            WHERE id = ? AND user_id = ?
        """,
            update_values,
        )

        conn.commit()

        return {
            "id": connection_id,
            "name": req.name,
            "db_type": req.db_type,
            "host": req.host,
            "port": req.port,
            "database_name": req.database_name,
            "username": req.username,
            "ssl_enabled": req.ssl_enabled,
            "is_default": req.is_default,
            "created_at": datetime.now().isoformat(),
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(
            status_code=400, detail=f"Failed to update connection: {str(e)}"
        )
    finally:
        conn.close()


@app.post("/database/connections/{connection_id}/set-default")
async def set_default_connection(
    connection_id: int, current_user: dict = Depends(get_current_user)
):
    """Set a connection as default for its database type"""
    conn = get_connection()
    c = conn.cursor()

    try:
        # Get the connection details
        c.execute(
            """
            SELECT db_type FROM database_connections 
            WHERE id = ? AND user_id = ?
        """,
            (connection_id, current_user["user_id"]),
        )

        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Connection not found")

        db_type = row[0]

        # Clear existing default for this db_type
        c.execute(
            """
            UPDATE database_connections 
            SET is_default = 0 
            WHERE user_id = ? AND db_type = ?
        """,
            (current_user["user_id"], db_type),
        )

        # Set this connection as default
        c.execute(
            """
            UPDATE database_connections 
            SET is_default = 1 
            WHERE id = ? AND user_id = ?
        """,
            (connection_id, current_user["user_id"]),
        )

        conn.commit()
        return {"status": "success", "message": f"Default {db_type} connection set"}
    finally:
        conn.close()


@app.get("/database/connections/default/{db_type}")
async def get_default_connection(
    db_type: str, current_user: dict = Depends(get_current_user)
):
    """Get the default connection for a database type"""
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        """
        SELECT id, name, db_type, host, port, database_name, username, ssl_enabled, encrypted_password
        FROM database_connections 
        WHERE user_id = ? AND db_type = ? AND is_default = 1
        LIMIT 1
    """,
        (current_user["user_id"], db_type),
    )

    row = c.fetchone()
    conn.close()

    if not row:
        return {"has_default": False}

    return {
        "id": row[0],
        "name": row[1],
        "db_type": row[2],
        "host": row[3],
        "port": row[4],
        "database_name": row[5],
        "username": row[6],
        "ssl_enabled": bool(row[7]),
        "has_default": True,
    }


# ----------------------------
# SQL Execution Engine
# ----------------------------
def execute_sql(connection_config: dict, sql: str, limit: int = 100) -> dict:
    """Execute SQL query against the specified database"""
    db_type = connection_config["db_type"]
    start_time = time.time()

    try:
        # Decrypt password

        if db_type == "postgresql":
            password = decrypt_password(connection_config["encrypted_password"])
            conn = psycopg2.connect(
                host=connection_config["host"],
                port=connection_config["port"] or 5432,
                database=connection_config["database_name"],
                user=connection_config["username"],
                password=password,
                sslmode="require" if connection_config.get("ssl_enabled") else "prefer",
            )
            cursor = conn.cursor()
            cursor.execute(sql)

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchmany(limit)
                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "execution_time": time.time() - start_time,
                }
            else:
                conn.commit()
                return {
                    "success": True,
                    "row_count": cursor.rowcount,
                    "execution_time": time.time() - start_time,
                }

        elif db_type == "mysql":
            password = decrypt_password(connection_config["encrypted_password"])
            conn = pymysql.connect(
                host=connection_config["host"],
                port=connection_config["port"] or 3306,
                database=connection_config["database_name"],
                user=connection_config["username"],
                password=password,
                ssl={"enabled": True} if connection_config.get("ssl_enabled") else None,
            )
            cursor = conn.cursor()
            cursor.execute(sql)

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchmany(limit)
                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "execution_time": time.time() - start_time,
                }
            else:
                conn.commit()
                return {
                    "success": True,
                    "row_count": cursor.rowcount,
                    "execution_time": time.time() - start_time,
                }

        elif db_type == "sqlite":
            db_path = connection_config["host"]
            normalized = os.path.normpath(db_path)
            if "app_data.db" in normalized or normalized.startswith("..") or "/.." in normalized or "\\.." in normalized:
                return {
                    "success": False,
                    "error": "Access to internal or restricted database files is prohibited.",
                    "execution_time": time.time() - start_time,
                }

            sqlite_base_dir = os.path.join(BASE_DIR, "data", "sqlite_dbs")
            os.makedirs(sqlite_base_dir, exist_ok=True)

            if not os.path.isabs(db_path):
                db_path = os.path.join(sqlite_base_dir, os.path.basename(db_path))
            elif not os.path.abspath(db_path).startswith(os.path.abspath(BASE_DIR)) or os.path.abspath(db_path) == os.path.abspath(DB_PATH):
                return {
                    "success": False,
                    "error": "SQLite databases must reside within the application data workspace.",
                    "execution_time": time.time() - start_time,
                }

            conn = sqlite_driver.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(sql)

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchmany(limit)
                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "execution_time": time.time() - start_time,
                }
            else:
                conn.commit()
                return {
                    "success": True,
                    "row_count": cursor.rowcount,
                    "execution_time": time.time() - start_time,
                }

        elif db_type == "sqlserver":
            # Using pyodbc with ODBC Driver
            username = connection_config.get("username")
            encrypted_password = connection_config.get("encrypted_password")

            # Windows Authentication if username is None or a placeholder
            is_windows_auth = (
                username is None
                or username == "WINDOWS_AUTH"
                or encrypted_password is None
                or encrypted_password == "WINDOWS_AUTH_PLACEHOLDER"
            )

            if is_windows_auth:
                # Windows Authentication (Trusted Connection) - USING DRIVER 17
                conn_str = (
                    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                    f"SERVER={connection_config['host']},{connection_config['port'] or 1433};"
                    f"DATABASE={connection_config['database_name']};"
                    f"Trusted_Connection=yes;"
                )
                print("🔐 Using Windows Authentication for SQL Server (ODBC Driver 17)")
            else:
                # SQL Server Authentication - USING DRIVER 17
                password = decrypt_password(encrypted_password)
                conn_str = (
                    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                    f"SERVER={connection_config['host']},{connection_config['port'] or 1433};"
                    f"DATABASE={connection_config['database_name']};"
                    f"UID={username};"
                    f"PWD={password}"
                )
                print("🔑 Using SQL Server Authentication (ODBC Driver 17)")

            # Add SSL settings
            if connection_config.get("ssl_enabled"):
                conn_str += ";Encrypt=yes;TrustServerCertificate=yes"
            else:
                conn_str += ";Encrypt=optional;TrustServerCertificate=yes"

            print(f"📡 Connection string: {conn_str}")

            try:
                conn = pyodbc.connect(conn_str)
                cursor = conn.cursor()
                cursor.execute(sql)

                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchmany(limit)
                    return {
                        "success": True,
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                        "execution_time": time.time() - start_time,
                    }
                else:
                    conn.commit()
                    return {
                        "success": True,
                        "row_count": cursor.rowcount,
                        "execution_time": time.time() - start_time,
                    }
            except Exception as e:
                print(f"❌ SQL Server connection error: {e}")
                raise

        elif db_type == "snowflake":
            password = decrypt_password(connection_config["encrypted_password"])
            conn = snowflake.connector.connect(
                account=connection_config["host"],  # Snowflake account
                warehouse=connection_config.get("warehouse", ""),
                database=connection_config["database_name"],
                schema=connection_config.get("schema", "PUBLIC"),
                user=connection_config["username"],
                password=password,
                ocsp_fail_open=False,
            )
            cursor = conn.cursor()
            cursor.execute(sql)

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchmany(limit)
                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "execution_time": time.time() - start_time,
                }
            else:
                conn.commit()
                return {
                    "success": True,
                    "row_count": cursor.rowcount,
                    "execution_time": time.time() - start_time,
                }

        elif db_type == "bigquery":
            password = decrypt_password(connection_config["encrypted_password"])
            from google.oauth2 import service_account

            # For BigQuery, we use service account JSON
            # Here we're assuming the password field contains the service account key JSON
            import json

            service_account_info = json.loads(password)
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info
            )

            client = bigquery.Client(
                project=connection_config["database_name"], credentials=credentials
            )

            query_job = client.query(sql)
            results = query_job.result(max_results=limit)

            columns = [field.name for field in results.schema]
            rows = [list(row.values()) for row in results]

            return {
                "success": True,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "execution_time": time.time() - start_time,
            }

        else:
            # Generic SQLAlchemy fallback for other databases
            if db_type == "oracle":
                driver = "oracle+cx_oracle"
            elif db_type == "redshift":
                driver = "redshift+psycopg2"
            else:
                driver = db_type

            password = decrypt_password(connection_config.get("encrypted_password", ""))
            connection_string = f"{driver}://{connection_config['username']}:{password}@{connection_config['host']}"
            if connection_config.get("port"):
                connection_string += f":{connection_config['port']}"
            if connection_config.get("database_name"):
                connection_string += f"/{connection_config['database_name']}"

            engine = create_engine(connection_string)
            with engine.connect() as conn:
                result = conn.execute(sql)
                if result.returns_rows:
                    columns = result.keys()
                    rows = result.fetchmany(limit)
                    return {
                        "success": True,
                        "columns": list(columns),
                        "rows": [list(row) for row in rows],
                        "row_count": len(rows),
                        "execution_time": time.time() - start_time,
                    }
                else:
                    return {
                        "success": True,
                        "row_count": result.rowcount,
                        "execution_time": time.time() - start_time,
                    }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "execution_time": time.time() - start_time,
        }
    finally:
        if "conn" in locals():
            conn.close()


@app.post("/database/execute", response_model=SQLExecutionResponse)
async def execute_sql_query(
    req: SQLExecutionRequest, current_user: dict = Depends(get_current_user)
):
    """Execute SQL query against a saved database connection"""
    conn = get_connection()
    c = conn.cursor()

    # Get connection details
    c.execute(
        """
        SELECT id, db_type, host, port, database_name, username, encrypted_password, ssl_enabled
        FROM database_connections 
        WHERE id = ? AND user_id = ?
    """,
        (req.connection_id, current_user["user_id"]),
    )

    row = c.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Connection not found")

    connection_config = {
        "id": row[0],
        "db_type": row[1],
        "host": row[2],
        "port": row[3],
        "database_name": row[4],
        "username": row[5],
        "encrypted_password": row[6],
        "ssl_enabled": bool(row[7]),
    }
    # Execute the query
    safe_config = {k: v for k, v in connection_config.items() if k != "encrypted_password"}
    print(f"Executing SQL query with connection: {safe_config.get('name')} ({safe_config.get('db_type')})")
    result = execute_sql(connection_config, req.sql, req.limit_rows)

    return result


# ----------------------------
# Mount static files (index.html, style.css, script.js)
# ----------------------------
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
os.makedirs(FRONTEND_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


# ----------------------------
# Run with uvicorn
# ----------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app_backend:app", host="127.0.0.1", port=8000, reload=True)
