# External contact data

Contact datasets are not redistributed. Obtain the exact inputs below under the
provider's terms, preserve their hashes and follow the preprocessing scripts.
Generated outcomes and retained normalization provenance are included.

| Family | Source | Context |
| --- | --- | --- |
| Asturies wildfires | [Author repository](https://github.com/sergiocabrero/asturies-wildfires), revision `6180235cfd63dbd78fdd4cb5eda9cc9e97ac21fe` | 23 firefighter incident clusters with synthetic request/site overlays |
| SocioPatterns | [Record 2540795](https://zenodo.org/records/2540795) | InVS13 workplace, LH10 hospital and SFHH conference, 20-second contacts |
| UMass DieselNet | [UMass repository](https://traces.cs.umass.edu/docs/traces/network/), `UMassDieselNet_Spring2005.tar.gz` | January-May 2005; do not substitute an unverified CRAWDAD release |
| ENE5AI | [Dataset DOI 10.57745/DFMCYS](https://doi.org/10.57745/DFMCYS), release 1.0; file DOI `10.57745/ISKJN8` | Fixed recorded external-validity context |

Acquisition code includes `scripts/prepare-asturies-wildfires.py`,
`aidmesh/experiments/trace_preprocess.py` and the CLI trace-preprocessing commands.
`external/**/provenance.json` contains retained provenance, not contact tables.
The dataset mapping is also recorded in the scientific notes and trace registries.

`raw/confirmatory/` contains historical synthetic simulator events needed to
inspect old/current-source drift. These are not external contact datasets and
are not fresh holdout observations.
