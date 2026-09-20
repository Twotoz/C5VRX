# PR flasher smoke test

This file exists only to exercise the same-repository pull-request firmware
publication path without changing C5VRX runtime behaviour.

Expected flow:

```text
open PR
  -> Architectural & DSP Verification
  -> ESP32-C5 production firmware build
  -> publish temporary GitHub prerelease pr-<PR_NUMBER>
  -> GitHub Pages web flasher discovers it through the Releases API
  -> PR Builds tab can flash c5vrx3_merged.bin or c5vrx3.bin
```

The PR should remain open while hardware flashing is tested. Closing the PR
must remove the temporary `pr-<PR_NUMBER>` prerelease/tag automatically.

Do not merge this smoke-test file into `main`.
