# AgentTelemetry landing page

This directory is a standalone, static marketing page for the AgentTelemetry 2.1.0 app
and the 2.1.0 desktop editor extension. It does not start or connect to the dashboard
and contains no personal usage records. Dashboard figures shown in the interactive
preview are illustrative sample data written directly in the page.

## Preview locally

From the repository root, run:

```bash
python3 -m http.server 8788 --bind 127.0.0.1 --directory website
```

Then open <http://127.0.0.1:8788>. The page has no build step, package install, runtime
dependencies, analytics, API calls, or remote fonts, scripts, or images. Its JavaScript
handles the mobile menu, sample-dashboard views and window controls, command copying, and
scroll reveals, sample-number entrances, animated disclosures and optional ambient
motion. All page assets are served from this directory. CSS and JavaScript URLs carry
content versions so browser caches do not hide preview updates.

## Vercel hosting

The public site is https://agenttelemetry-app.vercel.app/.
The production project is **agenttelemetry** in **Uttam Deb's projects**. Deploy this
`website/` directory as the project root, with the Other framework preset, no install
or build command, and `.` as the output directory. `vercel.json` keeps these static
settings with the source. Keep API tokens outside the repository and deployment files.

## Hosting and downloads

The files are ready to host as a static site. Download links point to the AgentTelemetry
v2.1.0 release: macOS 13+ Apple Silicon and Intel DMGs, and a Windows
10/11 x64 installer. Those downloads are unsigned/unnotarized; see the repository README
for installation notes and checksums. The hero install chooser works without JavaScript
and links to those assets, the editor card, and the Linux/source alternative. Linux uses
the browser dashboard; a native tray app is available for macOS and Windows. The editor
card links to the [Visual Studio Marketplace extension](https://marketplace.visualstudio.com/items?itemName=agenttelemetry-team.agenttelemetry),
the [Open VSX extension](https://open-vsx.org/extension/agenttelemetry-team/agenttelemetry),
and the [universal 2.1.0 VSIX](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.1.0/agenttelemetry-2.1.0.vsix)
with its desktop editor install guide. It supports desktop VS Code 1.85+ and compatible
desktop forks. Starting local monitoring from the editor needs Python 3.9+ or a supported
installed AgentTelemetry runtime; the extension does not bundle or install Python or
start monitoring automatically. The dashboard/native app version remains 2.1.0;
the immutable PyPI distribution revision is [agenttelemetry-app 2.1.0](https://pypi.org/project/agenttelemetry-app/2.1.0/).
The source card and FAQ lead with `pip install agenttelemetry-app`, followed by the
`agenttelemetry` launch step; `uvx agenttelemetry-app` is a secondary disclosure. Both
source commands have copy controls.

## Product limits and privacy

The dashboard runs locally and reads the coding-tool logs already on that computer. Cost
figures are estimates at API list prices; subscription users do not pay per token, and
some source usage is unavailable in the logs. Optional device sharing is opt-in over a
local network. Optional account snapshots make a provider lookup only after the user
confirms it. The stdio MCP bridge is off by default, requires a running dashboard, and
returns requested analytics to the configured client after the user enables access in
Settings. A cloud-backed client may forward those results to its provider. Project/device
display names and connected-device usage have separate opt-ins; MCP results exclude
prompts, session titles, native IDs, paths, credentials, and account identities. The editor extension
shows recorded usage and does not start monitoring on installation or opening. The
marketing preview does none of these things and never reads a real ledger.

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

## Design references

`glass.css` owns the material layer: a mint/cyan light field behind translucent
surfaces, background frost, reflected highlights and rim lighting. The floating
navigation remains available while scrolling; dense chart and command content keeps
its contrast layers. The install chooser uses the browser's top layer when available
so its frost samples the actual page, with viewport-aware placement and a native
`details` fallback. Reduced-transparency and increased-contrast preferences use solid
surfaces; reduced-motion remains supported independently.

This material pass follows [NN/G's glassmorphism guidance](https://www.nngroup.com/articles/glassmorphism/)
and [Apple's material principles](https://developer.apple.com/videos/play/wwdc2025/219/).
The homepage and Work page of [uttamdeb.com](https://uttamdeb.com/) were inspected
for the relationship between its coloured environment, translucent navigation and
floating foreground cards.

The visual direction draws on [Diponkar](https://diponkar.com/) for bold typography and
beveled glass controls, [uttamdeb.com](https://uttamdeb.com/) for editorial serif/sans
contrast and layered glass depth, and [Codeburn](https://codeburn.app/) for product-led
storytelling and clear download choices. [evasanchez.info](https://evasanchez.info/) was
also checked, but access stopped at a robot challenge, so this page makes no claim about
its full design.

## Manual QA checklist

- Preview with the command above; confirm the page works without a build step and the
  sample dashboard is clearly labeled as illustrative.
- At a narrow viewport, open and close the navigation by button, section link, and
  Escape; confirm the button label and `aria-expanded` stay in sync and Escape returns
  focus to the button. Check that desktop navigation stays visible after resizing.
- Use all eight sample views from the desktop sidebar and all nine choices in the mobile
  selector; confirm the selected view changes and every panel remains keyboard reachable.
- Use the Overview, Sessions, and Devices tabs by click, arrow keys, Home, and End; confirm
  only the selected panel is shown and tab selection stays synchronized with the sidebar
  and mobile selector.
- Test preview minimize/restore, expand/collapse with Escape, and reset to Overview; check
  labels and pressed/expanded states stay synchronized.
- Open the hero install chooser and verify all three native downloads, their platform and
  file-type labels, the editor link, and the Linux/source link. Follow the editor link;
  check the Marketplace target, universal VSIX download, desktop editor guide, and the
  separate app/extension version labels.
- On a fresh page load, follow both hero links to the editor and MCP cards; their
  headings must settle below the fixed navigation at desktop and mobile widths.
- Check the MCP setup card and guide link. Confirm the off-by-default state, running
  dashboard requirement, consent path, separate name/device opt-ins, and cloud-client
  forwarding warning appear near the setup entry point and in the privacy section/FAQ.
- Follow the source link and check the visible pip command and launch step, copy status,
  and expandable uvx alternative.
- Try command copying in a secure browser context, then with clipboard access unavailable;
  verify success is announced only after copying and the fallback selects the command.
- Check download links, text contrast, and both regular and reduced-motion settings.
  With JavaScript disabled, verify the navigation, sample panels, native install chooser,
  source command and uvx disclosure, and page content remain readable. The preview
  should not contact the dashboard or send analytics requests.
- Check normal-motion entrances, sample counters, chart drawing, sliding tab selection,
  accordion opening/closing and button feedback. Rapidly toggle a disclosure and change
  reduced-motion preference while it animates; final native open/closed state must agree
  with its visible content. Ambient hero motion pauses off screen and all motion stops
  under reduced motion. `motion.js` owns reveals; `site.js` only enhances controls.
