# AgentTelemetry landing page

This directory is a standalone, static marketing page for AgentTelemetry v2.0.0. It does
not start or connect to the dashboard and contains no personal usage records. Dashboard
figures shown in the interactive preview are illustrative sample data written directly
in the page.

## Preview locally

From the repository root, run:

```bash
python3 -m http.server 8788 --bind 127.0.0.1 --directory website
```

Then open <http://127.0.0.1:8788>. The page has no build step, package install, runtime
dependencies, analytics, API calls, or remote fonts, scripts, or images. Its JavaScript
handles the mobile menu, sample-dashboard views and window controls, command copying, and
finite product entrances, sliding selections, chart drawing and interruptible
disclosures. All page assets are served from this directory. CSS and JavaScript URLs carry
content versions so browser caches do not hide preview updates.

## Vercel hosting

The public site is https://agenttelemetry-app.vercel.app/.
The production project is **agenttelemetry** in **Uttam Deb's projects**. Deploy this
`website/` directory as the project root, with the Other framework preset, no install
or build command, and `.` as the output directory. `vercel.json` keeps these static
settings with the source. Keep API tokens outside the repository and deployment files.

## Hosting and downloads

The files are ready to host as a static site. Download links point to the existing
AgentTelemetry v2.0.0 release: macOS 13+ Apple Silicon and Intel DMGs, and a Windows
10/11 x64 installer. Those downloads are unsigned/unnotarized; see the repository README
for installation notes and checksums. The hero install chooser is native HTML and works
without JavaScript; it links to those same release assets and to the Linux/source
alternative. Native downloads use prominent labeled buttons. The source card and FAQ
lead with `pip install agenttelemetry-app`, followed by the `agenttelemetry` launch step;
`uvx agenttelemetry-app` is a secondary disclosure. Both have copy controls. The first-party Python package
is published as [agenttelemetry-app 2.0.0](https://pypi.org/project/agenttelemetry-app/2.0.0/).
Fresh `uvx` and `pip` installations from the public index have been verified.

## Product limits and privacy

The dashboard runs locally and reads the coding-tool logs already on that computer. Cost
figures are estimates at API list prices; subscription users do not pay per token, and
some source usage is unavailable in the logs. Optional device sharing is opt-in over a
local network. Optional account snapshots make a provider lookup only after the user
confirms it. The marketing preview does none of these things and never reads a real
ledger.

## Assets

The Apple and Windows marks in `assets/platform-icons/` are local Tabler SVGs with
documented source revision and MIT license; they do not depend on system fonts.

`assets/app-icon.png` is copied from the app's existing `static/icon-192.png`. The seven
tool SVGs and accompanying source and license files in `assets/tool-icons/` are copied
from `static/tool-icons/`; see its README for pinned sources and attribution details.

Team portraits in `assets/team/` are local copies of public GitHub profile images:
[uttamdeb](https://github.com/uttamdeb.png),
[moshiur-hridoy](https://github.com/moshiur-hridoy.png),
[Shad-Iqbal](https://github.com/Shad-Iqbal.png), and
[niloy-biswas](https://github.com/niloy-biswas.png). The team section follows the
manually curated order requested for the page. This order is static; the page serves its
local portraits and does not query GitHub at runtime.

## Design direction and research

The page is organized around a local developer's usage instrument: a compact,
specific introduction, the interactive dashboard, the supported tools, concrete
usage questions, the native companion, local processing/privacy, and installation.
The sample dashboard is the main visual. It contains invented figures and never
connects to a visitor's dashboard. Graphite surfaces, restrained mint accents,
system sans headings and monospace metadata give prose, data and actions distinct
roles. The team order remains Uttam, Shad, Moshiur and Niloy, without ranking labels.

The research informed the hierarchy and material choices rather than supplying a
layout to copy:

- [NN/G: homepage principles](https://www.nngroup.com/articles/homepage-design-principles/)
  and [information scent](https://www.nngroup.com/articles/information-scent/): say
  what the product does early, show useful context, and make actions explicit.
- [Linear: interface hierarchy](https://linear.app/now/behind-the-latest-design-refresh):
  let supporting navigation recede and vary visual weight by importance.
- [Linear: its approach to glass](https://linear.app/now/linear-liquid-glass),
  [Apple: materials](https://developer.apple.com/videos/play/wwdc2025/219/), and
  [NN/G: glassmorphism](https://www.nngroup.com/articles/glassmorphism/): reserve
  translucent depth for floating controls, with stable contrast for dense content.
- [Resend](https://resend.com/), [Raycast](https://www.raycast.com/), and
  [uttamdeb.com](https://uttamdeb.com/): foreground tangible product artifacts,
  use specific language, and establish a clear relationship between a surface
  and what sits behind it. No layouts or artwork were copied.

`styles.css` owns layout, typography and solid surfaces. `glass.css` owns the
foreground materials and solid fallbacks for increased contrast or reduced
transparency. The install chooser uses the browser's top layer where available,
with viewport-aware placement and a native `details` fallback. `motion.js` and
`motion.css` handle purposeful entrances and interaction feedback, respecting
reduced motion and changes to that preference while the page is open.

## Manual QA checklist

- Preview with the command above; confirm the page works without a build step and the
  sample dashboard is clearly labeled as illustrative.
- At a narrow viewport, open and close the navigation by button, section link, and
  Escape; confirm the button label and `aria-expanded` stay in sync and Escape returns
  focus to the button. Check that desktop navigation stays visible after resizing.
- Use the eight sidebar views, the Devices tab and all nine choices in the mobile
  selector; confirm the selected view changes and every panel remains keyboard reachable.
- Use the Overview, Sessions, and Devices tabs by click, arrow keys, Home, and End; confirm
  only the selected panel is shown and tab selection stays synchronized with the sidebar
  and mobile selector.
- Test preview minimize/restore, expand/collapse with Escape, and reset to Overview; check
  labels and pressed/expanded states stay synchronized.
- Open the hero install chooser and verify all three release downloads, their platform and
  file-type labels, and the Linux/source link. Follow the source link and check the visible
  pip command and launch step, copy status, and expandable uvx alternative.
- Try command copying in a secure browser context, then with clipboard access unavailable;
  verify success is announced only after copying and the fallback selects the command.
- Check download links, text contrast, and both regular and reduced-motion settings.
  With JavaScript disabled, verify the navigation, sample panels, native install chooser,
  source command and uvx disclosure, and page content remain readable. The preview
  should not contact the dashboard or send analytics requests.
- Follow all three usage-question links; confirm each selects its corresponding sample
  view and scrolls to the product. Copy the hero pip command as well as the source-card
  install commands and readable launch steps.
- Check finite entrances, the one-time chart trace, sliding selection, accordion
  opening/closing and button feedback. Rapidly toggle a disclosure and change
  reduced-motion preference while it animates; final native open/closed state must agree
  with its visible content. Animations settle after interactions and stop under reduced
  motion. `motion.js` owns motion; `site.js` owns controls.
- Inspect glass over visible background detail in the navigation, chooser and companion
  popover. Verify opaque fallbacks under increased contrast or reduced transparency.
  At 320, 390, 768, 1024, 1280 and 1440 pixels, check actual document and body widths,
  legible commands, aligned workflow steps and the full footer wordmark.
