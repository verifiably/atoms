---
id: atoms-88937b
title: Durability allowlist refusal names the volume's options but not the remedy
status: idea
priority: 2
created: 2026-10-09T23:58:47Z
updated: 2026-10-09T23:58:47Z
depends: []
tags: []
agent: claude-code/claude-fable-5-1
---

Initializing a world root on /mnt/ssd3 refused with CapabilityUnavailable: volume configuration is not on the supplied durability allowlist: ext4 ('async', 'barrier=1', 'commit=5', 'data=ordered') profile='flush-honoring-disk.v1'. findmnt shows the same options for the accepted volume (/mnt/ssd) and the refused one (rw,noatime,data=ordered), so the probe reads something findmnt does not print, and the message does not say which option fails or what would satisfy the profile. Observed 2026-10-09 while creating a disposable science world; worked around by using the other volume.
