# Resume Parser Coverage, Layout Normalization, OCR Fallbacks & Skill Transferability

*Reference specification for the upgraded resume parser in `scripts/offline_ats_auditor.py` and the multi-format benchmark & deterministic fuzz suite in `scripts/parser_benchmark_suite.py` (`ops-req-parser-bench`).*

---

## 1. Multi-Format Loader, DOCX Header/Table/Textbox Extraction & PDF OCR Fallback Chain

Real-world resumes arrive in diverse file formats, multi-column table layouts, and scanned PDF containers. `extract_resume_with_ocr_fallback()` in `scripts/offline_ats_auditor.py` implements deterministic extraction and fallback diagnostics across all supported formats:

### 1.1 Word `.docx` Archive Inspection (`docx_xml_stdlib`)
Using Python's standard-library `zipfile` and `xml.etree.ElementTree`, the loader extracts:
1. **Word Headers (`word/header*.xml`):** Captures contact headers, candidate names, and titles placed inside Word page headers rather than the document body (`docx_header_footer_extracted: true`).
2. **Document Body Paragraphs & Text Boxes (`word/document.xml`):** Traverses both standard `<w:p>` runs and `<w:txbxContent>` drawing/shape text boxes (`docx_textboxes_extracted`).
3. **Multi-Column Word Tables (`<w:tbl>`):** Joins cells per row with `" | "` and passes them to the 2-column section de-interleaver (`docx_tables_extracted`).
4. **Word Footers (`word/footer*.xml`):** Captures certifications or page-footer credentials.

### 1.2 Four-Tier `.pdf` & Scanned-Document OCR Fallback Chain

```
                         ┌───────────────────────────┐
                         │   Input `.pdf` Resume     │
                         └─────────────┬─────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Tier 1: Native PDF Reader (`pypdf` / `PyMuPDF fitz` if installed)           │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (< 40 printable chars or library absent)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Tier 2: Zero-Dependency Stdlib PDF Stream Decoder (`_extract_pdf_streams_stdlib`)│
│  • Inflates `FlateDecode` streams via Python `zlib`                         │
│  • Parses PDF `BT ... ET`, `(...) Tj`, and `[...] TJ` text operators        │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (< 40 printable chars -> Scanned Image PDF)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Tier 3: Sidecar OCR Text Fallback (`<path>.ocr.txt` or `<stem>.ocr.txt`)    │
│  • Loads pre-extracted DMS/ATS OCR sidecar text (`RECOVERED_VIA_SIDECAR_OCR_TXT`)│
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (No sidecar file found)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Tier 4: Local Host `tesseract` CLI Binary (`shutil.which("tesseract")`)     │
│  • If unavailable, sets `ocr_fallback_status = "OCR_REQUIRED_SCANNED_PDF"`  │
│    and emits `[SCANNED_DOCUMENT_OCR_REQUIRED]` so scanned resumes are never │
│    silently mis-scored as unqualified candidates.                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Date Parsing, Overlapping-Role Tenure & Career-Gap Exclusion

### 2.1 Supported Date & Multilingual Formats (`DATE_RANGE_REGEX` & `_parse_date_token`)

| Format Family | Examples Supported | Normalized Representation |
| :--- | :--- | :--- |
| **Standard Numeric & ISO** | `01/2021 - Present`, `2021-01 - 2024-06`, `2023-01-15 through Present`, `03/15/2020 - 11/30/2022` | `datetime(YYYY, MM, 1)` |
| **English Month & Apostrophe Year** | `Jan 2020 – Dec 2023`, `September 2019 to Current`, `Jan '19 - Dec '20` | `datetime(YYYY, MM, 1)` |
| **Quarters & Seasons** | `Q1 2021 - Q4 2022` (`Q1`=Jan, `Q2`=Apr, `Q3`=Jul, `Q4`=Oct), `Spring 2020 ~ Fall 2022` | `datetime(YYYY, MM, 1)` |
| **Spanish (`es`)** | `Enero 2021 hasta Actualidad`, `Feb. 2019 a Dic. 2022`, `Actual`, `Presente`, `Hoy` | `datetime(YYYY, MM, 1)` |
| **French (`fr`)** | `Janvier 2021 à Présent`, `Févr. 2020 au Déc. 2023`, `Août 2019 - Actuel`, `Aujourd'hui` | `datetime(YYYY, MM, 1)` |
| **German (`de`)** | `Januar 2021 bis Heute`, `März 2020 bis Dezember 2020`, `Okt. 2018 - Aktuell`, `Laufend` | `datetime(YYYY, MM, 1)` |

### 2.2 Overlapping-Role Interval Union & Career-Gap Exclusion (`CAREER_GAP_HEADER_RE`)

- **Interval Union (`_merge_intervals`):** When a candidate holds concurrent overlapping roles (for example, a full-time WFM Analyst role `01/2020 - 12/2021` plus an overlapping part-time consulting engagement `06/2020 - 06/2021`), `parse_resume()` sorts and merges overlapping intervals before summing days (`total_days / 365.25`), recording both `gross_unmerged_tenure_years` and `overlapping_years_deduplicated`.
- **Non-Punitive Career-Gap Recognition (`is_career_gap`):** Role headers matching `CAREER_GAP_HEADER_RE` (`Career Break`, `Parental Leave`, `Maternity/Paternity Leave`, `Family Caregiving`, `Sabbatical`, `Medical Leave`, `Pausa Profesional`, `Congé Parental`, `Elternzeit`, `Sabbatjahr`) are stored in `ParsedResume.career_gaps`:
  - Their durations are **excluded** from work-experience tenure math (preventing artificial tenure inflation), and
  - They incur **zero** Component 5 hygiene deductions.

---

## 3. Multilingual Section Names & Two-Column Table De-Interleaving

- **Expanded Section Dictionary (`STANDARD_SECTION_HEADERS`):** Maps 80+ English, Spanish (`Resumen Profesional`, `Experiencia Laboral`, `Formación Académica`, `Habilidades Técnicas`), French (`Profil Professionnel`, `Expérience Professionnelle`, `Formation`, `Compétences Techniques`), and German (`Berufsprofil`, `Berufserfahrung`, `Ausbildung`, `Fachkenntnisse`) headers into the five canonical HR-XML zones (`summary`, `experience`, `education`, `certifications`, `skills`).
- **Two-Column Table De-Interleaving:** When a line contains pipe-delimited cells (`Work Experience | Senior WFM Lead — Pacific Ops (01/2021 - Present)`), `parse_resume()` checks whether the left cell is a recognized section header; if so, it switches `current_section` and appends the right cell's content directly into that section.

---

## 4. Distinguishing Transferred Skills from Competing Products

`classify_skill_transferability()` and `COMPETING_PRODUCT_FAMILIES` in `scripts/offline_ats_auditor.py` classify every requirement match into five deterministic categories:

| Transferability Class | Match Credit | Rule & Operational Meaning | Example |
| :--- | :-: | :--- | :--- |
| `EXACT_OR_JD_ALTERNATIVE` | **`1.0x`** | Resume contains the exact requisition term or an alternative explicitly offered by the requisition (`"NICE, Verint, or Genesys"`). | JD: `Verint` (or `NICE IEX`) $\leftarrow$ Resume: `NICE IEX` |
| `GENERIC_CATEGORY_SATISFIED_BY_SPECIFIC_TOOL` | **`1.0x`** | JD asks for a generic tool category (`"WFM Platform"`, `"BI Tool"`, `"Cloud Data Warehouse"`, `"ERP System"`), and the candidate holds any specific platform in that family. | JD: `WFM Platform` $\leftarrow$ Resume: `NICE IEX` |
| `TRANSFERRED_METHODOLOGY_SYNONYM` | **`0.5x`** | Same underlying operational capability or methodology worded differently; eligible for Tier 1 wording alignment. | JD: `Workforce Capacity Modeling` $\leftarrow$ Resume: `capacity planning` |
| `COMPETING_PRODUCT_NO_CREDIT` | **`0.0x`** | JD requires a specific proprietary product without listing alternatives, and the candidate holds a **competing product** in the same ecosystem. Earns **`0.0x` scoring credit** (never swapped), while populating `adjacent_competing_product` so human reviewers see the transferable ecosystem experience. | JD: `Verint` $\leftarrow$ Resume: `NICE IEX` (`adjacent_competing_product="NICE IEX"`) |
| `UNVERIFIED_TRUE_GAP` | **`0.0x`** | Neither the required skill nor any competing product in its tool family is present on the resume. | JD: `Kubernetes` $\leftarrow$ Resume: *(not mentioned)* |

---

## 5. CLI Usage (`ops-req-parser-bench`)

```bash
# 1. Run the 11-case multi-format/multilingual corpus benchmark + 100-iteration deterministic fuzz suite
ops-req-parser-bench --demo

# 2. Export the benchmark corpus JSON and generate Markdown, JSON, and CSV benchmark reports
ops-req-parser-bench --corpus examples/sample_resume_parser_corpus.json \
  --fuzz-iterations 200 --output parser_benchmark.md --csv-out parser_benchmark.csv
```
