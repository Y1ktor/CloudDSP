# Archived EQ prototypes

These equalizer prototypes were archived on 2026-10-02. Their source files,
sample assets, and internal folder structure are retained in Git. The current
application is [the shared frontend](../../frontend/README.md).

## Contents

```text
eq/
  vanilla/
    html/index.html
    js/
    assets/
  react/
    src/EqPage.jsx
    src/components/
      AudioPlayerBar.jsx
      AutomationBar.jsx
      EqCanvas.jsx
    src/vanilla/
    src/assets/css/styles.css
```

`vanilla/` preserves the earlier `cloudDeployment/frontend/` tree, including
its HTML entry point, JavaScript engine, styles, and sample audio. Relative
HTML and module references remain valid. To inspect it locally, start a static
server from the repository root:

```bash
python3 -m http.server 8030 --bind 127.0.0.1 --directory archive/eq/vanilla
```

Then open [the archived equalizer](http://127.0.0.1:8030/html/index.html).

`react/` preserves the unused EQ page, its three components, and its JavaScript
engine from `frontend/src/` (originally `cloudDeployment/frontend-react/src/`).
It is a component source snapshot without a separate application entry point
or dependency installation. Its relative imports retain the same layout.

The archived React stylesheet is a snapshot. The active frontend still needs
its own `src/assets/css/styles.css` for the studio shell and upload controls.
Changes to the active stylesheet do not update this historical copy.
