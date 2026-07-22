# Walkthroughs ship as raw HTML, not Sphinx pages

The code walkthroughs (`docs/_extra/walkthrough/`) are hand-authored standalone
HTML pages with inline SVG figures and their own CSS/theming. Converting them
to RST/MyST broke the figures and layout, so they are copied verbatim into the
build output via `html_extra_path` and linked from a searchable Sphinx landing
page (`docs/walkthroughs.rst`) plus the relevant module pages. Each HTML page
carries a relative "← CircuitGenome docs" back-link so it is not a dead end.

## Considered Options

- **Convert to MyST/RST** — full search + nav integration, but destroys the
  hand-tuned figures and layout, at large conversion cost per page.
- **iframe wrapper pages** — pages appear in the sidebar but scroll, anchors,
  and mobile behavior all degrade; content still not indexed.
- **Separate site (GitHub Pages / RTD subproject)** — clean separation, but a
  second deploy pipeline and no coupling to RTD's versioned docs.
- **`html_extra_path` link-out (chosen)** — zero conversion, perfect rendering,
  versions alongside the docs on Read the Docs.

## Consequences

- Walkthrough content is invisible to Sphinx search; the landing page's
  per-walkthrough descriptions are the searchable surface.
- Filenames are stable, undated URLs; pages are living documents edited in
  place (dating via git). See the contributing guide for the co-update rule.
- The source dir is nested as `docs/_extra/walkthrough/` because
  `html_extra_path` copies directory *contents* into the output root — listing
  `walkthrough/` directly would overwrite Sphinx's own `index.html`.
