"""
Tool 1 — Document search/retrieval.

Searches the Chroma store over the policy/SOP/contract PDFs and returns
chunks WITH their authority metadata attached, sorted most-authoritative
first. Deprecated content is never hidden (the agent may need to explain
why it's NOT using it) but it always sorts last.

A customer must never receive another customer's contract in results —
that filter is enforced here, in code.
"""
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from app.config import settings, DOC_AUTHORITY_RANK
from app.access_control import UserContext, Role

_embeddings = HuggingFaceEmbeddings(model_name=settings.embedding_model)
_vectordb = Chroma(
    collection_name="parcelpilot_docs",
    embedding_function=_embeddings,
    persist_directory=settings.chroma_persist_dir,
)


def search_documents(
    user_ctx: UserContext,
    query: str,
    doc_type: str | None = None,
    customer_id: str | None = None,
    k: int = 6,
) -> list[dict]:
    """
    doc_type: one of support_policy_current, support_policy_deprecated,
              cancellation_sop, product_ops, contract, or None.
    customer_id: scope contract search to one customer (e.g. "northstar").
                 Forced to the caller's own account for customer role.

    Returns chunks sorted by authority_rank ascending (most authoritative
    first): [{text, source_file, doc_type, status, version, customer_id,
              authority_rank}]
    """
    if user_ctx.role == Role.CUSTOMER:
        customer_id = user_ctx.account_id  # customers can't request another's contract

    where = {"doc_type": doc_type} if doc_type else None
    results = _vectordb.similarity_search(query, k=k, filter=where)

    output = []
    for doc in results:
        meta = doc.metadata
        chunk_customer_id = meta.get("customer_id")
        chunk_customer_id = None if chunk_customer_id == "none" else chunk_customer_id

        # A contract chunk belonging to a different customer is dropped
        # entirely, regardless of what the caller asked for.
        if meta.get("doc_type") == "contract" and chunk_customer_id is not None:
            if customer_id is not None and chunk_customer_id != customer_id:
                continue
            if user_ctx.role == Role.CUSTOMER and chunk_customer_id != user_ctx.account_id:
                continue

        output.append({
            "text": doc.page_content,
            "source_file": meta.get("source_file"),
            "doc_type": meta.get("doc_type"),
            "status": meta.get("status"),
            "version": meta.get("version"),
            "customer_id": chunk_customer_id,
            "authority_rank": DOC_AUTHORITY_RANK.get(meta.get("doc_type"), 50),
        })

    output.sort(key=lambda x: x["authority_rank"])
    return output
