# What is pulled

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 1. What is pulled

| Source | What | Interface | Kept in |
|---|---|---|---|
| Cohort list | The projects to cover; kept by people, never written by code | `inputs/config/cohort.csv` | none |
| Appraisal documents | Every Project Appraisal Document (PAD) and Project Paper (additional financing or restructuring) disclosed per project, as PDF | World Bank Documents and Reports interface | `data/raw/pdf/` |
| Procurement plans | Every plan disclosed per project, as the Bank's text rendition (plans are not read from PDF) | Documents and Reports, type "Procurement Plan" | `data/raw/plans/` |
| Procurement notices | Every notice per project | Procurement notices interface | `data/raw/notices/` |
| Contract awards | Every signed contract per project | Contract awards interface | `data/raw/awards/` |
| Project records | Name, status, approval and closing dates, region, country, instrument, commitment, Global Practice, top three sectors | Projects interface, versions 2 and 3, only the fields listed in `src/fetch_projects.py` | `data/raw/projects/` |

Nothing else is pulled. The Bank's text renditions of appraisal documents are not fetched; the PDF is read.

**How a fetch behaves**

- **Paged listings.** One helper, `fetch_procurement.fetch_pages`, serves plan listings, notices, awards and the cohort listing (`src/cohort_build.py`). Each page must be JSON, not an HTML page, and is logged. A list shorter than the interface's own total stops the run, so a truncated list is never written. A response key missing from every record of a non-empty page means the interface changed, and also stops the run.
- **Retries.** Backoff on connection errors and on the status codes in `fetch.RETRY` (403, 408, 429, 500 to 599). Any other status, 404 included, is never retried. Redirects are followed in code, with `http://` raised to `https://`.
- **A PDF counts** only with a `%PDF` header and `%%EOF` in its last 2 KB. Anything else (a throttled interface sends an HTML page with status 200; a cut connection truncates) is a failed fetch, noted in the report and retried next run. An empty or HTML plan rendition, notice or award response is refused likewise.
- **Writing.** Each file is written under a temporary name unique to process and thread, then renamed.
- **`fetched_at`** is the download time, kept while the bytes are unchanged; it is never the file's modification time.
- **Nothing is deleted or overwritten.** A file on disk is not downloaded again: an appraisal PDF never (a re-disclosed PDF under the same id is not seen); a plan rendition only with `fetch_procurement.py --refresh`, which keeps the old file beside the new, named by checksum; notices, awards and project records with `--update`. A listing that no longer names a document whose PDF is on disk does not remove it (`kept_not_relisted` in the fetch report).
- **Logs.** `data/raw/fetch_log.csv` records every procurement listing, notice and award request; `data/reports/fetch_status/` what the appraisal and project fetches fully read.

**Personal data.** Contact details in notices and staff names in award records are dropped before storage (`fetch_procurement.PERSONAL`). The project team's names and e-mail addresses are not requested. A document's `owner` (the unit that owns it) is kept as `owner_unit`.

### 1.1 The projects table (`src/fetch_projects.py`, `src/projects.py`)

1. **Two forms.** Both `api/v2/projects` and `api/v3/projects` are asked. Version 3 holds records version 2 lacks and is more current (revised closing date after an extension; commitment after cancellations), so it gives the name, status, dates, region, country and commitment (US$, stored in millions). Version 2 gives the fiscal year, instrument, Global Practice and sectors. A version 3-only record has its fiscal year computed from the approval date (1 July to 30 June, named by the year it ends) and those fields blank. `record_source` is `v2`, `v3` or `v2+v3`. A project in neither form is stored empty and reported (`in_projects_interface` false; `(record pending: <id>)` in the views); one whose request failed has no file and is requested again next run.
2. **Commitment.** The forms can disagree either way, so it is a figure to read with its source, not a total to add up. Both are kept (`commitment_v2_usd_m`, `commitment_v3_usd_m`).
3. **Refresh.** An open project's record is fetched again after 7 days (`wbg.PROJECT_REFRESH_DAYS`); a Closed project's is not. `./wbg run --refresh-projects` refetches all selected records. A changed record keeps its old copy beside the new.
4. **Rows** in `data/projects/projects.csv`: `project_name`, `status` (Active, Closed, Pipeline), `approval_date`, `approval_fy`, `closing_date`, `region`, `country_code`, `country_name`, `lending_instrument`, `commitment_usd_m`, `practice`, `practice_code`, `sectors` (top three with shares), `in_projects_interface`; and from the appraisal documents `managing_unit`, the owner on the project's most recent document that records one (the owner is usually a practice unit, sometimes a country or regional office, and blank on most recent papers), printed as recorded, with `unit_doc_id`, `unit_as_of` and `unit_code` (the code inside it: "Digital Dev - AFR EAST/SOUTH (IDD04)" gives IDD04).
5. **Not there.** Neither interface publishes the vice presidency, nor the countries a regional operation covers: its country is the region (`3E`, `3W`, `6R`, `7C`, `3T`), as are its notices'. The views mark it `is_regional` with no map code (section 5f).
