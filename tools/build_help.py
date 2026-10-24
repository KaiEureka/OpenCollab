from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "help-src" / "zh-CN"
OUTPUT = ROOT / "help" / "zh-CN"
SITE_URL = "https://rise-x-lab.github.io/OpenCollab/"
MD = MarkdownIt("commonmark", {"html": False}).enable("table")


@dataclass
class Page:
    source: Path
    destination: Path
    metadata: dict
    body: str
    headings: list[tuple[str, str]]
    tokens: list


def relative(target: Path, source: Path) -> str:
    return Path(os.path.relpath(target, source.parent)).as_posix()


def read_markdown(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        metadata, body = text[4:].split("\n---\n", 1)
        return json.loads(metadata), body.lstrip()
    return {}, text


def heading_ids(tokens: list) -> list[tuple[str, str]]:
    seen: dict[str, int] = {}
    headings = []
    for i, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        title = tokens[i + 1].content.replace("`", "")
        base = re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", title.lower())) or "section"
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchor = f"{base}-{count}" if count else base
        token.attrSet("id", anchor)
        if token.tag in {"h2", "h3"}:
            headings.append((anchor, title))
    return headings


def rewrite_links(tokens: list, page: Page, pages_by_source: dict[Path, Page]) -> None:
    for token in tokens:
        if token.children:
            rewrite_links(token.children, page, pages_by_source)
        if token.type != "link_open":
            continue
        href = token.attrGet("href") or ""
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or not parts.path:
            continue
        target = (page.source.parent / unquote(parts.path)).resolve()
        if target not in pages_by_source:
            raise ValueError(f"Unknown local document link in {page.source}: {href}")
        destination = pages_by_source[target].destination
        result = relative(destination, page.destination)
        if parts.fragment:
            result += "#" + parts.fragment
        token.attrSet("href", result)


def navigation(page: Page, pages: list[Page], categories: list[dict]) -> str:
    parts = []
    for category in categories:
        entries = [p for p in pages if p.metadata.get("category") == category["id"]]
        opened = " open" if page.metadata.get("category") == category["id"] else ""
        links = []
        for entry in entries:
            current = ' aria-current="page"' if entry is page else ""
            links.append(
                f'<a href="{relative(entry.destination, page.destination)}"{current}>'
                f'{html.escape(entry.metadata["title"])}</a>'
            )
        parts.append(f'<details{opened}><summary>{html.escape(category["title"])}'
                     f' <small>{len(entries)}</small></summary>{"".join(links)}</details>')
    return "".join(parts)


def language_switch(destination: Path, language: str) -> str:
    links = []
    for code, label in (("zh-CN", "中文"), ("en", "English")):
        target = relative(ROOT / "help" / code / "index.html", destination)
        current = ' aria-current="true"' if code == language else ""
        links.append(f'<a href="{target}" lang="{code}" hreflang="{code}"{current}>{label}</a>')
    return '<nav class="language-switch" aria-label="Language">' + "".join(links) + '</nav>'


def render_page(page: Page, pages: list[Page], config: dict) -> str:
    title = page.metadata["title"]
    home = relative(OUTPUT / "index.html", page.destination)
    project = relative(ROOT / "index.html", page.destination)
    css = relative(ROOT / "help" / "assets" / "help.css", page.destination)
    script = relative(ROOT / "help" / "assets" / "help.mjs", page.destination)
    search_index = relative(OUTPUT / "search-index.json", page.destination)
    paths = relative(OUTPUT / "reading-paths" / "index.html", page.destination)
    questions = relative(OUTPUT / "questions" / "index.html", page.destination)
    source_path = relative(page.source, page.destination)
    category = next((c["title"] for c in config["categories"]
                     if c["id"] == page.metadata.get("category")), "阅读导航")
    toc = "".join(f'<a href="#{quote(anchor)}">{html.escape(label)}</a>'
                  for anchor, label in page.headings)
    body = MD.renderer.render(page.tokens, MD.options, {})
    canonical = SITE_URL + page.destination.relative_to(ROOT).as_posix()
    description = page.metadata.get("summary", "OpenCollab 中文使用与开发指南")
    entry = ' data-help-entry="true"' if page.destination == ROOT / "help" / "index.html" else ""
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · OpenCollab 帮助中心</title>
<meta name="description" content="{html.escape(description, quote=True)}">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="{css}">
<script type="module" src="{script}"></script>
</head>
<body data-search-index="{search_index}"{entry}>
<a class="skip" href="#content">跳到正文</a>
<header class="topbar">
<a class="brand" href="{home}">OpenCollab <span>帮助中心</span></a>
<div class="searchbox">
<label class="sr-only" for="help-search">搜索帮助文档</label>
<input id="help-search" type="search" placeholder="搜索隔离、Workflow 语法、派发子任务……" autocomplete="off" aria-controls="search-results" aria-expanded="false">
<div id="search-results" class="results" hidden></div>
<div id="search-status" class="sr-only" role="status" aria-live="polite"></div>
</div>
{language_switch(page.destination, 'zh-CN')}
<a class="project-link" href="{project}">项目主页</a>
<button id="menu-toggle" type="button" aria-expanded="false" aria-controls="help-navigation">目录</button>
</header>
<div class="layout">
<nav id="help-navigation" aria-label="帮助分类">
<div class="nav-start"><a href="{home}">帮助首页</a><a href="{paths}">阅读路径</a><a href="{questions}">问题导航</a></div>
{navigation(page, pages, config["categories"])}
</nav>
<main id="content" tabindex="-1">
<p class="category">{html.escape(category)}</p>
{body}
<footer class="article-footer"><a href="{source_path}">Markdown 源文档</a><span>依据源码 {config['verified_ref'][:8]}</span><a href="{project}">返回项目主页</a></footer>
</main>
<aside class="toc" aria-label="本页内容"><strong>本页内容</strong>{toc}</aside>
</div>
</body>
</html>
'''


def render_english_placeholder() -> str:
    destination = ROOT / "help" / "en" / "index.html"
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Help Center · OpenCollab</title>
<meta name="description" content="OpenCollab Help Center. English guides are coming soon.">
<link rel="canonical" href="{SITE_URL}help/en/index.html">
<link rel="stylesheet" href="../assets/help.css">
<script type="module" src="../assets/help.mjs"></script>
</head>
<body>
<a class="skip" href="#content">Skip to content</a>
<header class="topbar">
<a class="brand" href="index.html">OpenCollab <span>Help Center</span></a>
<div class="searchbox">
<label class="sr-only" for="help-search">Search help</label>
<input id="help-search" type="search" placeholder="Coming soon" disabled>
</div>
{language_switch(destination, 'en')}
<a class="project-link" href="../../index.html">Project home</a>
<button id="menu-toggle" type="button" aria-expanded="false" aria-controls="help-navigation">Menu</button>
</header>
<div class="layout">
<nav id="help-navigation" aria-label="Help navigation">
<div class="nav-start"><a href="index.html" aria-current="page">Help home</a></div>
</nav>
<main id="content" tabindex="-1">
<h1>Help Center</h1>
<p>Coming soon.</p>
</main>
</div>
</body>
</html>
'''


def main() -> None:
    config = json.loads((ROOT / "help-src" / "catalog.json").read_text())
    pages = []
    for source in sorted(SOURCE.rglob("*.md")):
        metadata, body = read_markdown(source)
        if source.relative_to(SOURCE).as_posix() == "index.md":
            metadata.update(id="home", title="OC 中文帮助中心", category="navigation")
            destination = OUTPUT / "index.html"
        else:
            metadata.setdefault("id", source.stem)
            metadata.setdefault("title", body.splitlines()[0].lstrip("# "))
            metadata.setdefault("category", "navigation")
            destination = OUTPUT / metadata["id"] / "index.html"
        tokens = MD.parse(body)
        pages.append(Page(source.resolve(), destination, metadata, body, heading_ids(tokens), tokens))
    by_id = {p.metadata["id"]: p for p in pages}
    if len(by_id) != len(pages):
        raise ValueError("Document IDs must be unique")
    order = {key: i for i, key in enumerate(config["topic_order"])}
    pages.sort(key=lambda p: order.get(p.metadata["id"], len(order)))
    by_source = {p.source: p for p in pages}
    for page in pages:
        rewrite_links(page.tokens, page, by_source)
        page.destination.parent.mkdir(parents=True, exist_ok=True)
        page.destination.write_text(render_page(page, pages, config), encoding="utf-8")
    home = by_id["home"]
    landing = Page(home.source, ROOT / "help" / "index.html", home.metadata,
                   home.body, [], MD.parse(home.body))
    landing.headings = heading_ids(landing.tokens)
    rewrite_links(landing.tokens, landing, by_source)
    landing.destination.write_text(render_page(landing, pages, config), encoding="utf-8")
    english = ROOT / "help" / "en" / "index.html"
    english.parent.mkdir(parents=True, exist_ok=True)
    english.write_text(render_english_placeholder(), encoding="utf-8")
    documents = []
    for page in pages:
        meta = page.metadata
        visible = " ".join(
            child.content for token in page.tokens for child in token.children or []
            if child.type in {"text", "code_inline"}
        )
        documents.append({
            "id": meta["id"], "title": meta["title"], "summary": meta.get("summary", ""),
            "url": page.destination.relative_to(OUTPUT).as_posix(),
            "keywords": meta.get("keywords", []) + meta.get("aliases", []) + meta.get("questions", []),
            "headings": [label for _, label in page.headings], "text": visible,
        })
    routes = json.loads((ROOT / "help-src" / "search-routes.json").read_text())["routes"]
    questions = SOURCE / "questions.md"
    for line in questions.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| (.+?) \| \[.*?\]\(([^)]+)\) \|", line)
        if match:
            question, href = match.groups()
            target = by_source[(questions.parent / href).resolve()]
            routes.append({"queries": [question], "target_id": target.metadata["id"],
                           "mode": "direct", "also_show": []})
    for route in routes:
        for ident in [route["target_id"], *route.get("also_show", [])]:
            if ident not in by_id:
                raise ValueError(f"Unknown search destination: {ident}")
    (OUTPUT / "search-index.json").write_text(
        json.dumps({"documents": documents, "routes": routes}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Built {len(pages) + 2} help pages from {len(order)} guide topics")


if __name__ == "__main__":
    main()
