# -*- coding: utf-8 -*-
"""GDWPMA-AI 最小测试集：建库/检索/规则引擎/报告/归档。运行：python -m pytest tests/ -v"""
import os, sys, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as database

def setup_module(m):
    # 独立临时测试库，不动运行中服务的 data/gdwpma.db
    database.DB_PATH = os.path.join(tempfile.gettempdir(), "gdwpma_test.db")
    for suf in ("", "-wal", "-shm"):
        p = database.DB_PATH + suf
        if os.path.exists(p):
            os.remove(p)
    database.init_db()

def test_regulations_seeded():
    c = database.conn()
    n = c.execute("SELECT COUNT(*) FROM regulations").fetchone()[0]
    levels = dict(c.execute("SELECT level, COUNT(*) FROM regulations GROUP BY level").fetchall())
    c.close()
    assert n >= 20, f"法规数不足: {n}"
    assert levels.get("国家", 0) >= 12 and levels.get("省", 0) >= 2 and levels.get("市县", 0) >= 2

def test_fts_search():
    rows = database.search_regulations("负面清单")
    assert any("52号" in r["doc_no"] for r in rows), "FTS 未命中 52号文"
    rows2 = database.search_regulations("专项债券", level="省")
    assert all(r["level"] == "省" for r in rows2) and rows2

def test_rule_engine_p0_hits():
    from services import rule_engine
    c = database.conn()
    cur = c.execute("INSERT INTO projects(county,name,category,bond_amount,term_years,rate,annual_income,owner_unit,limit_remaining,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("测试县", "违规测试项目", "商业房地产开发", 10000, 10, 3.0, 500, "测试单位", 20000, database.now()))
    pid = cur.lastrowid
    c.commit(); c.close()
    rid, findings = rule_engine.run_review(pid, "借", "tester")
    nos = {f["rule_no"] for f in findings}
    assert "AP-001" in nos, f"负面清单未命中: {nos}"          # 商业房地产
    assert "AP-003" in nos, f"覆盖倍数未命中: {nos}"          # 500*10/(10000*1.3)=0.38 <1.2
    assert "AP-013" not in nos, "额度未超限却命中 AP-013"      # 10000 < 20000 → 不命中
    assert "AP-006" in nos or "AP-005" in nos, "缺材料规则未命中"

def test_coverage_boundary():
    from services import rule_engine
    c = database.conn()
    cur = c.execute("INSERT INTO projects(county,name,category,bond_amount,term_years,rate,annual_income,created_at) "
                    "VALUES('测试县','临界项目','市政',10000,10,3.0,1300,?)", (database.now(),))
    pid = cur.lastrowid
    c.commit(); c.close()
    p = dict(database.conn().execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
    cov = rule_engine._coverage(p)
    assert abs(cov - 1.0) < 0.02, f"覆盖倍数计算错误: {cov}"   # 1300*10/13000=1.0

def test_report_and_archive():
    from services import rule_engine, report as report_svc
    c = database.conn()
    cur = c.execute("INSERT INTO projects(county,name,category,bond_amount,term_years,rate,annual_income,created_at) "
                    "VALUES('测试县','报告项目','市政',10000,10,3.0,500,?)", (database.now(),))
    pid = cur.lastrowid
    c.commit(); c.close()
    rid, _ = rule_engine.run_review(pid, "借", "tester")
    path, conclusion = report_svc.generate_report(rid)
    assert os.path.exists(path) and "不予通过" in conclusion
    arc = report_svc.archive_project(pid)
    assert os.path.exists(os.path.join(arc, "卷内目录.md"))

def test_rectify_loop():
    """整改闭环状态机：待整改→整改中→待复核→已闭环"""
    c = database.conn()
    cur = c.execute("INSERT INTO projects(county,name,created_at) VALUES('测试县','闭环项目',?)", (database.now(),))
    pid = cur.lastrowid
    cur2 = c.execute("INSERT INTO findings(review_id,project_id,rule_no,rule_name,stage,severity,clause,description) "
                     "VALUES(1,?,'AP-001','测试','借','P0','测试条款','测试')", (pid,))
    fid = cur2.lastrowid
    c.commit(); c.close()
    from flask import Flask
    os.environ.setdefault("FLASK_TEST", "1")
    import app as appmod
    client = appmod.app.test_client()
    client.post("/login", data={"username": "admin", "password": "gdwpma@2026"})  # API 已加登录保护
    r = client.post("/api/rectify", data={"finding_id": fid, "action": "plan", "plan": "整改方案", "due_date": "2026-11-30"})
    assert r.status_code in (302, 303)
    c = database.conn()
    st = c.execute("SELECT status FROM findings WHERE id=?", (fid,)).fetchone()[0]
    c.close()
    assert st == "整改中"
    client.post("/api/rectify", data={"finding_id": fid, "action": "result", "result": "已整改完毕"})
    client.post("/api/rectify", data={"finding_id": fid, "action": "recheck", "ok": "1", "recheck_note": "复核通过"})
    c = database.conn()
    st = c.execute("SELECT status FROM findings WHERE id=?", (fid,)).fetchone()[0]
    c.close()
    assert st == "已闭环"
