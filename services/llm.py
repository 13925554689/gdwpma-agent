# -*- coding: utf-8 -*-
"""GLM 算力客户端：与 Hermes 同源（config.yaml providers.zai = open.bigmodel.cn），模型 glm-5.3-flash。
仅用于法规问答 RAG 与文字润色，审核判定一律走规则引擎。"""
import os, json, re, urllib.request

HERMES_CONFIG = os.path.expandvars(r"%LOCALAPPDATA%\hermes\config.yaml")
MODEL = "glm-5.3-flash"
TIMEOUT = 60

def _cred():
    """从 Hermes config.yaml 读 zai provider 的 base_url 与 key（config.api_key 或 env，key 不落日志）。"""
    import yaml
    with open(HERMES_CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    z = cfg["providers"]["zai"]
    base = z["base_url"].rstrip("/")
    key = z.get("api_key") or os.environ.get(z.get("api_key_env") or "") or os.environ.get(z.get("key_env") or "")
    if not key:
        raise RuntimeError("未找到 GLM api_key（config zai.api_key / env ZHIPU_API_KEY 均为空）")
    return base, key

def chat(prompt, system="你是政府债务管理法规助手，只依据提供的法规内容回答，并标注依据条款。答不出就明确说答不出，不编造。", context=""):
    base, key = _cred()
    if context:
        prompt = f"参考法规内容：\n{context}\n\n问题：{prompt}"
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt}], "temperature": 0.3}).encode("utf-8")
    # ponytail: 本机环境代理对 bigmodel.cn 超时，直连；如需统一出口代理改回 urllib 默认
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"{base}/chat/completions", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with opener.open(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]

def answer_with_regulations(question, k=4):
    """法规库 RAG：FTS5 检索相关法规 → 取全文关键段 → GLM 生成带依据的回答。检索为空则直接问模型并降级提示。"""
    import db as database
    hits = database.search_regulations(question, limit=k)
    parts = []
    for h in hits:
        c = database.conn()
        row = c.execute("SELECT content FROM regulations WHERE id=?", (h["id"],)).fetchone()
        c.close()
        body = (row["content"] if row else h["snip"])[:800]
        parts.append(f"【{h['title']}】({h['doc_no'] or '文号不详'}，{h['level']})：{body}")
    ctx = "\n\n".join(parts)
    try:
        ans = chat(question, context=ctx or "")
        src = [f"{h['title']}({h['doc_no']})" for h in hits]
        return {"answer": ans, "sources": src, "mode": "glm+rag" if hits else "glm"}
    except Exception as e:
        # 降级：纯检索模式
        if hits:
            return {"answer": "GLM 暂不可用，以下为法规库原文检索结果：\n\n" + ctx,
                    "sources": [h["title"] for h in hits], "mode": "检索降级"}
        return {"answer": f"检索与 GLM 均不可用：{e}", "sources": [], "mode": "失败"}

if __name__ == "__main__":
    base, _ = _cred()
    print("base_url:", base)
    r = answer_with_regulations("专项债券投向领域实行的清单管理制度是什么？")
    print("mode:", r["mode"])
    print("answer:", r["answer"][:300])
