# -*- coding: utf-8 -*-
"""登录/会话：单机部署，Flask session + PBKDF2（db.check_user）。"""
import os
from functools import wraps
from flask import session, redirect, url_for, request, render_template

def init_auth(app):
    app.secret_key = os.environ.get("GDWPMA_SECRET") or "gdwpma-singlebox-5210"  # ponytail: 单机固定key，上服务器再换env

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w

def login_page():
    return render_template("login.html", error=None, next=request.args.get("next", ""))

def do_login():
    import db as database
    u = database.check_user(request.form.get("username", ""), request.form.get("password", ""))
    nxt = request.form.get("next") or "/"
    if not u:
        return render_template("login.html", error="用户名或密码错误", next=nxt), 401
    session["uid"], session["uname"], session["role"] = u["id"], u["username"], u["role"]
    return redirect(nxt if nxt.startswith("/") else "/")

def do_logout():
    session.clear()
    return redirect(url_for("login"))
