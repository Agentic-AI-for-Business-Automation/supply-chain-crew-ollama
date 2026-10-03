"""RAG over company SOP and contract PDFs, with page-level citations."""
import glob, hashlib, os, re
from datetime import datetime
from pathlib import Path

from crewai.tools import tool
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent          # repo root, so cwd does not matter


def _resolve(p: str) -> str:
    return p if os.path.isabs(p) else str(ROOT / p)


KB_DIR = _resolve(os.getenv("KB_DIR", "knowledge_base"))
RAG_DIR = _resolve(".rag_store")
CHUNK_SIZE, OVERLAP, TOP_K = 600, 120, 4
_collection = None
MAX_BEST = float(os.getenv("RAG_MAX_BEST", "1.6"))        # measured: relevant best <=1.35, off-topic best >=1.85
REL_MARGIN = float(os.getenv("RAG_REL_MARGIN", "0.08"))   # keep only hits close to the best one
CHUNKER_VERSION = "4-jsonl"                                      # bump when chunking changes: forces a re-index
RETRIEVED: set = set()
RETRIEVED_CLAUSES: set = set()   # (document, page, accepted spelling of the clause), lower-case, for clause-level citation checks
_RESULT_SETS: dict = {}          # result set -> times returned this session (loop detector)
MAX_REPEAT = 2                   # the same result set may be returned this many times, then the agent is told to move on
CLAUSES_FILE = "clauses.jsonl"   # structured records emitted by knowledge_base/source/build_pdfs.py      # (document, page) pairs actually returned; trigger_n8n only accepts these as citations


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


_LABEL = re.compile(r"^(Section \d+|Schedule [AB]|VC-\d+\.\d+|\d+\.\d+)")
_IDS = re.compile(r"\b(VC-\d\.\d|\d\.\d|Schedule [AB]|Section \d)\b", re.I)      # clause ids: strong boost
_ENT = re.compile(r"\b(S\d{3}|P-\d{4})\b", re.I)                                # supplier/part ids: weak boost


# Clause / section headers that make good split points
_HEADER = re.compile(r"(?m)(?=^(?:Section \d|Schedule [AB]|VC-\d+\.\d+|\d+\.\d+)\b)")


def _chunks(text: str):
    """Clause-atomic chunks: a clause is never cut, never loses its number, and there is no character
    overlap (a trimmed overlap used to strip the clause id from the head of the next chunk).
    An oversized clause is windowed and every continuation is re-labelled with its clause id."""
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not text:
        return
    buf = ""
    for part in (p.strip() for p in _HEADER.split(text)):
        if not part:
            continue
        if len(part) > CHUNK_SIZE:
            if buf:
                yield buf
                buf = ""
            m = _LABEL.match(part)
            cid = m.group(0) if m else part.split(" ", 1)[0]
            cur = ""
            for line in part.split("\n"):              # window on line boundaries so table rows stay whole
                if cur and len(cur) + 1 + len(line) > CHUNK_SIZE:
                    yield cur
                    cur = f"{cid} (cont.)"
                cur = f"{cur}\n{line}" if cur else line
            if cur:
                yield cur
        elif buf and (len(buf) + 1 + len(part) > CHUNK_SIZE or part.startswith(("Section ", "Schedule "))):
            yield buf                                    # every section/schedule heading starts a fresh chunk
            buf = part
        else:
            buf = f"{buf}\n{part}" if buf else part
    if buf:
        yield buf


def _fingerprint(files) -> str:
    """Hash of PDF *content* + chunk settings. Immune to mtime quirks; retuning also re-indexes."""
    h = hashlib.sha256(f"{CHUNKER_VERSION}/{CHUNK_SIZE}/{OVERLAP}".encode())
    for f in files:
        h.update(os.path.basename(f).encode())
        with open(f, "rb") as fh:
            h.update(fh.read())
    jsonl = os.path.join(KB_DIR, CLAUSES_FILE)
    if os.path.exists(jsonl):
        with open(jsonl, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


def reset_rag_session() -> None:
    """Forget what this session retrieved (called at the start of every crew cycle)."""
    RETRIEVED.clear(); RETRIEVED_CLAUSES.clear(); _RESULT_SETS.clear()


def _citation_tokens(meta: dict) -> set:
    """Every way an agent may legitimately cite this record: its clause id, its section number, 'Section N', or the schedule."""
    toks = {str(meta.get("clause", "")).lower(), str(meta.get("section", "")).lower()}
    sec = str(meta.get("section", ""))
    if sec.isdigit():
        toks.add(f"section {sec}")
    if str(meta.get("clause", "")).endswith(" table"):
        toks.add(str(meta["clause"]).lower().replace(" table", ""))
    return {t for t in toks if t}


def _load_records(files):
    """Clause records from clauses.jsonl when present (preferred, lossless); None means fall back to parsing the PDFs."""
    import json
    path = os.path.join(KB_DIR, CLAUSES_FILE)
    if not os.path.exists(path):
        return None
    ids, docs, metas = [], [], []
    names = {os.path.basename(f) for f in files}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["doc"] not in names:
                raise ValueError(f"{CLAUSES_FILE} refers to {rec['doc']}, which is not in {KB_DIR}/")
            ids.append(f"{rec['doc']}-p{rec['page']}-{rec['clause'].replace(' ', '_')}")
            docs.append(rec["text"])
            metas.append({"source": rec["doc"], "page": rec["page"], "chunk": rec["clause"], "clause": rec["clause"],
                          "section": rec["section"]})
    if len(set(ids)) != len(ids):
        raise ValueError(f"{CLAUSES_FILE} contains duplicate clause ids")
    return ids, docs, metas


def build_index(force: bool = False) -> int:
    """Index every PDF in KB_DIR into ChromaDB (.rag_store/). Returns chunk count.
    Re-uses the index unless PDFs/settings changed or force=True."""
    global _collection
    import chromadb
    from pypdf import PdfReader

    files = sorted(glob.glob(os.path.join(KB_DIR, "*.pdf")))
    if not files:
        raise FileNotFoundError(f"No PDFs found in {KB_DIR}/")
    client = chromadb.PersistentClient(path=RAG_DIR)
    name = f"sops_{_fingerprint(files)}"

    # drop stale collections left by older PDF versions
    for c in client.list_collections():
        cname = getattr(c, "name", c)
        if cname.startswith("sops_") and (cname != name or force):
            try:
                client.delete_collection(cname)
            except Exception:
                pass

    col = client.get_or_create_collection(name)
    loaded = _load_records(files) if col.count() == 0 else None
    if col.count() == 0 and loaded:
        col.add(ids=loaded[0], documents=loaded[1], metadatas=loaded[2])
        log("RAG", f"Indexed {len(loaded[1])} clause records from {CLAUSES_FILE}")
    elif col.count() == 0:
        ids, docs, metas = [], [], []
        for f in files:
            base = os.path.basename(f)
            for pno, page in enumerate(PdfReader(f).pages, start=1):
                txt = (page.extract_text() or "").strip()
                if not txt:
                    continue
                pieces = [c for c in _chunks(txt) if c.strip()]
                labels = [str(i) for i in range(len(pieces))]
                # whole-page chunk for recall on tables (approval matrix, AVL schedule)
                if pieces != [txt]:
                    pieces.append(txt)
                    labels.append("full")
                for label, chunk in zip(labels, pieces):
                    ids.append(f"{base}-p{pno}-c{label}")
                    docs.append(chunk)
                    metas.append({"source": base, "page": pno, "chunk": label})
        col.add(ids=ids, documents=docs, metadatas=metas)
        log("RAG", f"Indexed {len(docs)} chunks from {len(files)} PDFs")
    else:
        log("RAG", f"Re-using index ({col.count()} chunks)")
    _collection = col
    return col.count()


@tool("SOP Search")
def sop_search(query: str) -> str:
    """Semantic search over the company's Standard Operating Procedures and Vendor Contracts.
    Use it to find: severity levels, the RFQ trigger rule, RFQ quantity formula, which backup
    suppliers are approved, contingency measures (air freight, safety stock, re-routing, open POs),
    the approval matrix, force-majeure and supplier contract terms.
    Input: a short question. Returns passages headed [document, page] that you must cite.
    Tip: ask like the Coordinator does, e.g. 'RFQ trigger days of cover delay + 5 (clause 3.2)'
    or 'approval matrix total RFQ value Chief Financial Officer (section 5)'."""
    try:
        q = (query or "").strip()
        if not q:
            return "RAG ERROR: empty query. Retry with a question like 'When is an RFQ mandatory (clause 3.2)?'."
        if _collection is None:
            build_index()
        log("TOOL", f"SOP Search: {q}")
        r = _collection.query(query_texts=[q], n_results=max(8, TOP_K * 2),
                              include=["documents", "metadatas", "distances"])
        ids = [i.lower() for i in _IDS.findall(q)]          # exact ids named in the question
        ents = [i.lower() for i in _ENT.findall(q)]
        scored = sorted(((dist - (0.25 if any(i in d.lower() for i in ids) else 0.0)
                          - (0.05 if any(e in d.lower() for e in ents) else 0.0), d, m)
                         for d, m, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0])),
                        key=lambda x: x[0])
        if not scored or scored[0][0] > MAX_BEST:
            return ("RAG ERROR: nothing relevant found. Retry with a shorter query that names the clause, "
                    "e.g. 'RFQ quantity formula (clause 3.3)'.")
        best, hits, pages = scored[0][0], [], set()
        for score, d, m in scored:
            if score > best + REL_MARGIN or len(hits) == TOP_K:
                break
            if m.get("chunk") == "full" and (m["source"], m["page"]) in pages:
                continue                                     # whole-page chunk would repeat text already returned
            hits.append((d, m))
            pages.add((m["source"], m["page"]))
        for d, m in list(hits):                              # a clause that introduces a table brings the table along
            sec, kind = m.get("section"), str(m.get("clause", ""))
            if sec and not kind.endswith(" table") and len(hits) < TOP_K + 1:
                got = _collection.get(where={"$and": [{"source": m["source"]}, {"page": m["page"]}, {"section": sec}]},
                                      include=["documents", "metadatas"])
                for d2, m2 in zip(got["documents"], got["metadatas"]):
                    if str(m2.get("clause", "")).endswith(" table") and all(m2["clause"] != h[1].get("clause") for h in hits):
                        hits.append((d2, m2))
        key = frozenset((m["source"], m["page"], m.get("clause")) for _, m in hits)
        _RESULT_SETS[key] = _RESULT_SETS.get(key, 0) + 1
        note = ""
        if _RESULT_SETS[key] > MAX_REPEAT:               # the agent keeps asking for the same clauses: still answer, but tell it to move on
            note = (f"RAG NOTE: you already retrieved these clauses {_RESULT_SETS[key] - 1} times. Searching again changes nothing. "
                    "You have all the rules you need: apply them now, build the JSON payload and call 'Trigger n8n Procurement Workflow'.\n\n")
        for _, m in hits:
            RETRIEVED.add((m["source"], m["page"]))
            for tok in _citation_tokens(m):
                RETRIEVED_CLAUSES.add((m["source"], m["page"], tok))
        return note + "\n\n".join(f"[{m['source']}, page {m['page']}]" + (f" clause {m['clause']}" if m.get("clause") else "") + f"\n{d.strip()}"
                                  for d, m in hits)
    except Exception as e:
        return f"RAG ERROR: {e}. Retry with a shorter query."


if __name__ == "__main__":          # quick manual check: python tools/rag_tool.py
    build_index(force=True)
    for q in ["When is an RFQ mandatory?", "Who approves an order above 1 crore?",
              "Penang backup lead time"]:
        print("\nQ:", q, "\n", sop_search.run(query=q)[:500])