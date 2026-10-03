# RAG Search Tool (`tools/rag_tool.py`)

## How it works
`build_index()` reads every PDF in `KB_DIR` page by page with pypdf, cuts each page into clause-aware overlapping chunks
(plus one whole-page chunk), and stores them in ChromaDB (`.rag_store/`) with `source` and `page` metadata.
`sop_search(query)` embeds the query, returns the top 4 closest chunks, and formats each as `[<file>, page <n>]` + text.
The Coordinator cites those headers in its final report.

## Final settings
| Setting | Value | Why |
|---|---|---|
| CHUNK_SIZE | 600 | 400 also scored 10/10 but risks splitting a rule; 900 added ~30% more text with no accuracy gain |
| OVERLAP | 120 | 80 and 200 both scored 10/10; 120 keeps rules whole without bloating output |
| TOP_K | 4 | 3 and 5 both scored 10/10; 5 made prompts ~28% longer for no gain |

Embedding model: ChromaDB default (ONNX all-MiniLM-L6-v2), ~80 MB, downloaded on first use (internet needed once).
Note: this model reads only ~256 tokens (~1,000 characters) per chunk, so the whole-page chunk is embedded from
its start only. It helps recall but is not a substitute for good clause chunks.

## Tuning results (one setting changed at a time)
| CHUNK_SIZE | OVERLAP | TOP_K | Pass rate | Avg output (chars) |
|---|---|---|---|---|
| 600 | 120 | 4 | 10/10 | 2998 |
| 400 | 120 | 4 | 10/10 | 2437 |
| 900 | 120 | 4 | 10/10 | 3947 |
| 600 | 80 | 4 | 10/10 | 2613 |
| 600 | 200 | 4 | 10/10 | 3135 |
| 600 | 120 | 3 | 10/10 | 2199 |
| 600 | 120 | 5 | 10/10 | 3839 |

**Final accuracy:** 10/10 questions return the correct page (12/12 pytest tests pass).
The index is small (20 chunks from 2 PDFs), so retrieval is robust to these settings; the main effect of
CHUNK_SIZE and TOP_K is prompt length, not accuracy.

## ID-heavy queries
Embeddings capture meaning, not exact tokens, so bare IDs like `P-1001`, `3.2`, `VC-4.1` match poorly.
Test queries therefore pair a natural question with the clause ID and key terms
(e.g. "...days of cover delay + 5 (clause 3.2)"), which is how the Coordinator is told to ask.
Not measured: no before/after comparison was run. The test phrasing follows how the Coordinator is prompted to ask; it is a design choice, not a tested improvement.

## Stale index
The collection name is `sops_<hash>`, where the hash covers the **content bytes** of every PDF plus the chunk settings
(not mtime, which is unreliable on Windows and under git checkouts). Any change creates a new collection and old
`sops_*` collections are deleted automatically. To force a rebuild: delete `.rag_store/` or call `build_index(force=True)`.