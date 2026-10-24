const normalize = text => text.normalize("NFKC").toLowerCase().replace(/[\s_-]+/g, "");

export function searchDocuments(index, query) {
  const q = normalize(query);
  if (!q) return [];
  const routes = index.routes.filter(route => route.queries.some(word => normalize(word) === q));
  return index.documents.map(doc => {
    const title = normalize(doc.title);
    const words = doc.keywords.map(normalize);
    let score = title === q ? 100 : title.includes(q) ? 65 : 0;
    if (words.some(word => word === q)) score += 80;
    else if (words.some(word => word.includes(q) || (word.length > 1 && q.includes(word)))) score += 35;
    if (normalize(doc.headings.join(" ")).includes(q)) score += 20;
    if (normalize(doc.text).includes(q)) score += 8;
    for (const route of routes) {
      if (route.target_id === doc.id) score += 300;
      if (route.also_show?.includes(doc.id)) score += 100;
    }
    return {doc, score};
  }).filter(hit => hit.score > 0).sort((a, b) => b.score - a.score).slice(0, 10);
}

export function needsChoice(index, query) {
  return index.routes.some(route => route.mode === "choose" &&
    route.queries.some(word => normalize(word) === normalize(query)));
}

async function initializeSearch() {
  const input = document.querySelector("#help-search");
  const results = document.querySelector("#search-results");
  const status = document.querySelector("#search-status");
  const indexURL = new URL(document.body.dataset.searchIndex, window.location.href);
  let indexPromise;
  let sequence = 0;
  let hits = [];
  let currentIndex;
  const close = () => {results.hidden = true; input.setAttribute("aria-expanded", "false");};
  const loadIndex = () => indexPromise ??= fetch(indexURL).then(response => {
    if (!response.ok) throw new Error("Search index unavailable");
    return response.json();
  }).catch(error => {indexPromise = undefined; throw error;});
  async function show() {
    const call = ++sequence;
    const query = input.value;
    if (!query.trim()) {hits = []; close(); return;}
    try {
      const index = await loadIndex();
      if (call !== sequence) return;
      currentIndex = index;
      hits = searchDocuments(index, query);
      results.replaceChildren();
      for (const {doc} of hits) {
        const anchor = document.createElement("a");
        anchor.className = "hit";
        anchor.href = new URL(doc.url, indexURL).href;
        const title = document.createElement("strong");
        const detail = document.createElement("small");
        title.textContent = doc.title;
        detail.textContent = doc.summary;
        anchor.append(title, detail);
        results.append(anchor);
      }
      if (!hits.length) results.innerHTML = '<p class="empty">没有找到对应主题，可以试试功能名、API 名或现象关键词。</p>';
      results.hidden = false;
      input.setAttribute("aria-expanded", "true");
      status.textContent = `找到 ${hits.length} 篇相关指南`;
    } catch {
      if (call !== sequence) return;
      hits = [];
      results.innerHTML = '<p class="empty">搜索索引暂时无法加载，可通过分类目录继续阅读。</p>';
      results.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }
  }
  input.addEventListener("input", show);
  input.addEventListener("keydown", async event => {
    if (event.key === "Escape") {sequence++; close(); return;}
    if (event.key === "ArrowDown") {event.preventDefault(); results.querySelector("a")?.focus();}
    if (event.key === "Enter") {
      event.preventDefault();
      const query = input.value;
      await show();
      if (input.value !== query) return;
      if (!hits.length || results.hidden) return;
      if (needsChoice(currentIndex, input.value)) results.querySelector("a")?.focus();
      else window.location.assign(new URL(hits[0].doc.url, indexURL).href);
    }
  });
  results.addEventListener("keydown", event => {
    const links = [...results.querySelectorAll("a")];
    const at = links.indexOf(document.activeElement);
    if (event.key === "Escape") {close(); input.focus();}
    if (event.key === "ArrowDown") {event.preventDefault(); links[Math.min(at + 1, links.length - 1)]?.focus();}
    if (event.key === "ArrowUp") {event.preventDefault(); if (at <= 0) input.focus(); else links[at - 1].focus();}
  });
  document.addEventListener("click", event => {if (!event.target.closest(".searchbox")) close();});
  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault(); input.focus(); input.select();
    }
  });
  document.querySelector("#menu-toggle").addEventListener("click", event => {
    const open = document.body.classList.toggle("menu-open");
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });
}

if (typeof document !== "undefined") initializeSearch();
