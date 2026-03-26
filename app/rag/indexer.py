import hashlib
import os

os.environ["ANONYMIZED_TELEMETRY"] = "False"
from typing import Callable, List

import chromadb
from chromadb.utils import embedding_functions

from app.config import settings
from app.rag.ast_parser import CodeChunk, parse_python_file

SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".go": "go",
    ".md": "markdown",
    ".txt": "text",
    ".yaml": ".yml",
    ".json": "json",
}
ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)


def get_chroma_client():
    return chromadb.HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)


def get_collection(task_id: str):
    client = get_chroma_client()

    return client.get_or_create_collection(
        name=f"repo_{task_id.replace('-', '_')}",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def index_repository(
    repo_path: str, task_id: str, progress_callback: Callable = None
) -> int:
    """Index an entire repository into ChromaDB.
    Returns the number of chunks indexed.
    """
    collection = get_collection(task_id)
    all_files = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d
            not in {
                "node_modules",
                "__pycache__",
                "venv",
                ".venv",
                "dist",
                "build",
                ".git",
                "coverage",
            }
        ]
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                all_files.append(os.path.join(root, filename))

    total = len(all_files)
    indexed_chunks = 0

    for i, filepath in enumerate(all_files):
        ext = os.path.splitext(filepath)

        if ext == ".py":
            chunks = parse_python_file(filepath, repo_path)
        else:
            chunks = _chunk_generic_file(filepath, repo_path, ext)

        if not chunks:
            continue

        documents = []
        metadatas = []
        ids = []

        for chunk in chunks:
            chunk_id = hashlib.md5(
                f"{task_id}:{chunk.filepath}:{chunk.name}:{chunk.start_line}".encode()
            ).hexdigest()

            doc = f"File: {chunk.filepath}\nType: {chunk.chunk_type}\nName: {chunk.name or ''}\n\n{chunk.content}"
            if chunk.docstring:
                doc = f"Docstring: {chunk.docstring}\n\n" + doc

            documents.append(doc)
            metadatas.append(
                {
                    "filepath": chunk.filepath,
                    "chunk_type": chunk.chunk_type,
                    "name": chunk.name or "",
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "language": SUPPORTED_EXTENSIONS.get(ext, "unknown"),
                }
            )
            ids.append(chunk_id)

        if documents:
            collection.upsert(documents=documents, metadatas=metadatas, ids=ids)
            indexed_chunks += len(documents)

        if progress_callback:
            progress_callback(int((i + 1) / total * 100), f"Index {filepath}")

    return indexed_chunks


def _chunk_generic_file(filepath: str, repo_root: str, ext: str) -> List[CodeChunk]:
    """Split non-Python files into chunks"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return []

    relative_path = os.path.relpath(filepath, repo_root)
    chunks = []
    lines = content.splitlines()
    chunk_size = 80

    for i in range(0, len(lines), chunk_size):
        chunk_content = "\n".join(lines[i : i + chunk_size])
        if chunk_content.strip():
            from rag.ast_parser import CodeChunk

            chunks.append(
                CodeChunk(
                    content=chunk_content,
                    filepath=relative_path,
                    chunk_type="file_chunk",
                    name=f"{relative_path}:lines_{i}",
                    start_line=i + 1,
                    end_line=min(i + chunk_size, len(lines)),
                    docstring=None,
                    dependencies=[],
                )
            )

    return chunks
