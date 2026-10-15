# Ingestion — owner B1

Suggested modules (one PR each):

| Module | Job |
|---|---|
| `parse_pdf.py` | pdfplumber → text per page; tables kept as Markdown; Tesseract only for scanned pages |
| `structured_sources.py` | OBDex YAML, dtc-database SQLite, MechanicDB CSV → one text chunk per code / per fix, `page = null` |
| `metadata.py` | make, model, years, language from the file name (`marque_modele_annees_langue.pdf`); `system` and `section` by keywords, checked by hand on a sample |
| `chunk.py` | strategy A (recursive 500 tokens, overlap 50) and B (by heading or table row, max 600, heading prefixed) |
| `index.py` | BGE-M3 embeddings → Qdrant collection `car_manuals` (cosine, payload indexes on the filter fields) |

The payload fields are listed in `rag/README.md`. Keep `chunk_id` stable: the evaluation
queries refer to it.
