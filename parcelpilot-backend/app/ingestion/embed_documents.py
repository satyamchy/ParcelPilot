"""
Builds embeddings from the PDF data pack, ONLY. Doesn't touch SQLite or
the workbook — run this independently whenever the PDFs change.

Usage:
    python -m app.ingestion.embed_documents

Reads:  data/pdfs/*.pdf
Writes: data/db/chroma/  (Chroma vector store, local sentence-transformers
        embeddings — no API key needed for this step)
"""
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from app.config import settings, DOCUMENT_METADATA


def load_and_chunk_pdfs() -> list:
    """Reads every PDF in data/pdfs/, tags each chunk with its
    hand-authored authority metadata from config.py, and returns the
    combined chunk list. Files not listed in DOCUMENT_METADATA are
    skipped with a warning — their authority/status must be explicit,
    never guessed."""
    pdf_dir = Path(settings.pdf_dir)
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in {pdf_dir}.")

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
    all_chunks = []

    for pdf_path in pdf_files:
        meta = DOCUMENT_METADATA.get(pdf_path.name)
        if meta is None:
            print(f"  WARNING: {pdf_path.name} not in DOCUMENT_METADATA "
                  f"(app/config.py) — skipping.")
            continue

        pages = PyPDFLoader(str(pdf_path)).load()
        chunks = splitter.split_documents(pages)
        for c in chunks:
            c.metadata.update({
                "source_file": pdf_path.name,
                **{k: (v if v is not None else "none") for k, v in meta.items()},
            })
        all_chunks.extend(chunks)
        print(f"  {pdf_path.name}: {len(chunks)} chunks "
              f"(doc_type={meta['doc_type']}, status={meta['status']})")

    return all_chunks


def build_vector_store(chunks: list) -> None:
    """Embeds the given chunks and (re)writes the Chroma collection from
    scratch, so re-running this never duplicates old chunks."""
    print(f"\nEmbedding {len(chunks)} chunks locally (sentence-transformers, "
          f"no API key required)...")
    embeddings = HuggingFaceEmbeddings(model_name=settings.embedding_model)

    Path(settings.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
    vectordb = Chroma(
        collection_name="parcelpilot_docs",
        embedding_function=embeddings,
        persist_directory=settings.chroma_persist_dir,
    )
    try:
        vectordb.delete_collection()
    except Exception:
        pass  # collection didn't exist yet — fine

    vectordb = Chroma(
        collection_name="parcelpilot_docs",
        embedding_function=embeddings,
        persist_directory=settings.chroma_persist_dir,
    )
    vectordb.add_documents(chunks)
    print(f"Chroma store written to {settings.chroma_persist_dir}")


if __name__ == "__main__":
    print("=== Building document embeddings ===\n")
    chunks = load_and_chunk_pdfs()
    build_vector_store(chunks)
    print("\nDone.")
