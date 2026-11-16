# Data attribution

## Duplex Apartment sample files

The sample building models used for development, documentation and the optional
`sample` tests are:

> BSI (2020) "Duplex Apartment Test Files," buildingSMART International.
> <https://github.com/buildingsmart-community/Community-Sample-Test-Files>

Licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

**This repository does not contain or redistribute these files.** They are downloaded on
demand by [`scripts/download_samples.py`](scripts/download_samples.py) from a pinned
upstream commit and verified byte-for-byte against the SHA-256 checksums below.

**Changes made:** none. The files are used exactly as published. Data derived from them
(database rows, query results and counts shown in the documentation) is produced by
running this project's code against the unmodified files.

### Pinned files

Upstream commit `31eb1dd0a0406fb9ebf0ebd823b48e1ec95afc08`, folder
`IFC 2.3.0.1 (IFC 2x3)/Duplex Apartment/`.

| Key     | File                               | Bytes      | SHA-256                                                            |
|---------|------------------------------------|-----------:|--------------------------------------------------------------------|
| `arc`   | `Duplex_A_20110907.ifc`            |  2,380,763 | `b347a2c8aa8fff6db896a4417a9c50c22ac0ccd7c5cfc22b99b8d29336c606ed` |
| `mep`   | `Duplex_MEP_20110907.ifc`          | 17,871,432 | `13976a8e223f177a6d7123679e4b02e750cd90c13f7bb20c593be125d9407119` |
| `ele`   | `Duplex_Electrical_20121207.ifc`   |  1,602,758 | `d47b014ed5b3428496683ee75e4664013f6df8237ea571b8d13f877cea0b7a94` |
| `plu`   | `Duplex_Plumbing_20121113.ifc`     | 31,556,138 | `abfaf5c0979b6b4b05182aec7ada8945374bdb414db76c763724d549b41b212b` |
| `cobie` | `2012-03-23-Duplex-Handover.xlsx`  |    233,186 | `26cdaf0bbf34b67f0f68c78de5bc3c1c3a285df97223c2704c9175b1936fdd4a` |

The `arc` checksum was supplied with the project brief; the other four were computed when
the files were first downloaded for this repository (2026-11-16).

## Synthetic test fixture

The small IFC file under `tests/fixtures/` is generated from scratch by a checked-in
script using IfcOpenShell. It imitates the *conventions* of a Revit export (naming
patterns, property-set names, typical defects) but contains no data copied from the
Duplex files, and is covered by this repository's MIT licence.

## Software

IfcOpenShell is licensed under LGPL-3.0-or-later and is used as an unmodified dependency
installed from PyPI.
