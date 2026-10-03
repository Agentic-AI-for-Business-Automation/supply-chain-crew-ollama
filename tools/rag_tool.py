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


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


# Clause / section headers that make good split points
_HEADER = re.compile(r"(?m)(?=^(?:Section \d|Schedule [AB]|VC-\d+\.\d+|\d+\.\d+)\b)")


def _chunks(text: str):
    """Clause-aware overlapping chunks. Splits at clause headers so a rule stays whole;
    oversized clauses fall back to fixed windows with overlap."""
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not text:
        return
    buf = ""
    for part in _HEADER.split(text):
        part = part.strip()
        if not part:
            continue
        if len(part) > CHUNK_SIZE:                      # one huge clause -> fixed windows
            if buf.strip():
                yield buf.strip()
                buf = ""
            step = max(1, CHUNK_SIZE - OVERLAP)
            for s in range(0, len(part), step):
                yield part[s:s + CHUNK_SIZE]
                if s + CHUNK_SIZE >= len(part):
                    break
            continue
        if buf and len(buf) + 1 + len(part) > CHUNK_SIZE:
            yield buf.strip()
            tail = buf[-OVERLAP:]                       # carry overlap, start at a word boundary
            buf = tail.split(" ", 1)[1] if " " in tail else tail
        buf = f"{buf}\n{part}" if buf else part
    if buf.strip():
        yield buf.strip()


def _fingerprint(files) -> str:
    """Hash of PDF *content* + chunk settings. Immune to mtime quirks; retuning also re-indexes."""
    h = hashlib.sha256(f"{CHUNK_SIZE}/{OVERLAP}".encode())
    for f in files:
        h.update(os.path.basename(f).encode())
        with open(f, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


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
    if col.count() == 0:
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
                    metas.append({"source": base, "page": pno})
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
        r = _collection.query(query_texts=[q], n_results=TOP_K)
        docs, metas = r["documents"][0], r["metadatas"][0]
        if not docs:
            return "RAG ERROR: no passages found. Retry with a shorter query."
        return "\n\n".join(f"[{m['source']}, page {m['page']}]\n{d.strip()}"
                           for d, m in zip(docs, metas))
    except Exception as e:
        return f"RAG ERROR: {e}. Retry with a shorter query."


if __name__ == "__main__":          # quick manual check: python tools/rag_tool.py
    build_index(force=True)
    for q in ["When is an RFQ mandatory?", "Who approves an order above 1 crore?",
              "Penang backup lead time"]:
        print("\nQ:", q, "\n", sop_search.run(query=q)[:500])