# -*- coding: utf-8 -*-
"""GDWPMA-AI 政府债全过程管理智能体 — Flask 入口（7页面线性推进）。端口 5210。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import db as database
from services import rule_engine, report as report_svc, llm
from collections import Counter

app = Flask(__name__, template_folder=os.path.join("web", "templates"))
database.init_db()
from services import auth as _auth
from services.auth import init_auth, login_required
auth = _auth  # login/logout 路由引用
init_auth(app)
UPLOAD_DIR = os.path.join(database.BASE, "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def _extract_text(path, fname):
    """PDF 用 pymupdf 抽文本；txt/md 直读；其他类型存文件不抽取。"""
    try:
        if fname.lower().endswith(".pdf"):
            import fitz
            with fitz.open(path) as d:
                return "".join(pg.get_text() for pg in d)[:20000]
        if fname.lower().endswith((".txt", ".md")):
            return open(path, encoding="utf-8", errors="replace").read()[:20000]
    except Exception as e:
        return f"[抽取失败：{e}]"
    return None

@app.context_processor
def _nav():
    from flask import session
    return {"nav": [("总览", "/"), ("法规库", "/regulations"), ("项目库", "/projects"),
                    ("审核工作台", "/review"), ("问题台账", "/ledger"), ("培训辅导", "/training"),
                    ("底稿归档", "/archive"), ("用户", "/users")], "uname": session.get("uname")}

# ------------------------------------------------ 登录
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return auth.login_page()
    return auth.do_login()

@app.route("/logout")
def logout():
    return auth.do_logout()

# ------------------------------------------------ 1 总览
@app.route("/")
@login_required
def home():
    c = database.conn()
    stats = {
        "reg_cnt": c.execute("SELECT COUNT(*) FROM regulations").fetchone()[0],
        "proj_cnt": c.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
        "find_cnt": c.execute("SELECT COUNT(*) FROM findings").fetchone()[0],
        "open_find": c.execute("SELECT COUNT(*) FROM findings WHERE status NOT IN ('已闭环')").fetchone()[0],
        "p0": c.execute("SELECT COUNT(*) FROM findings WHERE severity='P0' AND status NOT IN ('已闭环')").fetchone()[0],
        "review_cnt": c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0],
    }
    by_county = [dict(r) for r in c.execute(
        "SELECT county, COUNT(*) n, SUM(bond_amount) amt FROM projects GROUP BY county ORDER BY n DESC").fetchall()]
    sev = dict(c.execute("SELECT severity, COUNT(*) FROM findings GROUP BY severity").fetchall())
    recent = [dict(r) for r in c.execute(
        "SELECT w.*, p.name proj FROM workpapers w LEFT JOIN projects p ON p.id=w.project_id ORDER BY w.id DESC LIMIT 10").fetchall()]
    c.close()
    return render_template("home.html", stats=stats, by_county=by_county, sev=sev, recent=recent)

# ------------------------------------------------ 2 法规库
@app.route("/regulations")
@login_required
def regulations():
    q, level = request.args.get("q", "").strip(), request.args.get("level", "")
    rows = database.search_regulations(q, level=level or None, limit=50)
    levels = [r[0] for r in database.conn().execute("SELECT DISTINCT level FROM regulations").fetchall()]
    return render_template("regulations.html", rows=rows, q=q, level=level, levels=levels)

# ------------------------------------------------ 3 项目库
@app.route("/projects")
@login_required
def projects():
    c = database.conn()
    rows = [dict(r) for r in c.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()]
    c.close()
    return render_template("projects.html", rows=rows)

@app.route("/projects/<int:pid>")
@login_required
def project_detail(pid):
    c = database.conn()
    p = database.get_project(pid)
    if p is None:
        from flask import abort
        abort(404)
    docs = [dict(r) for r in c.execute("SELECT * FROM project_docs WHERE project_id=? ORDER BY id", (pid,)).fetchall()]
    revs = [dict(r) for r in c.execute("SELECT * FROM reviews WHERE project_id=? ORDER BY id DESC", (pid,)).fetchall()]
    fds = [dict(r) for r in c.execute("SELECT * FROM findings WHERE project_id=? ORDER BY id DESC", (pid,)).fetchall()]
    rp = [dict(r) for r in c.execute("SELECT * FROM repayments WHERE project_id=? ORDER BY due_date", (pid,)).fetchall()]
    c.close()
    have = {d["doc_type"] for d in docs}
    checklist = [(t, t in have) for t in rule_engine.REQUIRED_DOCS]
    cov = rule_engine._coverage(p)
    return render_template("project_detail.html", p=p, docs=docs, revs=revs, fds=fds,
                           checklist=checklist, cov=cov, rp=rp, doc_types=rule_engine.DOC_TYPES)

# ------------------------------------------------ 4 审核工作台
@app.route("/review")
@login_required
def review_page():
    c = database.conn()
    projs = [dict(r) for r in c.execute("SELECT id,county,name,status FROM projects ORDER BY id DESC").fetchall()]
    c.close()
    return render_template("review.html", projs=projs, stages=list(rule_engine.STAGE_FILES))

@app.route("/review/<int:rid>")
@login_required
def review_result(rid):
    c = database.conn()
    rv = dict(c.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone())
    p = dict(c.execute("SELECT * FROM projects WHERE id=?", (rv["project_id"],)).fetchone())
    fds = [dict(r) for r in c.execute("SELECT * FROM findings WHERE review_id=? ORDER BY id", (rid,)).fetchall()]
    c.close()
    return render_template("review_result.html", rv=rv, p=p, fds=fds)

# ------------------------------------------------ 5 问题台账
@app.route("/ledger")
@login_required
def ledger():
    status, county = request.args.get("status", ""), request.args.get("county", "")
    sql = ("SELECT f.*, p.name proj, p.county FROM findings f JOIN projects p ON p.id=f.project_id WHERE 1=1")
    args = []
    if status:
        sql += " AND f.status=?"; args.append(status)
    if county:
        sql += " AND p.county=?"; args.append(county)
    sql += " ORDER BY f.id DESC LIMIT 200"
    c = database.conn()
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    counties = [r[0] for r in c.execute("SELECT DISTINCT county FROM projects").fetchall()]
    by_county = [dict(r) for r in c.execute(
        "SELECT p.county, COUNT(*) n, SUM(f.severity='P0') p0 FROM findings f JOIN projects p ON p.id=f.project_id "
        "GROUP BY p.county ORDER BY n DESC").fetchall()]
    c.close()
    return render_template("ledger.html", rows=rows, counties=counties, by_county=by_county,
                           status=status, county=county,
                           statuses=["待整改", "整改中", "待复核", "已闭环", "已退回"])

# ------------------------------------------------ 6 培训辅导
@app.route("/training")
@login_required
def training():
    c = database.conn()
    top = [dict(r) for r in c.execute(
        "SELECT rule_no, rule_name, severity, clause, COUNT(*) n FROM findings GROUP BY rule_no, rule_name, severity, clause "
        "ORDER BY n DESC LIMIT 15").fetchall()]
    c.close()
    return render_template("training.html", top=top, answer=None, q="")

@app.route("/training", methods=["POST"])
@login_required
def training_ask():
    q = request.form.get("q", "").strip()
    c = database.conn()
    top = [dict(r) for r in c.execute(
        "SELECT rule_no, rule_name, severity, clause, COUNT(*) n FROM findings GROUP BY rule_no, rule_name, severity, clause "
        "ORDER BY n DESC LIMIT 15").fetchall()]
    c.close()
    answer = llm.answer_with_regulations(q) if q else None
    return render_template("training.html", top=top, answer=answer, q=q)

# ------------------------------------------------ 7 底稿归档
@app.route("/archive")
@login_required
def archive_page():
    c = database.conn()
    rows = [dict(r) for r in c.execute(
        "SELECT p.id, p.county, p.name, COUNT(w.id) wp_cnt, "
        "(SELECT COUNT(*) FROM findings f WHERE f.project_id=p.id) fd_cnt "
        "FROM projects p LEFT JOIN workpapers w ON w.project_id=p.id GROUP BY p.id ORDER BY p.id DESC").fetchall()]
    arcs = []
    outdir = os.path.join(database.BASE, "data", "outputs")
    if os.path.isdir(outdir):
        arcs = sorted([d for d in os.listdir(outdir) if d.startswith("archive_")], reverse=True)
    c.close()
    return render_template("archive.html", rows=rows, arcs=arcs)

# ------------------------------------------------ API
@app.route("/api/projects", methods=["POST"])
@login_required
def api_project_add():
    f = request.form
    c = database.conn()
    cur = c.execute(
        "INSERT INTO projects(county,name,category,project_type,total_invest,bond_amount,term_years,rate,"
        "annual_income,owner_unit,status,limit_remaining,approved_scale,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f.get("county"), f.get("name"), f.get("category"), f.get("project_type") or "新增专项债券",
         f.get("total_invest") or None, f.get("bond_amount") or None, f.get("term_years") or None,
         f.get("rate") or None, f.get("annual_income") or None, f.get("owner_unit"),
         f.get("status") or "储备", f.get("limit_remaining") or None, f.get("approved_scale") or None,
         database.now()))
    pid = cur.lastrowid
    c.commit(); c.close()
    database.wp(pid, "项目库", "建档", f"项目建档：{f.get('name')}（{f.get('county')}）")
    return redirect(url_for("project_detail", pid=pid))

@app.route("/api/docs", methods=["POST"])
@login_required
def api_doc_add():
    f = request.form
    pid = int(f.get("project_id"))
    file = request.files.get("file")
    fpath, extracted = "", None
    if file and file.filename:
        from werkzeug.utils import secure_filename
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md", ".jpg", ".jpeg", ".png"}:
            return redirect(request.referrer or "/projects")  # ponytail: 白名单外静默拒收，与表单 accept 一致
        fname = f"{pid}_{database.now().replace(':', '').replace(' ', '-')}_{secure_filename(file.filename)}"
        fpath = os.path.join(UPLOAD_DIR, fname)
        file.save(fpath)
        extracted = _extract_text(fpath, file.filename)
    c = database.conn()
    c.execute("INSERT INTO project_docs(project_id,doc_type,file_path,note,uploaded_at,extracted_text) VALUES(?,?,?,?,?,?)",
              (pid, f.get("doc_type"), fpath, f.get("note"), database.now(), extracted))
    c.commit(); c.close()
    database.wp(pid, "材料", "登记", f"登记材料《{f.get('doc_type')}》{f.get('note') or ''}" + (f"（上传 {file.filename}）" if file and file.filename else ""))
    return redirect(url_for("project_detail", pid=pid))

@app.route("/api/review/run", methods=["POST"])
@login_required
def api_review_run():
    pid = int(request.form.get("project_id"))
    stage = request.form.get("stage")
    reviewer = request.form.get("reviewer") or "系统"
    rid, _ = rule_engine.run_review(pid, stage, reviewer)
    return redirect(url_for("review_result", rid=rid))

@app.route("/api/review/report", methods=["POST"])
@login_required
def api_review_report():
    rid = int(request.form.get("review_id"))
    path, conclusion = report_svc.generate_report(rid)
    try:
        from services.docx_report import md_to_docx
        docx = md_to_docx(path)
    except Exception as e:
        docx = f"docx转换失败: {e}"  # ponytail: md 是权威载体，docx 失败不阻断报告
    return jsonify({"report_path": path, "conclusion": conclusion, "docx_path": docx})

@app.route("/api/rectify", methods=["POST"])
@login_required
def api_rectify():
    f = request.form
    fid = int(f.get("finding_id"))
    c = database.conn()
    cur_f = dict(c.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone())
    action = f.get("action")
    if action == "plan":
        c.execute("UPDATE findings SET status='整改中', plan=?, due_date=? WHERE id=?",
                  (f.get("plan"), f.get("due_date"), fid))
    elif action == "result":
        c.execute("UPDATE findings SET status='待复核', result=? WHERE id=?", (f.get("result"), fid))
    elif action == "recheck":
        ok = f.get("ok") == "1"
        c.execute("UPDATE findings SET status=?, recheck_at=?, recheck_note=? WHERE id=?",
                  ("已闭环" if ok else "已退回", database.now(), f.get("recheck_note"), fid))
    c.commit(); c.close()
    database.wp(cur_f["project_id"], cur_f["stage"], {"plan": "整改计划", "result": "整改结果提交", "recheck": "二次复核"}[action],
                f"问题{cur_f['rule_no']}：{f.get('plan') or f.get('result') or f.get('recheck_note') or ''}")
    return redirect(request.referrer or url_for("ledger"))

@app.route("/api/repayments", methods=["POST"])
@login_required
def api_repayment_add():
    f = request.form
    pid = int(f.get("project_id"))
    c = database.conn()
    c.execute("INSERT INTO repayments(project_id,bond_batch,due_date,principal,interest,paid,income_recv) VALUES(?,?,?,?,?,?,?)",
              (pid, f.get("bond_batch"), f.get("due_date"), f.get("principal") or 0,
               f.get("interest") or 0, f.get("paid") or 0, f.get("income_recv") or 0))
    c.commit(); c.close()
    database.wp(pid, "还", "还本付息登记", f"批次{f.get('bond_batch')} 到期{f.get('due_date')}")
    return redirect(url_for("project_detail", pid=pid))

@app.route("/api/archive/<int:pid>", methods=["POST"])
@login_required
def api_archive(pid):
    arc = report_svc.archive_project(pid)
    return jsonify({"archive_dir": arc})


@app.route("/users")
@login_required
def users_page():
    c = database.conn()
    rows = [dict(r) for r in c.execute("SELECT id,username,role,created_at FROM users ORDER BY id").fetchall()]
    c.close()
    return render_template("users.html", rows=rows, msg=request.args.get("msg", ""))

@app.route("/api/users/add", methods=["POST"])
@login_required
def api_user_add():
    if session.get("role") != "管理员":
        return redirect("/users?msg=仅管理员可操作")
    u = database.add_user(request.form.get("username", "").strip(), request.form.get("password", ""), request.form.get("role") or "审核员")
    return redirect(f"/users?msg={'已添加' if u else '用户名已存在'}")

@app.route("/api/users/reset_pw", methods=["POST"])
@login_required
def api_user_reset_pw():
    if session.get("role") != "管理员":
        return redirect("/users?msg=仅管理员可操作")
    import hashlib, secrets
    c = database.conn()
    salt = secrets.token_hex(8)
    pw = hashlib.pbkdf2_hmac("sha256", request.form.get("password", "").encode(), salt.encode(), 60000).hex()
    c.execute("UPDATE users SET pw_hash=?, salt=? WHERE username=?", (pw, salt, request.form.get("username")))
    c.commit(); c.close()
    return redirect("/users?msg=已重置密码")

@app.route("/api/users/del", methods=["POST"])
@login_required
def api_user_del():
    if session.get("role") != "管理员":
        return redirect("/users?msg=仅管理员可操作")
    if request.form.get("username") == session.get("uname"):
        return redirect("/users?msg=不能删除自己")
    c = database.conn()
    c.execute("DELETE FROM users WHERE username=? AND username!='admin'", (request.form.get("username"),))
    c.commit(); c.close()
    return redirect("/users?msg=已删除")

@app.route("/api/me/password", methods=["POST"])
@login_required
def api_me_password():
    if not database.check_user(session.get("uname"), request.form.get("old", "")):
        return redirect("/users?msg=旧密码错误")
    import hashlib, secrets
    c = database.conn()
    salt = secrets.token_hex(8)
    pw = hashlib.pbkdf2_hmac("sha256", request.form.get("new", "").encode(), salt.encode(), 60000).hex()
    c.execute("UPDATE users SET pw_hash=?, salt=? WHERE username=?", (pw, salt, session.get("uname")))
    c.commit(); c.close()
    return redirect("/users?msg=密码已修改")

@app.route("/api/review/pdf", methods=["POST"])
@login_required
def api_review_pdf():
    """docx -> PDF，Word COM（convert_pdf 同法）；返回 PDF 路径，无 Word/失败给明确错误。"""
    docx_path = request.form.get("docx_path", "")
    allowed_root = os.path.join(database.BASE, "data", "outputs")  # ponytail: 白名单目录，防任意本地 docx 转换
    if (not docx_path.endswith(".docx") or not os.path.exists(docx_path)
            or os.path.abspath(docx_path).startswith(os.path.abspath(allowed_root)) is False):
        return jsonify({"error": "docx 不存在或不在允许目录"}), 404
    pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
    try:
        import pythoncom
        from win32com import client as w32c
        pythoncom.CoInitialize()
        word = w32c.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(os.path.abspath(docx_path))
        doc.SaveAs(os.path.abspath(pdf_path), FileFormat=17)  # 17 = wdFormatPDF
        doc.Close(False); word.Quit()
        return jsonify({"pdf_path": pdf_path})
    except Exception as e:
        return jsonify({"error": f"Word COM 转换失败: {e}"}), 500

@app.route("/api/ask", methods=["POST"])
@login_required
def api_ask():
    q = request.form.get("q", "").strip()
    if not q:
        return jsonify({"error": "问题为空"})
    return jsonify(llm.answer_with_regulations(q))


@app.route("/api/docs/<int:doc_id>/ocr", methods=["POST"])
@login_required
def api_doc_ocr(doc_id):
    """扫描件 OCR：PDF 页无文本层→光栅化；图片直接识别。RapidOCR（本地离线）。"""
    c = database.conn()
    d = dict(c.execute("SELECT * FROM project_docs WHERE id=?", (doc_id,)).fetchone())
    c.close()
    if not d["file_path"] or not os.path.exists(d["file_path"]):
        return jsonify({"error": "该材料无上传文件"}), 404
    try:
        from rapidocr_onnxruntime import RapidOCR
        import fitz
        ocr = RapidOCR()
        texts = []
        if d["file_path"].lower().endswith(".pdf"):
            with fitz.open(d["file_path"]) as doc:
                for pg in doc:
                    t = pg.get_text().strip()
                    if t:  # 有文本层的页不 OCR
                        texts.append(t); continue
                    pix = pg.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    res, _ = ocr(img_bytes)
                    texts.append("\n".join(r[1] for r in (res or [])))
        else:
            res, _ = ocr(d["file_path"])
            texts.append("\n".join(r[1] for r in (res or [])))
        full = "\n".join(x for x in texts if x)[:20000]
        c = database.conn()
        c.execute("UPDATE project_docs SET extracted_text=? WHERE id=?", (full, doc_id))
        c.commit(); c.close()
        database.wp(d["project_id"], "材料", "OCR", f"材料#{doc_id} OCR识别 {len(full)} 字")
        return jsonify({"chars": len(full), "text_head": full[:200]})
    except ImportError as e:
        return jsonify({"error": f"OCR 引擎未安装: {e}"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/review/ai_comment", methods=["POST"])
@login_required
def api_review_ai_comment():
    """AI 点评：LLM 只做辅助解读（优先整改项/风险提示），判定与结论仍以规则引擎与人工为准。"""
    rid = int(request.form.get("review_id"))
    c = database.conn()
    rv = dict(c.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone())
    p = dict(c.execute("SELECT county,name,bond_amount,annual_income FROM projects WHERE id=?", (rv["project_id"],)).fetchone())
    fs = [dict(r) for r in c.execute("SELECT rule_no,rule_name,severity,clause,description FROM findings WHERE review_id=?", (rid,)).fetchall()]
    c.close()
    if not fs:
        return jsonify({"comment": "本批次无问题，无需点评。", "mode": "noop"})
    findings_text = "\n".join(f"- [{f['severity']}] {f['rule_no']} {f['rule_name']}：{f['description'][:60]}（依据 {f['clause'][:40]}）" for f in fs)
    from services import llm
    prompt = (f"项目「{p['name']}」（{p['county']}，申请债券{p.get('bond_amount') or '—'}万元，年均收入{p.get('annual_income') or '—'}万元）"
              f"在{rv['stage']}环节审核发现以下问题：\n{findings_text}\n\n"
              "请作为债务管理辅导专家输出：1) 最需优先整改的两项及理由；2) 一句话风险提示。控制在150字内，不编造规则之外的依据。")
    try:
        return jsonify({"comment": llm.chat(prompt), "mode": "glm"})
    except Exception as e:
        return jsonify({"comment": f"GLM 暂不可用：{e}", "mode": "fail"})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5210, debug=False)
