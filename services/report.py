# -*- coding: utf-8 -*-
"""分县市审核报告生成 + 底稿立卷归档。"""
import os, re, json, datetime
import db as database

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（services/ 的上级），对齐 db.BASE
OUT_DIR = os.path.join(BASE, "data", "outputs")

REPORT_TMPL = """# {county}政府债券项目{stage}环节审核报告

**报告编号**：{report_no}　**审核人**：{reviewer}　**日期**：{date}

## 一、项目基本情况

| 项目 | 内容 |
|------|------|
| 项目名称 | {name} |
| 项目单位 | {owner_unit} |
| 项目类别 | {category} |
| 总投资 | {total_invest} 万元 |
| 申请债券金额 | {bond_amount} 万元 |
| 期限/利率 | {term_years} 年 / {rate}% |
| 年均运营收入 | {annual_income} 万元 |
| 收益覆盖倍数 | {coverage} |

## 二、审核依据

国发〔2014〕43号、国办发〔2024〕52号、财预〔2021〕61号、财库〔2020〕36号及本系统规则库（{stage}环节）。

## 三、审核发现（{n} 项）

{findings_table}

## 四、审核结论

{conclusion}

## 五、整改要求

{demands}

---
*本报告由 GDWPMA-AI 政府债全过程管理智能体生成，数据预填，须经审核人员复核签字后生效。*
"""

def _fmt_wan(v):
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"

def generate_report(review_id):
    c = database.conn()
    rv = dict(c.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone())
    p = dict(c.execute("SELECT * FROM projects WHERE id=?", (rv["project_id"],)).fetchone())
    fs = [dict(r) for r in c.execute("SELECT * FROM findings WHERE review_id=?", (review_id,)).fetchall()]
    c.close()

    sev_order = {"P0": 0, "P1": 1, "P2": 2}
    fs.sort(key=lambda f: sev_order.get(f["severity"], 9))
    rows = "\n".join(
        f"| {i+1} | {f['rule_no']} {f['rule_name']} | **{f['severity']}** | {f['clause']} | {f['description']} | 证据：{f['evidence'] or '—'} |"
        for i, f in enumerate(fs))
    findings_table = ("| # | 规则 | 级别 | 依据条款 | 问题描述 | 对应证据 |\n|---|------|------|----------|----------|----------|\n" + rows) if fs else "未发现问题。"

    p0 = [f for f in fs if f["severity"] == "P0"]
    p1 = [f for f in fs if f["severity"] == "P1"]
    if p0:
        conclusion = f"**不予通过**：存在 {len(p0)} 项 P0 否决问题，项目不得进入下一环节，须整改后重新申报。"
    elif p1:
        conclusion = f"**限期补正**：存在 {len(p1)} 项 P1 问题，须在整改期限内补正并提交复核。"
    else:
        conclusion = "**通过**：未发现 P0/P1 问题，P2 提示项纳入辅导清单。"

    demands = []
    for f in fs:
        act = "立即停止相关行为并整改" if f["severity"] == "P0" else ("限期补正" if f["severity"] == "P1" else "纳入辅导")
        demands.append(f"- 【{f['rule_no']}】{f['rule_name']}：{act}；涉及证据材料：{f['evidence'] or '—'}；依据：{f['clause']}")
    demands_text = "\n".join(demands) if demands else "无。"

    date = datetime.date.today().strftime("%Y-%m-%d")
    safe_county = re.sub(r'[\\/:*?"<>|]', "", p["county"] or "县")  # 文件名清洗，county 来自用户表单
    report_no = f"GDWPMA-{safe_county}-{rv['stage']}-{review_id:04d}"
    coverage = "—"
    if p.get("annual_income") and p.get("bond_amount"):
        term = p.get("term_years") or 10
        rate = p.get("rate") or 3.0
        coverage = f"{p['annual_income']*term/(p['bond_amount']*(1+rate/100*term)):.2f}"

    text = REPORT_TMPL.format(county=p["county"], stage=rv["stage"], report_no=report_no,
                              reviewer=rv["reviewer"], date=date, name=p["name"],
                              owner_unit=p["owner_unit"] or "—", category=p["category"] or "—",
                              total_invest=_fmt_wan(p.get("total_invest")), bond_amount=_fmt_wan(p.get("bond_amount")),
                              term_years=p.get("term_years") or "—", rate=p.get("rate") or "—",
                              annual_income=_fmt_wan(p.get("annual_income")), coverage=coverage,
                              n=len(fs), findings_table=findings_table, conclusion=conclusion, demands=demands_text)

    os.makedirs(OUT_DIR, exist_ok=True)
    fname = f"{report_no}_{date}.md"
    fpath = os.path.join(OUT_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(text)
    c = database.conn()
    c.execute("UPDATE reviews SET report_path=? WHERE id=?", (fpath, review_id))
    c.commit(); c.close()
    database.wp(p["id"], rv["stage"], "审核报告", f"生成{report_no}", fpath)
    return fpath, conclusion

def archive_project(project_id):
    """按项目立卷：全过程底稿+报告打包为一个归档目录，生成卷内目录。"""
    c = database.conn()
    p = dict(c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
    wps = [dict(r) for r in c.execute(
        "SELECT * FROM workpapers WHERE project_id=? ORDER BY id", (project_id,)).fetchall()]
    fds = [dict(r) for r in c.execute(
        "SELECT * FROM findings WHERE project_id=? ORDER BY id", (project_id,)).fetchall()]
    c.close()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    arc = os.path.join(OUT_DIR, f"archive_p{project_id}_{ts}")
    os.makedirs(arc, exist_ok=True)
    idx = [f"# 立卷归档：{p['name']}（{p['county']}）", f"", f"归档时间：{ts}", f"", "## 卷内目录", ""]
    for w in wps:
        line = f"- [{w['created_at']}] {w['stage']}·{w['action']}：{w['content']}"
        if w["file_path"] and os.path.exists(w["file_path"]):
            dst = os.path.join(arc, os.path.basename(w["file_path"]))
            try:
                with open(w["file_path"], "rb") as s, open(dst, "wb") as d: d.write(s.read())
                line += f"（附件：{os.path.basename(w['file_path'])}）"
            except OSError:
                pass
        idx.append(line)
    idx.append(f"\n## 问题台账（{len(fds)}条）\n")
    for f in fds:
        idx.append(f"- 【{f['severity']}】{f['rule_no']} {f['rule_name']} — {f['status']}；整改结果：{f['result'] or '—'}")
    with open(os.path.join(arc, "卷内目录.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(idx))
    database.wp(project_id, "归档", "立卷", f"项目立卷归档，底稿{len(wps)}条，问题{len(fds)}条", arc)
    return arc

if __name__ == "__main__":
    print("report/archive module ok")
