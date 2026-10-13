# -*- coding: utf-8 -*-
"""GDWPMA-AI 数据层：SQLite WAL + FTS5，8张核心表+FTS虚拟表 + 法规种子。"""
import os, sqlite3, json, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GDWPMA_DB_PATH") or os.path.join(BASE, "data", "gdwpma.db")  # ponytail: 测试用env指向临时库，避免与运行中服务锁冲突
REG_DIR = os.path.join(BASE, "data", "regulations")

DDL = """
CREATE TABLE IF NOT EXISTS regulations(
  id INTEGER PRIMARY KEY, level TEXT, region TEXT, title TEXT, doc_no TEXT,
  issue_date TEXT, status TEXT DEFAULT '现行有效', keywords TEXT,
  content TEXT, updated_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS regulations_fts USING fts5(
  title, doc_no, content, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, county TEXT, name TEXT, category TEXT, project_type TEXT,
  total_invest REAL, bond_amount REAL, term_years REAL, rate REAL, annual_income REAL,
  owner_unit TEXT, status TEXT DEFAULT '储备', limit_remaining REAL,
  approved_scale REAL, created_at TEXT, extra TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS project_docs(
  id INTEGER PRIMARY KEY, project_id INTEGER, doc_type TEXT, file_path TEXT,
  note TEXT, uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY, project_id INTEGER, stage TEXT, reviewer TEXT,
  reviewed_at TEXT, report_path TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY, review_id INTEGER, project_id INTEGER,
  rule_no TEXT, rule_name TEXT, stage TEXT, severity TEXT, clause TEXT,
  description TEXT, evidence TEXT,
  status TEXT DEFAULT '待整改',  -- 待整改/整改中/待复核/已闭环/已退回
  plan TEXT, due_date TEXT, result TEXT, recheck_at TEXT, recheck_note TEXT
);
CREATE TABLE IF NOT EXISTS repayments(
  id INTEGER PRIMARY KEY, project_id INTEGER, bond_batch TEXT, due_date TEXT,
  principal REAL, interest REAL, paid REAL DEFAULT 0, income_recv REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workpapers(
  id INTEGER PRIMARY KEY, project_id INTEGER, stage TEXT, action TEXT,
  content TEXT, file_path TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw_hash TEXT, salt TEXT,
  role TEXT DEFAULT '审核员', created_at TEXT
);
"""

def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(REG_DIR, exist_ok=True)
    c = conn()
    c.executescript(DDL)
    try:
        c.execute("ALTER TABLE project_docs ADD COLUMN extracted_text TEXT")  # ponytail: 旧库迁移，新库 DDL 未含则补列
    except sqlite3.OperationalError:
        pass
    if c.execute("SELECT COUNT(*) FROM regulations").fetchone()[0] == 0:
        seed_regulations(c)
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        import hashlib, secrets as _s
        salt = _s.token_hex(8)
        pw = hashlib.pbkdf2_hmac("sha256", "gdwpma@2026".encode(), salt.encode(), 60000).hex()
        c.execute("INSERT INTO users(username,pw_hash,salt,role,created_at) VALUES(?,?,?,?,?)",
                  ("admin", pw, salt, "管理员", now()))  # 同连接写入，避免自锁
    c.commit(); c.close()

def add_user(username, password, role="审核员"):
    import hashlib, secrets as _s
    salt = _s.token_hex(8)
    pw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 60000).hex()
    c = conn()
    c.execute("INSERT OR IGNORE INTO users(username,pw_hash,salt,role,created_at) VALUES(?,?,?,?,?)",
              (username, pw, salt, role, now()))
    c.commit(); uid = c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()[0]
    c.close()
    return uid

def check_user(username, password):
    import hashlib
    c = conn()
    r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    c.close()
    if not r:
        return None
    pw = hashlib.pbkdf2_hmac("sha256", password.encode(), r["salt"].encode(), 60000).hex()
    return dict(r) if pw == r["pw_hash"] else None

def wp(project_id, stage, action, content, file_path=None):
    """全过程底稿留痕：每个管理动作都记入 workpapers。"""
    c = conn()
    c.execute("INSERT INTO workpapers(project_id,stage,action,content,file_path,created_at) VALUES(?,?,?,?,?,?)",
              (project_id, stage, action, content, file_path, now()))
    c.commit(); c.close()

# ---------------------------------------------------------------- 法规种子
# frontmatter: level(国家/省/市) region title doc_no issue_date status keywords
def _spacen(s):
    """中文按字符切分为 FTS token（unicode61 对连续中文整段成词，检索必空——REGKB 已知坑）。"""
    return " ".join(list(s))
def seed_regulations(c):
    import glob, re
    n = 0
    for path in sorted(glob.glob(os.path.join(REG_DIR, "*.md"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
        meta = {}
        if m:
            import yaml
            meta = yaml.safe_load(m.group(1)) or {}
            text = text[m.end():]
        text = text.strip()
        c.execute(
            "INSERT INTO regulations(level,region,title,doc_no,issue_date,status,keywords,content,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (meta.get("level", "国家"), meta.get("region", "全国"), meta.get("title", os.path.basename(path)[:-3]),
             meta.get("doc_no", ""), meta.get("issue_date", ""), meta.get("status", "现行有效"),
             meta.get("keywords", ""), text, now()))
        c.execute("INSERT INTO regulations_fts(title,doc_no,content) VALUES(?,?,?)",
                  (_spacen(meta.get("title", "")), _spacen(meta.get("doc_no", "")), _spacen(text)))
        n += 1
    return n

def search_regulations(q, level=None, limit=20):
    c = conn()
    if q:
        toks = [ch for ch in q.replace(" ", "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"]
        if not toks:
            toks = list(q.replace(" ", ""))
        sq = " OR ".join(toks)  # OR 语义：长问句按命中数排序，避免单字 AND 全空
        sql = ("SELECT r.id,r.level,r.region,r.title,r.doc_no,r.status,"
               "snippet(regulations_fts,2,'【','】','…',12) AS snip "
               "FROM regulations_fts f JOIN regulations r ON r.id=f.rowid "
               "WHERE regulations_fts MATCH ?")
        args = [sq]
        if level:
            sql += " AND r.level=?"; args.append(level)
        sql += " ORDER BY rank LIMIT ?"; args.append(limit)
        try:
            rows = c.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            rows = []
    else:
        sql = "SELECT id,level,region,title,doc_no,status,'' AS snip FROM regulations"
        args = []
        if level:
            sql += " WHERE level=?"; args.append(level)
        sql += " ORDER BY level, id"
        rows = c.execute(sql, args).fetchall()
    c.close()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    init_db()
    c = conn()
    print("regulations:", c.execute("SELECT COUNT(*) FROM regulations").fetchone()[0])
    print("projects:", c.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    c.close()
