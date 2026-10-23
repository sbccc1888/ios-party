# Source and licensing

The root BSD-2-Clause-Patent license applies to original ios-party contributions
that do not carry a different license. It does not relicense third-party code,
patches to third-party software, or files obtained by the build scripts.
Existing copyright notices, SPDX identifiers and component licenses take
precedence.

## Upstream components

- Apple XNU and patches derived from it retain the Apple Public Source License
  2.0. XNU is fetched from apple-oss-distributions/xnu at the revisions recorded
  in `ci/sources.json` and `research/hackios-reuse/sources.lock.json`.
- HackiOS kernel, loader and associated source are fetched from
  `oosexgang/hackios-xnu-kernel`; that upstream specifies APSL 2.0.
  Source overlays and modifications to those files retain the upstream terms.
- HackiOS userland is fetched from `oosexgang/hackios-xnu-userland`, which
  specifies MIT for its own code. Its third-party dependencies retain their
  respective licenses.
- UniXNU code retains its upstream notices. `drivers/ArmCpu/LICENSE` supplies
  the BSD-2-Clause-Patent notice for the included CPU driver.
- `drivers/ChironUfsReadonly/LICENSE` supplies the separate BSD-2-Clause notice
  and attribution for that component.
- EDK II, edk2-msm8998, Mu and any vendor firmware fetched during a build retain
  their upstream licenses and notices. Making this source repository public
  does not grant additional redistribution rights to vendor binaries.

## System files and private captures

Apple IPSW, DMG, APFS system volumes, extracted Apple programs and shared caches
are not part of this source release. Build recipes may obtain required files
from Apple's servers; each builder is responsible for their use.
The public workflows do not publish the Apple system-volume artifact.

Local device captures, credentials, serial numbers and downloaded system images
are excluded from Git. Generated binary artifacts have their own component
notices and are not a replacement for the corresponding source or licenses.
