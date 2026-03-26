from typing import Dict, List

from app.rag.indexer import get_collection


def search_codebase(
    query: str, task_id: str, n_results: int = 8, filter_by_type: str = None
):
    """Semantic search over the indexed codebase.
    Return relevant code chunks with metadata.
    """
    collection = get_collection(task_id)

    where = None
    if filter_by_type:
        where = {"chunk_type": {"$eq": filter_by_type}}

    try:
        results = collection.query(
            query_texts=[query],
            n_results=min(n_results, collection.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

    except Exception as e:
        return []

    chunks = []
    if results and results["documents"] and results["documents"][0]:
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        ):
            chunks.append(
                {
                    "content": doc,
                    "filepath": meta.get("filepath", ""),
                    "chunk_type": meta.get("chunk_type", ""),
                    "name": meta.get("name", ""),
                    "start_line": meta.get("start_line", 0),
                    "relevance_score": round(1 - dist, 3),
                }
            )
    return chunks


def get_file_contents(task_id: str, filepath: str) -> str:
    """Get all chunks from a specific file."""
    collection = get_collection(task_id)
    try:
        results = collection.get(
            where={"filepath": {"$eq": filepath}}, include=["documents", "metadatas"]
        )

        if results and results["documents"]:
            pairs = sorted(
                zip(
                    results["documents"],
                    results["metadatas"],
                    key=lambda x: x[1].get("start_line", 0),
                )
            )

            return "\n\n".join(doc for doc, _ in pairs)

    except Exception:
        pass
    return ""


def delete_collection(task_id: str):
    """clean up after complete task"""
    try:
        from app.rag.indexer import get_chroma_client

        client = get_chroma_client()
        client.delete_collection(f"repo_{task_id.replace('-', '_')}")
    except Exception:
        pass


def format_chunks_for_agent(chunks: List[Dict]) -> str:
    """Format retrieved chunks into a readable string for agent context."""
    if not chunks:
        return "No relevant code found."
    result = f"Found {len(chunks)} relevant code section:\n"
    for i, chunk in enumerate(chunks, 1):
        result += (
            f"--[{i}] {chunk['filepath']} (relevance: {chunk['relevance_score']}) ---\n"
        )
        result += chunk["content"][:1500]
        result += "\n\n"
    return result
