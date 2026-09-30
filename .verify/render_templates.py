# -*- coding: utf-8 -*-
"""渲染 base.html / login.html 到 .verify/ 供本地浏览器预览验证"""
import os
from jinja2 import Environment, FileSystemLoader, ChainableUndefined

TPL_DIR = r"D:\CC-Workspace\construction-spec-query-v2-aux\app\templates"
OUT_DIR = r"D:\CC-Workspace\construction-spec-query-v2-aux\.verify"
os.makedirs(OUT_DIR, exist_ok=True)

env = Environment(loader=FileSystemLoader(TPL_DIR), undefined=ChainableUndefined, autoescape=False)


def render(name, out_name, **ctx):
    html = env.get_template(name).render(**ctx)
    html = html.replace('href="/static/', 'href="../static/').replace('src="/static/', 'src="../static/')
    path = os.path.join(OUT_DIR, out_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


render("base.html", "render_base.html", hide_tree=False, left_content=None, center_content=None)
render("login.html", "render_login.html", error=None)
print("RENDER OK")
