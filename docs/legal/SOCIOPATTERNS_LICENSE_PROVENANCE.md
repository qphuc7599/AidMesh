# SocioPatterns dataset provenance and license memo

Accessed: 2026-07-13  
This is a factual provenance memo, not legal advice.

## Exact source used

- Zenodo record: https://zenodo.org/records/2540795
- DOI: https://doi.org/10.5281/zenodo.2540795
- Record title: “SocioPatterns datasets”
- Publisher/resource type: Zenodo / Dataset
- Record-level license displayed on access: Creative Commons
  Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)
- Legal code: https://creativecommons.org/licenses/by-nc/4.0/legalcode
- Downloaded archive context: `contact.tar.bz2`; transformed source files
  `tij_InVS13.dat`, `tij_LH10.dat`, and `tij_SFHH.dat`.
- Frozen source hashes are recorded in each
  `external/sociopatterns/normalized_slots/*/provenance.json`; for example InVS13 is
  `4b1c0c4065766e89106bdfca6687488bc38dd38ef48f42a0efa326144ecd0b2b`.

## Terms applied to this artifact

The exact acquisition record labels the archive CC BY-NC 4.0. Therefore this project
uses the conservative record-level terms: copy/adapt only for non-commercial
purposes, give appropriate credit, link the license, and indicate transformations.
The data must not be represented as Apache-2.0 merely because AidMesh source code is
Apache-2.0. Keep data and derived traces segregated with their own notice.

The SocioPatterns source pages provide additional context and are not silently used
to weaken the Zenodo record terms:

- InVS13 workplace:
  https://sociopatterns.org/datasets/contacts-in-a-workplace/ — page states Creative
  Commons Public Domain Dedication and requests citation to M. Génois et al.,
  *Network Science* 3, 326 (2015).
- SFHH:
  https://sociopatterns.org/datasets/sfhh-conference-data-set/ — page states Creative
  Commons Public Domain Dedication and requests citation to M. Génois and A. Barrat,
  *EPJ Data Science* 7, 11 (2018).
- LH10 hospital face-to-face page:
  https://sociopatterns.org/datasets/hospital-ward-dynamic-contact-network/ — page
  states a CC Attribution-NonCommercial-ShareAlike license and requests citation to
  *PLoS ONE* 8(9), e73970 (2013), acknowledgment of SocioPatterns, and a link.
- The co-location collection page:
  https://sociopatterns.org/datasets/co-location-data-for-several-sociopatterns-data-sets/
  states a public-domain dedication for that collection and requests the Génois and
  Barrat 2018 citation.

These pages describe overlapping collections with different notices. Because the
workspace files came from Zenodo 2540795, this memo does **not** infer that a more
permissive source-page notice overrides the exact record-level CC BY-NC 4.0 label.

## Required attribution and transformation notice

At minimum cite Zenodo DOI 10.5281/zenodo.2540795, the applicable source publication,
SocioPatterns, the record URL, and CC BY-NC 4.0. State that AidMesh:

1. preserved 20-second contact slots;
2. deterministically ranked anonymous nodes by observed contact duration;
3. assigned synthetic site/relay/client roles;
4. generated synthetic bundles, risk scores, bandwidth, and TTL parameters; and
5. did not infer identity, demographic traits, need, or humanitarian behavior.

## Redistribution decision

- AidMesh code/configuration: Apache-2.0.
- Original/normalized SocioPatterns data: separately licensed; do not relicense.
- Non-commercial research artifact redistribution is compatible only if attribution,
  license notice, transformation notice, and no-extra-restrictions requirements are
  met. Commercial use is not authorized by CC BY-NC 4.0.
- Before public archival release, include a data-level LICENSE/NOTICE beside the
  traces and verify whether the archive should be redistributed or fetched by DOI.
  The lowest-risk option is scripts + hashes + DOI, with users downloading the exact
  archive from Zenodo.
- If commercial, partner, or field use is contemplated, obtain written clarification
  from the rightsholder/Zenodo depositor; ambiguity is not permission.
