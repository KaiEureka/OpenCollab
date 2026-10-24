# Help center

The project site links to the help center at `help/`, which opens the Chinese
guides on first visit. A global Chinese and English switch selects the help center
edition. The browser remembers the selected edition when entering through `help/`
again. Explicit article URLs open their specified edition. The English
page at `help/en/` uses the same reader layout and displays "Coming soon."
Chinese article URLs remain under `help/zh-CN/`. The 68 guides are
maintained in `zh-CN/topics/`. Navigation pages and the explicitly planned VM
extension page live alongside them. Each Markdown document is rendered to an
independent HTML page, so article links work with JavaScript disabled. Browser
search loads a local static index and uses no external model service.

## Edit and build

Topic front matter is a JSON object enclosed in YAML delimiters. It owns the
stable document ID, title, category, summary, search terms, related topics and
reviewed source revision. `catalog.json` owns category labels and navigation
order. `search-routes.json` owns explicit synonyms and disambiguation choices.
Natural questions in `questions.md` also become search routes during the build.

Preserve document IDs when changing titles. Relative Markdown links are resolved
to generated HTML destinations. The builder reports a missing destination instead
of publishing a broken internal link. Source references identify the framework
revision that the guide describes. Update those references after checking the
corresponding API, CLI, configuration or runtime behavior.

Use an existing environment with `markdown-it-py`, or install the small build
requirements in a disposable environment. From this branch's repository root,
run the following commands.

```bash
python -m pip install -r tools/help-requirements.txt
python tools/build_help.py
python tools/check_help.py
node --test tools/help-search.test.mjs
python -m http.server 8000 --bind 127.0.0.1
```

Preview `/help/` through the local server. Generated HTML and the search
index are committed because GitHub Pages publishes this branch directly. CSS
and JavaScript are maintained in `help/assets/`. The `.nojekyll` file preserves
the site's existing static publishing behavior.

## Validation and publication

The local checks verify generated links and anchors, guide coverage, source
metadata, code-block syntax, search routes, subpath URL resolution and homepage
entry points. Model, Docker and external-service examples have environment
requirements; their execution coverage is recorded separately in PR validation.
Source code examples should be checked against the documented framework revision.

Review the Markdown sources and generated pages together. Publish by merging the
reviewed change into `gh-pages`. The project homepage navigation, hero action and
quick-start section all link to `help/`; every help page links back to the
project homepage. Application code remains on `main`.
