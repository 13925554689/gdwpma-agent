# -*- coding: utf-8 -*-
"""规则引擎：YAML规则即数据，扫描项目→自动检查+材料核对→问题清单。AI不编造证据，每条问题带条款+证据指向。"""
import os, re, yaml, datetime
import db as database

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules")
STAGE_FILES = {"借": "apply.yaml", "发": "issue.yaml", "用": "use.yaml", "还": "repay.yaml"}

# 材料清单（申报阶段法定/惯例材料，52号文+一案两书+前期要件）
DOC_TYPES = ["实施方案", "融资平衡方案", "财务评估报告", "法律意见书", "信用评级报告",
             "可研批复", "初设批复", "用地预审", "环评", "额度文件", "信息披露文件"]
REQUIRED_DOCS = ["实施方案", "融资平衡方案", "财务评估报告", "法律意见书", "可研批复"]

# 负面清单领域（国办发〔2024〕52号附件，节选高频项）
NEGATIVE_CATEGORIES = ["楼堂馆所", "主题文旅公园", "仿古商业街区", "商业房地产开发",
                       "一般竞争性产业", "景观工程", "形象工程"]

def load_rules(stage):
    path = os.path.join(RULES_DIR, STAGE_FILES[stage])
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["rules"]

def all_rules():
    out = []
    for stage in STAGE_FILES:
        out.extend(load_rules(stage))
    return out

def _docs(project_id):
    c = database.conn()
    rows = c.execute("SELECT doc_type, note FROM project_docs WHERE project_id=?", (project_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

def _coverage(p):
    """收益覆盖倍数 = 年均运营收入×年限 / 债券本息。缺失返回 None。"""
    if not p.get("annual_income") or not p.get("bond_amount"):
        return None
    term = p.get("term_years") or 10
    rate = p.get("rate") or 3.0
    interest = p["bond_amount"] * rate / 100 * term
    total_debt = p["bond_amount"] + interest
    return round(p["annual_income"] * term / total_debt, 3)

def auto_eval(cond, p, ctx):
    """自动条件求值。支持 and 组合、coverage/cap_ratio/limit_remaining/idle_days 等。"""
    if " and " in cond:
        return all(auto_eval(c.strip(), p, ctx) for c in cond.split(" and "))
    cov = ctx.get("coverage")
    def val(name):
        if name == "coverage": return cov
        return p.get(name)
    m = re.match(r"([\d.]+)\s*(<=|<|>|>=)\s*(\w+)\s*(<=|<|>|>=)\s*([\d.]+)$", cond)
    if m:
        lo, op1, var, op2, hi = m.groups()
        v = val(var)
        if v is None: return False
        ops = {"<": lambda a,b: a<b, "<=": lambda a,b: a<=b, ">": lambda a,b: a>b, ">=": lambda a,b: a>=b}
        return ops[op1](float(lo), v) and ops[op2](v, float(hi))
    m = re.match(r"(\w+)\s*(<|>|<=|>=)\s*(.+)$", cond)
    if m:
        var, op, rhs = m.groups()
        v = val(var)
        if v is None: return False
        # 右值先查项目字段，再查 ctx，最后当字面量（IS-007: bond_amount > approved_scale）
        r = p.get(rhs, ctx.get(rhs, rhs))
        try: r = float(r)
        except (TypeError, ValueError): pass
        ops = {"<": lambda a,b: a<b, ">": lambda a,b: a>b, "<=": lambda a,b: a<=b, ">=": lambda a,b: a>=b}
        try: return ops[op](float(v), float(r))
        except (TypeError, ValueError): return False
    return False

def run_review(project_id, stage, reviewer):
    """执行一个环节审核：自动规则 + 材料核对规则。返回 (review_id, [finding])。"""
    c = database.conn()
    p = dict(c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
    docs = _docs(project_id)
    doc_types = [d["doc_type"] for d in docs]
    ctx = {"coverage": _coverage(p)}
    today = datetime.date.today()

    cur = c.cursor()
    cur.execute("INSERT INTO reviews(project_id,stage,reviewer,reviewed_at) VALUES(?,?,?,?)",
                (project_id, stage, reviewer, database.now()))
    review_id = cur.lastrowid

    findings = []
    for rule in load_rules(stage):
        hit, desc = False, ""
        # 1) 自动条件
        if rule.get("auto"):
            hit = auto_eval(rule["auto"], p, ctx)
            if hit:
                desc = f"自动检查命中：{rule['check']}"
        # 2) 材料核对类（任何环节：规则要求某证据齐备而必备材料缺失）
        if not hit:
            for ev in rule.get("evidence", []):
                if ev in REQUIRED_DOCS and ev not in doc_types:
                    hit, desc = True, f"缺少必备材料《{ev}》：{rule['check']}"
                    break
        # 3) 负面清单关键词（借环节 AP-001）
        if not hit and rule["no"] == "AP-001":
                bad = [k for k in NEGATIVE_CATEGORIES if k in (p.get("category") or "")]
                if bad:
                    hit, desc = True, f"项目类别“{p.get('category')}”触碰负面清单关键词：{ '、'.join(bad) }"
        if hit:
            cur.execute(
                "INSERT INTO findings(review_id,project_id,rule_no,rule_name,stage,severity,clause,description,evidence) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (review_id, project_id, rule["no"], rule["name"], stage, rule["severity"],
                 rule["clause"], desc or rule["check"], "、".join(rule.get("evidence", []))))
            findings.append({"rule_no": rule["no"], "rule_name": rule["name"],
                             "severity": rule["severity"], "clause": rule["clause"],
                             "description": desc or rule["check"],
                             "evidence": "、".join(rule.get("evidence", []))})
    c.commit()

    missing = [d for d in REQUIRED_DOCS if d not in doc_types]
    summary = f"覆盖倍数={ctx['coverage'] if ctx['coverage'] else '—'}；材料{'齐备' if not missing else '缺'+str(len(missing))}项；命中问题{len(findings)}条"
    c.execute("UPDATE reviews SET summary=? WHERE id=?", (summary, review_id))
    c.commit(); c.close()
    database.wp(project_id, stage, "审核", f"执行{stage}环节审核，{summary}", None)
    return review_id, findings

if __name__ == "__main__":
    rs = all_rules()
    print("rules total:", len(rs))
    from collections import Counter
    print(Counter(r["stage"] if "stage" in r else "?" for r in rs))
