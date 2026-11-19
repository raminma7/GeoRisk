# Test fixtures — NOT live data

These files are **small samples of real GDELT 2.0 data** captured on 2026-11-19
(subsets of the 09:30 and 09:45 UTC 15-minute export files), used only by the
automated tests so they run offline and deterministically. Rows were selected
to over-represent conflict/diplomatic CAMEO codes.

* `*.export.CSV.zip` — GDELT Events 2.0 export subsets (tab-separated, 61 columns)
* `lastupdate.txt` — format sample of `http://data.gdeltproject.org/gdeltv2/lastupdate.txt`
* `gdelt_doc_artlist.json` — format sample of a GDELT DOC 2.0 `mode=artlist` response
* `ucdp_ged_sample.csv` — rows from UCDP GED (format sample for connector tests)

Source: The GDELT Project (https://www.gdeltproject.org/), UCDP (https://ucdp.uu.se/).
The application itself never serves these fixtures; they are only loaded by tests.
