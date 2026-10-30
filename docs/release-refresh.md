# Refreshing the 2.0.0 downloads

The original public `v2.0.0` tag stays intact. Refreshed native downloads can be built
from a reviewed, clean descendant commit using **Build release downloads** with
`tag=v2.0.0` and `source_commit=<full 40-character SHA>`. Omitting `source_commit`
still builds the original tagged source. A branch name, abbreviated SHA, unrelated
commit or modified source checkout is refused.

The workflow builds and tests both macOS architectures and the Windows x64
installer. It uploads candidate artifacts only. Review checks, installation/runtime
results and the candidate source before explicitly publishing assets.

Before replacing public assets, preserve the previous release notes and downloads.
Publish the new SHA-256 checksums and a build manifest recording the exact source
commit, original tag commit, workflow run and archive digests. Attach a source bundle
from that commit: GitHub's automatic source archives still represent the unchanged
original tag. Explain this distinction in the release notes.

The dashboard and native application remain **2.0.0**. PyPI's immutable archives use
the distribution revision **2.0.0.post1**; the Marketplace extension uses **2.0.1**.
Neither revision changes the application version or requires rebuilding the durable
ledger. Retain archived history and make a safety backup before local upgrades.

## Beta releases

A beta of the next version is tagged `v<version>-beta.<n>` (for example `v2.2.0-beta.1`) on `beta`,
built with **Build release downloads** using that tag, and published on GitHub as a **pre-release**,
never as the latest release. The files in the repository keep the plain version (`2.2.0`); the tag
carries the beta number, and the installer and DMG report it. Release notes inside the apps ignore
pre-releases, so a stable install is never offered a beta, and a beta build shows no release-notes
prompt. The stable `v<version>` tag is cut from `main` after `beta` is merged into it.

The editor extension's betas go to the Marketplace and Open VSX as **pre-releases** under their own
plain numbers, because both accept only `major.minor.patch` and a pre-release cannot reuse a number a
release will need: the 2.2.0 betas are extension **2.1.1**, 2.1.2, …, published with
`vsce publish --pre-release` and `ovsx publish --pre-release`, and the stable release is extension
**2.2.0**, which is higher, so beta testers move to it automatically. People opt in with "Switch to
Pre-Release Version". The bundled backend reports the app version (`pyproject.toml`), not the
extension's; `BUILD-INFO.json` records both.
