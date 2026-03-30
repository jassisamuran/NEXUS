import os
import subprocess
import sys
from typing import Annotated, Optional

from app.config import settings


def register_code_tools(agent, executor):
    """
    Register all code execution and file operation tools
    on the given agent (for LLM awareness) and executor
    (for actual execution).
    """

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Read a file from the repository with line numbers"
    )
    def read_repo_file(
        filepath: Annotated[str, "Relative path to file inside repo (e.g. src/app.py)"],
        repo_dir: Annotated[
            str, "Absolute or relative path to the repo root directory"
        ],
    ) -> str:
        full_path = os.path.join(repo_dir, filepath)

        if not os.path.exists(full_path):
            return f"Error: File not found: {filepath}"

        if os.path.getsize(full_path) > 200_000:
            return f"Error: File too large to read (>{200_000} bytes)."

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

                # add line numbers so the agent can reference exact lines
            numbered = "".join(f"{i + 1:4}: {line}" for i, line in enumerate(lines))
            return f"File: {filepath} ({len(lines)} lines)\n\n{numbered}"
        except Exception as e:
            return f"Error reading {filepath}: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Write complete content to a file. Always write the FULL file,never partial content."
    )
    def write_repo_file(
        filepath: Annotated[
            str,
            "Relateive path to file (e.g src/auth.py). Will create directories if needed.",
        ],
        content: Annotated[
            str,
            "Complete file content. Write the ENTIRE file - never use placeholders.",
        ],
        repo_dir: Annotated[str, "Absolute or relative path to the root directory"],
    ) -> str:
        full_path = os.path.join(repo_dir, filepath)

        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            line_count = content.count("\n")
            return f"Successfully wrote {filepath} ({len(content)} chars, ~{line_count} lines)"

        except Exception as e:
            return f"Error writing {filepath}: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="List all files in the repository. Skips hidden folders, node_modules, __pycache__, venv."
    )
    def list_repo_files(
        repo_dir: Annotated[
            str, "Absolute or relative path to the repo root directory"
        ],
        subdir: Annotated[str, "Subdirectory to list. Use empty string for root."] = "",
        extension_filter: Annotated[
            str, "Only show files with this extension, e.g. '.py'. Empty= show all."
        ] = "",
    ) -> str:
        target = os.path.join(repo_dir, subdir) if subdir else repo_dir

        if not os.path.exists(target):
            return f"Directory not found: {target}"

        SKIP_DIRS = {
            "__pycache__",
            "node_modules",
            ".git",
            "venv",
            ".venv",
            "env",
            ".env",
            "dist",
            "build",
            ".pytest_cache",
            ".mypy_cache",
            "htmlcov",
        }

        result = []
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]

            for filename in sorted(files):
                if filename.startswith("."):
                    continue

                if extension_filter and not filename.endswith(extension_filter):
                    continue

                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, repo_dir)
                size = os.path.getsize(full_path)

                if size > 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f}MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size}B"

                result.append(f"{rel_path} ({size_str})")

        if not result:
            return f"No files found in {target}" + (
                f"with extension {extension_filter}" if extension_filter else ""
            )

        header = f"Files in {subdir or 'root'} ({len(result)} total):\n\n"

        if len(result) > 150:
            return (
                header
                + "\n".jon(result[:150])
                + f"\n\n... and  {len(result) - 150} more files"
            )

        return header + "\n".join(result)

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Run a shell command inside the repository directory. Use for running tests, installing packages, checking syntax, etc."
    )
    def run_command(
        command: Annotated[
            str,
            "Shell command to run (e.g. 'python -m pytest tests/ -v' pr 'pip install requests' )",
        ],
        repo_dir: Annotated[str, "Directory to run the command in "],
        timeout: Annotated[
            int, "Timeout in seconds. Default 60. use 120 for slow test suites."
        ] = 60,
    )->str:
        BLOCKED = ["rm -rf /", "mkfs", "dd if=", "format c:", "shutdown", "reboot"]
        for blocked in BLOCKED:
            if blocked in command.lower():
                return f"Error: Command blocked for safety: {command}"

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={
                    **os.environ,
                    "PYTHONPATH": repo_dir,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )

            output = ""

            if result.stdout:
                stdout = result.stdout

                if len(stdout) > 5000:
                    stdout = (
                        stdout[:2500] + "\n\n... (truncated) ...\n\n" + stdout[-2500:]
                    )
                    output += f"STDOUT:\n{stdout}\n"

            if result.stderr:
                stderr = result.stderr
                if len(stderr) > 3000:
                    stderr = (
                        stderr[:1500] + "\n\n... (truncated) ... \n\n" + stderr[-1500:]
                    )
                output += f"STDERR:\n{stderr}\n"

            output += f"\nExit code: {result.returncode}"

            if result.returncode == 0:
                output += " (success)"
            else:
                output += " (failed)"

            return (
                output if output.strip() else "Command run with no output. Exit code: 0"
            )

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds: {command}"
        except Exception as e:
            return f"Error running command: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Check python syntax of a file without executing it. Use before running to catch syntax errors early."
    )
    def check_python_syntax(
        filepath: Annotated[str, "Relative path to .py file to check"],
        repo_dir: Annotated[str, "Repo root directory"],
    ) -> str:
        full_path = os.path.join(repo_dir, filepath)

        if not os.path.exists(full_path):
            return f"Error: File not found: {filepath}"

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                source = f.read()

            import ast

            ast.parse(source)
            return f"Syntax OK: {filepath}"
        except SyntaxError as e:
            return (
                f"Syntax Error in {filepath}:\n"
                f"Line {e.lineno}: {e.msg}\n"
                f"Text {e.text} or ''"
            )

        except Exception as e:
            return f"Error checking syntax: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Search for a string or pattern inside all files in the repository. Useful for finding where a function is used or where a variable is defined."
    )
    def search_in_files(
        search_term: Annotated[str, "String to search for inside files"],
        repo_dir: Annotated[str, "Repo root directory"],
        file_extension: Annotated[
            str,
            "Only search in files with this extension (e.g. '.py'). Empty = all files.",
        ] = ".py",
    ) -> str:
        SKIP_DIRS = {
            "__pycache__",
            "node_modules",
            ".git",
            "venv",
            ".venv",
            "dist",
            "build",
        }

        matches = []

        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for filename in files:
                if file_extension and not filename.endswith(file_extension):
                    continue

                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, repo_dir)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()

                    for i, line in enumerate(lines, 1):
                        if search_term.lower() in line.lower():
                            matches.append(f"{rel_path}:{i} {line.rstrip()}")

                            if len(matches) > 200:
                                break

                except Exception:
                    continue

        if not matches:
            return f"No matches found for '{search_term}'"

        result = f"Found {len(matches)} matches for '{search_term}':\n\n"
        result += "\n".join(matches[:100])

        if len(matches) > 100:
            result += f"\n\n... and {len(matches) - 100} more matches"

        return result

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Delete a file from the repository. Use carefully — only delete files you created that are no longer needed."
    )
    def delete_repo_file(
        filepath: Annotated[str, "Relative path to file to delete"],
        repo_dir: Annotated[str, "Repo root directory"],
    )->str:
        full_path = os.path.join(repo_dir, filepath)

        if not os.path.exists(full_path):
            return f"File not found (already deleted?): {filepath}"

        try:
            os.remove(full_path)
            return f"Deleted: {filepath}"
        except Exception as e:
            return f"Error deleting {filepath}: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Read only specific lines from a large file. Use when you only need to see part of a file."
    )
    def read_file_lines(
        filepath: Annotated[str, "Relative path to file"],
        repo_dir: Annotated[str, "Repo root directory"],
        start_line: Annotated[int, "First line to read (1-indexed)"],
        end_line: Annotated[int, "Last line to read (inclusive)"]   ,
    ) -> str:
        full_path = os.path.join(repo_dir, filepath)

        if not os.path.exists(full_path):
            return f"Error: file not found: {filepath}"

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()

            total = len(all_lines)
            start = max(0, start_line - 1)
            end = min(total, end_line)

            selected = all_lines[start:end]

            numbered = "".join(
                f"{start + i + 1:4}:{line}" for i, line in enumerate(selected)
            )
            return (
                f"File: {filepath}  (showing lines {start_line}-{end_line} of {total})\n\n"
                f"{numbered}"
            )

        except Exception as e:
            return f"Error reading lines: {str(e)}"

    return agent, executor
