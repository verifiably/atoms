---
id: atoms-88937b
title: Explain durability allowlist refusals with complete tuples and remedy guidance
status: todo
priority: 2
size: s
complexity: low
process: direct
created: 2026-10-09T23:58:47Z
updated: 2026-10-14T00:58:47Z
depends: []
tags: []
agent: claude-code/claude-fable-5-1
---

Why: The allowlist matches the complete VolumeConfiguration and StorageProfile, but both refusal paths print only filesystem_type, barrier_options and profile_id. Kernel, backend id/revision and ext4 feature masks can therefore differ while the printed values look identical. Make that refusal explainable and actionable.

Done:
- Both bind_project_volume and coordinator.lifecycle._verify_volume report all six observed VolumeConfiguration fields plus the declared storage profile.
- Show the supplied allowlist's certified tuples and their certification_ref values in deterministic order, identifying an empty allowlist explicitly. The user can compare observed and certified values without guessing a mount-option failure.
- Explain the remedy: use a configuration already covered by the supplied crash-certified allowlist, or obtain and review crash-certification evidence for the observed tuple. Point to docs/certification/README.md for the bundled allowlist's workflow.
- Extend that README to explain the exact tuple, including ext4 feature masks read through EXT4_IOC_GET_TUNE_SB_PARAM and normalized mount-option defaults. Identical findmnt output alone does not establish a match. Certification remains evidence for its recorded tuple, not blanket approval of another volume.
- Cover both refusal paths with focused tests using controlled configurations: identical displayed options with different kernel, backend revision or feature masks; empty and multi-entry supplied allowlists; matching tuples still admitted. Preserve refusal before new probe writes and existing survivor reclamation order.
- Verification: just test-one tests/test_fs_volume.py tests/test_fs_binding.py tests/test_lifecycle_commands.py, then just test-fast and just check.

Where to look: python/src/atoms/fs/volume.py (VolumeConfiguration, DurabilityAllowlist.match, build_configuration); python/src/atoms/fs/binding.py (bind_project_volume); python/src/atoms/coordinator/lifecycle.py (_verify_volume); python/tests/test_fs_volume.py (near-miss matrix); python/tests/test_fs_binding.py (refusal/reclamation checks); python/tests/test_lifecycle_commands.py; docs/certification/README.md; authority design §5.5.

Established at HEAD 250c320: exact equality of configuration and storage selects an entry; both refusal messages omit the same four configuration fields. The original volume's actual mismatch has not been reproduced and remains unknown. This task changes diagnostics and explanatory documentation; allowlist membership and durability admission remain governed by the existing contract.

Captured observation (preserved):
Initializing a world root on /mnt/ssd3 refused with CapabilityUnavailable: volume configuration is not on the supplied durability allowlist: ext4 ('async', 'barrier=1', 'commit=5', 'data=ordered') profile='flush-honoring-disk.v1'. findmnt shows the same options for the accepted volume (/mnt/ssd) and the refused one (rw,noatime,data=ordered), so the probe reads something findmnt does not print, and the message does not say which option fails or what would satisfy the profile. Observed 2026-10-09 while creating a disposable science world; worked around by using the other volume.
