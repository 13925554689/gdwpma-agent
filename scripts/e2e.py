# -*- coding: utf-8 -*-
"""GDWPMA-AI 全流程 E2E：对运行中的服务(5210)以 UTF-8 走完整业务闭环。
用法：py scripts/e2e.py   （服务须已启动：py app.py）"""
import json, urllib.request, urllib.parse, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:5210"
OK = []
import http.cookiejar
_cookie = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie),
    urllib.request.HTTPRedirectHandler())

def req(path, data=None, expect=(200, 302, 303), raw_files=None):
    if raw_files:
        # multipart
        boundary = "----GDE2E1234"
        body = b""
        for k, v in (data or {}).items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
        for k, (fname, content, ctype) in raw_files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                     f"Content-Type: {ctype}\r\n\r\n").encode("utf-8") + content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        r = urllib.request.Request(BASE + path, data=body, method="POST",
                                   headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    elif data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        r = urllib.request.Request(BASE + path, data=body, method="POST")
    else:
        r = urllib.request.Request(BASE + path)
    try:
        resp = _opener.open(r, timeout=90)
        code, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode("utf-8", "replace")
    assert code in expect, f"{path} -> {code}\n{text[:600]}"
    return code, text

def step(name, cond=True):
    assert cond, f"FAIL: {name}"
    OK.append(name)
    print(f"  ok: {name}")

print("== GDWPMA-AI E2E ==")
# 0 未登录应被重定向到登录页（opener 默认跟随 302，落在登录页即通过）
code, text = req("/", expect=(200, 302, 303))
assert "登录" in text, "未登录未被拦截"
step("未登录访问 / → 落在登录页")
# 登录
code, text = req("/login", {"username": "admin", "password": "gdwpma@2026"})
step("登录 admin")
for page in ["/", "/regulations", "/projects", "/review", "/ledger", "/training", "/archive"]:
    code, _ = req(page)
    step(f"GET {page} {code}")

# 1 建档（覆盖倍数 0.58 → 应命中 AP-003 P0）
req("/api/projects", {"county": "演示县", "name": "演示县产业园区基础设施项目", "category": "产业园区基础设施",
                      "total_invest": "52000", "bond_amount": "20000", "term_years": "10", "rate": "3.0",
                      "annual_income": "1500", "owner_unit": "演示县城投", "limit_remaining": "30000"})
import db as database
c = database.conn()
pid = c.execute("SELECT MAX(id) FROM projects").fetchone()[0]
step("项目建档", pid >= 1)

# 2 材料登记（缺融资平衡方案 → AP-006）
for dt, note in [("实施方案", "2026年3月版"), ("可研批复", "豫发改投资〔2026〕88号"),
                 ("财务评估报告", "XX会计师事务所"), ("法律意见书", "XX律所")]:
    req("/api/docs", {"project_id": pid, "doc_type": dt, "note": note})
n = c.execute("SELECT COUNT(*) FROM project_docs WHERE project_id=?", (pid,)).fetchone()[0]
step("材料登记4项", n == 4)

# 3 借环节审核
req("/api/review/run", {"project_id": pid, "stage": "借", "reviewer": "E2E自动化"})
rid = c.execute("SELECT MAX(id) FROM reviews").fetchone()[0]
nos = {r[0] for r in c.execute("SELECT rule_no FROM findings WHERE review_id=?", (rid,)).fetchall()}
step("借环节审核产生问题", len(nos) >= 3)
assert "AP-003" in nos and "AP-006" in nos, f"关键规则未命中: {nos}"
step(f"AP-003覆盖倍数P0 + AP-006缺一案两书 命中 ({len(nos)}条)")

# 4 审核结果页
code, _ = req(f"/review/{rid}")
step("审核结果页", code == 200)

# 5 报告生成（含 docx 转换）
_, text = req("/api/review/report", {"review_id": rid}, expect=(200,))
j = json.loads(text)
jdocx = j
assert os.path.exists(j["report_path"]) and "不予通过" in j["conclusion"]
step(f"审核报告生成：{os.path.basename(j['report_path'])}")
assert j.get("docx_path", "").endswith(".docx") and os.path.exists(j["docx_path"]), j.get("docx_path")
step(f"DOCX 同步生成：{os.path.basename(j['docx_path'])}")

# 5b AI 点评（GLM 辅助，不可用不阻断）
_, text = req("/api/review/ai_comment", {"review_id": rid}, expect=(200,))
jc = json.loads(text)
assert jc["comment"]
step(f"AI 点评返回（mode={jc['mode']}）：{jc['comment'][:40]}…")

# 5c 材料上传（PDF 文本抽取）
import fitz
pdf_path = os.path.join(os.environ.get("TMPDIR", os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")), "gdwpma_e2e.pdf")
if not os.path.exists(pdf_path):
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((72, 100), "E2E test pdf: annual income 1500 wan")
    d.save(pdf_path); d.close()
with open(pdf_path, "rb") as fh:
    pdf_bytes = fh.read()
req("/api/docs", {"project_id": pid, "doc_type": "融资平衡方案", "note": "E2E上传"},
    raw_files={"file": ("e2e_平衡方案.pdf", pdf_bytes, "application/pdf")})
c2 = database.conn()
ext = c2.execute("SELECT extracted_text FROM project_docs WHERE project_id=? AND doc_type='融资平衡方案'", (pid,)).fetchone()
c2.close()
assert ext and ext[0] and "E2E test pdf" in ext[0], f"PDF 文本抽取失败: {ext}"
step("PDF 上传 + 文本抽取入库")

# 6 整改闭环
fid = c.execute("SELECT MIN(id) FROM findings WHERE review_id=?", (rid,)).fetchone()[0]
req("/api/rectify", {"finding_id": fid, "action": "plan", "plan": "补充融资平衡方案并重报", "due_date": "2026-11-30"})
st = c.execute("SELECT status FROM findings WHERE id=?", (fid,)).fetchone()[0]
step("整改计划→整改中", st == "整改中")
req("/api/rectify", {"finding_id": fid, "action": "result", "result": "已补充方案并重新申报"})
req("/api/rectify", {"finding_id": fid, "action": "recheck", "ok": "1", "recheck_note": "复核通过"})
st = c.execute("SELECT status FROM findings WHERE id=?", (fid,)).fetchone()[0]
step("复核→已闭环", st == "已闭环")

# 7 底稿留痕
wps = c.execute("SELECT COUNT(*) FROM workpapers WHERE project_id=?", (pid,)).fetchone()[0]
step(f"全过程底稿留痕 {wps} 条", wps >= 5)

# 8 立卷归档
_, text = req(f"/api/archive/{pid}", {}, expect=(200,))
arc = json.loads(text)["archive_dir"]
assert os.path.exists(os.path.join(arc, "卷内目录.md"))
step(f"立卷归档：{os.path.basename(arc)}")

# 9 法规检索（页面级）
code, text = req("/regulations?q=" + urllib.parse.quote("负面清单"))
assert "52号" in text
step("法规FTS检索命中52号文")
c.close()


# 10 用户管理 + 改密
code, text = req("/users")
assert "用户列表" in text
step("用户管理页")
code, text = req("/api/users/add", {"username": "auditor2", "password": "test123", "role": "审核员"})
code, text = req("/users")
assert "auditor2" in text
step("管理员添加用户 auditor2")
code, text = req("/api/me/password", {"old": "gdwpma@2026", "new": "gdwpma@2026x"})
code, text = req("/api/me/password", {"old": "gdwpma@2026x", "new": "gdwpma@2026"})  # 改回
step("本人改密往返")

# 11 扫描件 OCR（生成图片PDF→OCR）
import fitz, numpy as _np
doc = fitz.open(); pg = doc.new_page(width=300, height=120)  # 无文本层的纯图片页
pix = pg.get_pixmap(dpi=120)
import cv2 as _cv
img = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(pix.height, pix.width, pix.n)
img = _cv.putText(img.copy(), "OCR TEST 2026", (20, 70), _cv.FONT_HERSHEY_SIMPLEX, 1.0, (0,0,0), 2)
ok, buf = _cv.imencode(".png", img)
req("/api/docs", {"project_id": pid, "doc_type": "实施方案", "note": "扫描件"},
    raw_files={"file": ("scan.pdf", buf.tobytes(), "application/pdf")})
c2 = database.conn()
row = c2.execute("SELECT id FROM project_docs WHERE project_id=? AND note='扫描件' ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
c2.close()
_, text = req(f"/api/docs/{row[0]}/ocr", {}, expect=(200, 500, 501))
j = json.loads(text)
if "chars" in j:
    assert j["chars"] > 0
    step(f"扫描件 OCR 识别 {j['chars']} 字（RapidOCR）")
else:
    step(f"扫描件 OCR：引擎不可用降级（{j.get('error','')[:40]}）")

# 12 docx -> PDF（Word COM）
docx_file = jdocx["docx_path"]
_, text = req("/api/review/pdf", {"docx_path": docx_file}, expect=(200, 500))
jp = json.loads(text)
if "pdf_path" in jp:
    assert os.path.exists(jp["pdf_path"])
    step(f"docx→PDF：{os.path.basename(jp['pdf_path'])}")
else:
    step(f"docx→PDF 降级：{jp.get('error','')[:40]}")

print(f"\nALL PASS ({len(OK)} steps)")

