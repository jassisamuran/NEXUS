# app/tools/github_tools.py
import os
import shutil
from typing import Annotated, Dict, List

import git
from github import Github

from app.config import settings


def clone_repository(repo_url: str, target_dir: str) -> str:
    """Clone a GitHub repository to local disk."""
    print("now")
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    try:
        # Inject token for private repos
        if settings.GITHUB_TOKEN and "github.com" in repo_url:
            auth_url = repo_url.replace(
                "https://github.com", f"https://{settings.GITHUB_TOKEN}@github.com"
            )
        else:
            auth_url = repo_url
            print("ths ")
        repo = git.Repo.clone_from(auth_url, target_dir, depth=1)
        return f"Cloned {repo_url} to {target_dir}"
    except Exception as e:
        raise ValueError(f"Clone failed: {str(e)}")


def register_github_tools(agent, executor):
    """Register all GitHub-related tools on the given agents."""
    gh = Github(settings.GITHUB_TOKEN) if settings.GITHUB_TOKEN else None

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Read a specific file from the cloned repository"
    )
    def read_repo_file(
        filepath: Annotated[str, "Relative path to file in repo (e.g. src/app.py)"],
        repo_dir: Annotated[str, "The local repository directory path"],
    ) -> str:
        full_path = os.path.join(repo_dir, filepath)
        if not os.path.exists(full_path):
            return f"Error: File not found: {filepath}"
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines = content.splitlines()
            # Return with line numbers for precise editing
            numbered = "\n".join(f"{i + 1:4}: {line}" for i, line in enumerate(lines))
            return f"File: {filepath} ({len(lines)} lines)\n\n{numbered}"
        except Exception as e:
            return f"Error reading file: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(description="Write or overwrite a file in the repository")
    def write_repo_file(
        filepath: Annotated[str, "Relative path to file (e.g. src/auth.py)"],
        content: Annotated[
            str, "Complete file content — write the entire file, not just changes"
        ],
        repo_dir: Annotated[str, "The local repository directory path"],
    ) -> str:
        full_path = os.path.join(repo_dir, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Written {len(content)} chars to {filepath}"
        except Exception as e:
            return f"Error writing file: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(
        description="List all files in the repository or a subdirectory"
    )
    def list_repo_files(
        repo_dir: Annotated[str, "The local repository directory path"],
        subdir: Annotated[str, "Subdirectory to list (empty string for root)"] = "",
    ) -> str:
        target = os.path.join(repo_dir, subdir) if subdir else repo_dir
        if not os.path.exists(target):
            return f"Directory not found: {subdir}"
        result = []
        for root, dirs, files in os.walk(target):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d not in {"__pycache__", "node_modules", ".git", "venv", ".venv"}
            ]
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, repo_dir)
                size = os.path.getsize(full)
                result.append(f"{rel} ({size} bytes)")
        return "\n".join(result[:100]) + (
            "\n... (truncated)" if len(result) > 100 else ""
        )

    @executor.register_for_execution()
    @agent.register_for_llm(description="Create a new git branch for the changes")
    def create_branch(
        branch_name: Annotated[str, "Branch name (e.g. feature/add-jwt-auth)"],
        repo_dir: Annotated[str, "The local repository directory path"],
    ) -> str:
        try:
            repo = git.Repo(repo_dir)
            new_branch = repo.create_head(branch_name)
            new_branch.checkout()
            return f"Created and checked out branch: {branch_name}"
        except Exception as e:
            return f"Error creating branch: {str(e)}"

    @executor.register_for_execution()
    @agent.register_for_llm(description="Commit all changes to the current branch")
    def commit_changes(
        message: Annotated[str, "Commit message describing the changes"],
        repo_dir: Annotated[str, "The local repository directory path"],
    ) -> str:
        try:
            repo = git.Repo(repo_dir)
            repo.git.add(A=True)
            if not repo.is_dirty(untracked_files=True):
                return "No changes to commit."
            repo.index.commit(
                message,
                author=git.Actor("NEXUS AI", "nexus@ai-dev.io"),
                committer=git.Actor("NEXUS AI", "nexus@ai-dev.io"),
            )
            changed = [item.a_path for item in repo.index.diff("HEAD~1")]
            return f"Committed with message: {message}\nChanged files: {', '.join(changed[:10])}"
        except Exception as e:
            return f"Error committing: {str(e)}"


    @executor.register_for_execution()
    @agent.register_for_llm(
        description="Push the branch and create a GitHub Pull Request"
    )
    def create_pull_request(
        repo_url: Annotated[str, "GitHub repo URL (e.g. https://github.com/user/repo)"],
        branch_name: Annotated[str, "Branch to create PR from"],
        pr_title: Annotated[str, "Pull request title"],
        pr_body: Annotated[str, "Detailed pull request description with what changed and why"],
        repo_dir: Annotated[str, "The local repository directory path"],
    ) -> str:
        if not gh:
            return "GitHub token not configured. Cannot create PR. Changes are committed locally."
        try:
            local_repo = git.Repo(repo_dir)

            auth_push_url = repo_url.rstrip("/").replace(
                "https://github.com",
                f"https://{settings.GITHUB_TOKEN}@github.com"
            )
            if not auth_push_url.endswith(".git"):
                auth_push_url += ".git"

            # Force push — handles branch already existing on remote from previous runs
            local_repo.git.push(auth_push_url, branch_name, force=True)

            repo_name = (
                repo_url.rstrip("/").split("github.com/")[-1].replace(".git", "")
            )
            gh_repo = gh.get_repo(repo_name)
            default_branch = gh_repo.default_branch

            # Check if PR already exists for this branch before creating
            existing_prs = gh_repo.get_pulls(state="open", head=f"jassisamuran:{branch_name}")
            for existing_pr in existing_prs:
                return f"PR already exists: {existing_pr.html_url}\nPR #{existing_pr.number}: {existing_pr.title}"

            pr = gh_repo.create_pull(
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=default_branch,
            )
            return f"PR created: {pr.html_url}\nPR #{pr.number}: {pr.title}"
        except Exception as e:
            return f"Error creating PR: {str(e)}"