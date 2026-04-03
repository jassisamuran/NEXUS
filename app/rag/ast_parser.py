import ast
import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CodeChunk:
    """A meaningful chunk of code with metadata."""

    content: str
    filepath: str
    chunk_type: str
    name: Optional[str]
    start_line: int
    end_line: int
    docstring: Optional[str]
    dependencies: List[str]


def parse_python_file(filepath: str, repo_root: str) -> List[CodeChunk]:
    """Parse a Python file using AST and return meaningful chunks.
    This is much better than native text splitting because we never
    split in the middle of a function.
    """

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except Exception:
        return []

    relative_path = os.path.relpath(filepath, repo_root)
    chunks = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        chunks.append(
            CodeChunk(
                content=source[:3000],
                filepath=relative_path,
                chunk_type="module",
                name=relative_path,
                start_line=1,
                end_line=source.count("\n"),
                docstring=None,
                dependencies=[],
            )
        )
        return chunks

    lines = source.splitlines()

    import_lines = []
    for node in ast.walk(tree):
        print("asts", ast.Import, ast.ImportFrom, node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_lines.append(ast.get_source_segment(source, node) or "")
            print("getline bi", ast.get_source_segment(source, node))

    if import_lines:
        chunks.append(
            CodeChunk(
                content="\n".join(import_lines),
                filepath=relative_path,
                chunk_type="import_block",
                name=f"{relative_path}:imports",
                start_line=1,
                end_line=len(import_lines),
                docstring=None,
                dependencies=[],
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunk = _extract_function_chunk(node, lines, relative_path, source)
            if chunk:
                chunks.append(chunk)

        elif isinstance(node, ast.ClassDef):
            class_chunk = _extract_class_chunk(node, lines, relative_path, source)
            if class_chunk:
                chunks.append(class_chunk)

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_chunk = _extract_function_chunk(
                        item, lines, relative_path, source, class_name=node.name
                    )
                    if method_chunk:
                        chunks.append(method_chunk)

    if len(chunks) <= 1:
        for i in range(0, len(lines), 100):
            chunk_lines = lines[i : i + 100]
            chunks.append(
                CodeChunk(
                    content="\n".join(chunk_lines),
                    filepath=relative_path,
                    chunk_type="module",
                    name=f"{relative_path}:lines_{i}-{i + 100}",
                    start_line=i + 1,
                    end_line=min(i + 100, len(lines)),
                    docstring=None,
                    dependencies=[],
                )
            )
    return chunks


def _extract_function_chunk(node, lines, filepath, source, class_name=None):
    try:
        start = node.lineno - 1
        end = node.end_lineno
        content = "\n".join(lines[start:end])
        docstring = ast.get_docstring(node) or ""
        name = f"{class_name}.{node.name}" if class_name else node.name

        return CodeChunk(
            content=content,
            filepath=filepath,
            chunk_type="method" if class_name else "function",
            name=name,
            start_line=node.lineno,
            end_line=node.end_lineno,
            docstring=docstring,
            dependencies=[],
        )
    except Exception:
        return None


def _extract_class_chunk(node, lines, filepath, source):
    try:
        start = node.lineno - 1
        sig_end = min(start + 20, node.end_lineno)
        content = "\n".join(lines[start:sig_end])
        docstring = ast.get_docstring(node) or ""
        methods = [n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)]

        return CodeChunk(
            content=content + f"\n# Methods: {', '.join(methods)}",
            filepath=filepath,
            chunk_type="class",
            name=node.name,
            start_line=node.lineno,
            end_line=node.end_lineno,
            docstring=docstring,
            dependencies=[],
        )
    except Exception:
        return None
