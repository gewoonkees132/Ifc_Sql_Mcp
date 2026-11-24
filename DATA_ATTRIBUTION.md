# Data attribution

## Duplex Apartment sample files

The sample building models used for development, documentation and the optional
`sample` tests are:

> BSI (2020) "Duplex Apartment Test Files," buildingSMART International.
> <https://github.com/buildingsmart-community/Community-Sample-Test-Files>

Licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

**This repository does not contain or redistribute these files** (one picture made from
them is the exception: see the explainer's picture below). They are downloaded on
demand by [`scripts/download_samples.py`](scripts/download_samples.py) from a pinned
upstream commit and verified byte-for-byte against the SHA-256 checksums below.

**Changes made:** none. The files are used exactly as published. Data derived from them
(database rows, query results and counts shown in the documentation) is produced by
running this project's code against the unmodified files.

### The demo on GitHub Pages

The static copy of the scripted Duplex demo, which `.github/workflows/pages.yml` builds
from `main` with `ifc-sql demo --export` and publishes on GitHub Pages, **does
redistribute data derived from the four IFC files** (not the files themselves):

- each model's 3D scene, **converted to glTF by IfcOpenShell triangulation, openings
  cut** from their walls, each element a node named by its GlobalId;
- the class, name and storey of each element and space;
- everything stored about one wall (`get_element`: its properties, quantities,
  materials and data quality findings);
- the results of the demo's nine questions, as the page lights them.

The page credits the work as the licence asks, with links to the source and to the
licence: "Duplex Apartment Test Files", buildingSMART International (2020), CC BY 4.0;
converted to glTF by IfcOpenShell triangulation, openings cut. Only the Duplex is
published: the audit's files below are never exported (`ifc-sql demo --export` refuses
their script).

### The explainer's picture

The page of `ifc-sql view` (and so the demo on GitHub Pages, and the wheel) **ships one
picture made from the four IFC files**: `src/ifc_sql_mcp/viewer/static/images/duplex.webp`,
a still of the Duplex behind the title of the "?" explainer. Changes made: the models
converted to glTF by IfcOpenShell triangulation, openings cut, drawn by the page with the
external walls of the demo's question 3 in the colours of their types, and cropped. It is
shared under CC BY 4.0, as the work it was made from; the explainer credits it beside the
picture ("Picture: Duplex Apartment, buildingSMART, CC BY 4.0", linked), and
`images/LICENSE` beside the file says the same. Its SHA-256 sum is in `images/SHA256SUMS`.

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

## Audit files: Revit 2019 to 2022 exports

The audit of modern Revit exports ([docs/audit/](docs/audit/2026-11-modern-revit-exports.md))
and the optional `modern` tests use five files from two public repositories:

- [ThatOpen/engine_web-ifc](https://github.com/ThatOpen/engine_web-ifc), licensed under the
  Mozilla Public License 2.0: files B and E.
- [youshengCode/IfcSampleFiles](https://github.com/youshengCode/IfcSampleFiles), which
  states no licence and describes itself as "a collection of IFC sample files for software
  test use": files A, C and D.

**This repository does not contain or redistribute these files.** They are downloaded on
demand by [`scripts/download_audit_samples.py`](scripts/download_audit_samples.py) from a
pinned upstream commit, verified against the SHA-256 checksums below, and used unmodified
for testing only.

| Key | File | Upstream commit | Bytes | SHA-256 |
|---|---|---|---:|---|
| `a` | `Ifc4_Revit_ARC.ifc` | IfcSampleFiles `7e69279` | 13,646,137 | `c4dd3612f126a0ca5a57f0e75d28f3e5da62751def85a452d849883c2892b518` |
| `b` | `R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc` | engine_web-ifc `d1962b4` | 72,882,778 | `08ab44c7a0e68efe6c1722c54235b405c0d649195254d92f964eaa47438aa62e` |
| `c` | `Ifc4_Revit_MEP.ifc` | IfcSampleFiles `7e69279` | 29,165,277 | `829c08715d56256e04d04fa7be40ec68928e0397631c3b846efd72d003f96495` |
| `d` | `Ifc4_Revit_ARC_FireRatingAdded.ifc` | IfcSampleFiles `7e69279` | 13,650,061 | `e015b8314d5bb64291a3ad9f7e3807beea5714469de28bb5d4c34aacd7bf4fbf` |
| `e` | `ARK_NUS_skolebygg.ifc` | engine_web-ifc `d1962b4` | 57,304,675 | `362f0f5e5ad35557014100a6c4de75ec166f6dbd9197933170dc1423f4af2a76` |

## Synthetic test fixture

The small IFC file under `tests/fixtures/` is generated from scratch by a checked-in
script using IfcOpenShell. It imitates the *conventions* of a Revit export (naming
patterns, property-set names, typical defects) but contains no data copied from the
Duplex files, and is covered by this repository's MIT licence.

## Software

IfcOpenShell is licensed under LGPL-3.0-or-later and is used as an unmodified dependency
installed from PyPI.
