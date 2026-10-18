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
