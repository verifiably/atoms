# Filesystem expansion — brainstorming brief

**Status:** Exploratory handoff for a future brainstorming session. This is
not an approved design, implementation plan, or authorization to format or
move data.

## Why revisit the filesystem boundary

Ext4 is a good reference substrate for Atoms: it is simple, mature, and the
current crash-certification and path-resolution proofs are built around its
observable semantics. A second filesystem would be useful primarily as an
independent proof environment, not as a claim that ext4 was the wrong choice.
Passing the crash matrix on both a journaled filesystem and a copy-on-write
filesystem would test more of Atoms' assumptions than another ext4 tuple.

There is also a scientific-data benefit. Ext4 protects filesystem metadata,
but does not generally checksum file contents. A checksumming filesystem can
detect latent corruption during reads or scrubs; with redundant copies it can
also repair it.

## Current Atoms boundary

- `atoms.fs.volume` already normalizes durability-relevant mount options for
  ext4, XFS, and Btrfs.
- The path resolver deliberately admits only non-casefold ext4. XFS and Btrfs
  remain refused until Atoms can prove their filename lookup semantics rather
  than infer them from ext4 ioctls.
- The certification driver is explicitly ext4-only: image construction,
  feature identity, record naming, and guest formatting all assume ext4.
- Therefore another filesystem requires a designed support slice. Adding an
  allowlist row or formatting a partition is insufficient.

## Candidate order

### 1. Btrfs — recommended first experiment

Btrfs is the strongest complement to ext4 for Atoms and Beliefs:

- data and metadata checksums by default;
- online scrub, with repair where a replicated profile supplies a good copy;
- copy-on-write transaction behavior, materially different from ext4's
  journaled updates;
- cheap snapshots and reflinks for experiment forks;
- transparent compression for suitable datasets.

Begin with a single-device test volume or RAID1/RAID1C3 where repair is a
goal. Do not include RAID5/6 in an initial support claim; upstream continues
to classify RAID56 as unstable.

### 2. XFS — performance and implementation diversity

XFS is attractive for large files, parallel I/O, metadata checksums, reflinks,
and online scrub. It would add a mature, independently implemented journaling
filesystem to the matrix. It adds less than Btrfs for scientific-data
integrity because it does not generally checksum file contents, so it is the
better second expansion unless workload measurements make scalability the
dominant goal.

### 3. OpenZFS — archive candidate, not first Atoms target

OpenZFS offers end-to-end checksums, snapshots, replication, scrubbing, and
self-healing when the pool has redundancy. It is compelling for a mirrored
long-term scientific archive. On Linux it is an out-of-tree module with an
explicit supported-kernel range, which adds upgrade friction to Atoms' already
exact kernel certification. Supporting it would also require new volume
observation and certification machinery rather than extending the existing
Linux ext4/XFS/Btrfs seams.

## Storage cautions

- A snapshot is not a backup: it shares the underlying storage with its
  source.
- A second partition on the same device does not protect against device,
  controller, or firmware failure.
- Checksums without another valid copy detect corruption but cannot repair it.
- Certification itself needs little capacity. Start with roughly 128–256 GiB
  on a separate physical device; allocate 0.5–1 TiB only if real datasets will
  remain there.
- Keep ext4 authoritative until the alternative independently clears lookup
  proof, crash certification, and the full Atoms acceptance surface.

## Questions for the future brainstorming session

1. Is the first goal stronger data integrity, filesystem implementation
   diversity, workload performance, or portability?
2. What observable evidence proves exact-byte filename lookup for the chosen
   filesystem and rejects casefolding configurations?
3. Which on-disk features, mount options, storage profiles, and redundancy
   layouts form the certified configuration identity?
4. How should image creation and guest recovery become filesystem-specific
   without weakening the current exact ext4 record?
5. Which Btrfs profiles and options are admitted initially, and which remain
   explicit refusals?
6. What staged evidence is required before an experimental volume can hold an
   authoritative Atoms root?
7. Should archival storage remain a separate concern from the filesystem that
   hosts active Atoms transactions?

## Starting references

Local authority:

- [`python/src/atoms/fs/volume.py`](../python/src/atoms/fs/volume.py) —
  configuration identity and mount-option tables.
- [`docs/plans/2026-07-30-a4b1-path-resolution-design.md`](plans/2026-07-30-a4b1-path-resolution-design.md)
  — current lookup-semantics refusal and the XFS/Btrfs deferral.
- [`python/tools/certify/__main__.py`](../python/tools/certify/__main__.py) —
  ext4-only certification composition.

Upstream references:

- [Ext4 journal and data modes](https://docs.kernel.org/filesystems/ext4/journal.html)
- [Btrfs checksumming](https://btrfs.readthedocs.io/en/stable/Checksumming.html)
- [Btrfs scrub](https://btrfs.readthedocs.io/en/latest/btrfs-scrub.html)
- [Btrfs feature status](https://btrfs.readthedocs.io/en/latest/Status.html)
- [Btrfs subvolumes and snapshots](https://btrfs.readthedocs.io/en/latest/Subvolumes.html)
- [XFS administration](https://docs.kernel.org/admin-guide/xfs.html)
- [XFS online scrub design](https://docs.kernel.org/filesystems/xfs/xfs-online-fsck-design.html)
- [OpenZFS checksums](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Data%20Storage/Checksums.html)
- [OpenZFS scrub and self-healing](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Operations/Scrub%20and%20Resilver.html)
- [OpenZFS Linux kernel support](https://openzfs.github.io/openzfs-docs/Project%20and%20Community/FAQ.html)
