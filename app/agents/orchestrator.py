# app/agents/orchestrator.py
import re
from typing import Callable, Optional
import autogen
from app.config import get_llm_config
from app.rag.retriever import search_codebase, format_chunks_for_agent
from app.tools.code_tools import register_code_tools
from app.tools.github_tools import register_github_tools


class NexusOrchestrator:
    def __init__(
        self,
        task_id: str,
        repo_dir: str,
        repo_url: str,
        log_callback: Optional[Callable] = None,
    ):
        self.task_id = task_id
        self.repo_dir = repo_dir
        self.repo_url = repo_url
        self.log_callback = log_callback or (lambda *args: None)

    def _log(self, event_type: str, agent: str, message: str):
        print(f"[{event_type}] [{agent}] {message}")
        self.log_callback(event_type, agent, message)

    def _make_executor(self, termination_token: str):
        """
        Create a FRESH executor for every stage.
        This is the core fix — never reuse the same executor
        across stages because tool registrations accumulate.
        """
        return autogen.UserProxyAgent(
            name="executor",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=10, 
            is_termination_msg=lambda m: (
                termination_token in (m.get("content") or "")
                or "TERMINATE" in (m.get("content") or "")
            ),
            code_execution_config={
                "work_dir": self.repo_dir,
                "use_docker": False,
                "timeout": 60,
                "last_n_messages": 3,
            },
        )

    def _safe_last_message(self, executor, agent):
        try:
            msg = executor.last_message(agent)
            if msg is None:
                return ""
            return msg.get("content", str(msg))
        except Exception:
            return ""

    def run(self, task: str):
        results = {}

        self._log("STAGE_START 1", "system", "Planning...")
        plan = self._run_architect_stage(task)
        results["plan"] = plan

        self._log("STAGE_START 2", "system", "Coding...")
        code = self._run_coder_stage(task, plan)
        results["code"] = code

        self._log("STAGE_START 3", "system", "Testing...")
        tests = self._run_tester_stage(task)
        results["tests"] = tests

        self._log("STAGE_START 4", "system", "Reviewing...")
        review = self._run_reviewer_stage()
        results["review"] = review

        self._log("STAGE_START 5", "system", "Creating PR...")
        pr = self._run_git_stage(task, plan)
        results["pr"] = pr

        return results


    def _run_architect_stage(self, task: str) -> str:
        context = format_chunks_for_agent(
            search_codebase(task, self.task_id, n_results=8)
        )

        architect = autogen.AssistantAgent(
            name="architect",
            llm_config=get_llm_config(),
            system_message="""You are a senior software architect.
Create a file-by-file implementation plan.

OUTPUT FORMAT:
## Branch name: feature/[name]
## Files to modify: list them
## Files to create: list them
## Build order: numbered list
## Success criteria: bullet list

Do NOT write any code.
When plan is complete, write ##PLAN_READY## on its own line.""",
        )

        executor = autogen.UserProxyAgent(
            name="architect_proxy",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=3,
            is_termination_msg=lambda m: "##PLAN_READY##" in (m.get("content") or ""),
            code_execution_config=False,
        )

        executor.initiate_chat(
            architect,
            message=f"TASK:\n{task}\n\nCODEBASE CONTEXT:\n{context}",
            silent=False,
        )

        return self._safe_last_message(executor, architect)


    def _run_coder_stage(self, task: str, plan: str) -> str:
        coder = autogen.AssistantAgent(
            name="coder",
            llm_config=get_llm_config(),
            system_message=f"""You are a senior Python engineer.
Repo directory: {self.repo_dir}

RULES:
1. Use read_repo_file to read a file before editing it
2. Use write_repo_file to write the COMPLETE file content
3. Use run_command to run tests after writing
4. Fix errors if tests fail
5. Do NOT call list_repo_files more than ONCE — you already have the plan

When ALL files are written and tests pass, write ##CODE_COMPLETE## on its own line.
If you cannot complete after 3 attempts, write ##CODE_COMPLETE## anyway and explain.""",
        )

        executor = self._make_executor("##CODE_COMPLETE##")

        register_code_tools(coder, executor)

        executor.initiate_chat(
            coder,
            message=f"TASK:\n{task}\n\nPLAN:\n{plan}\n\nRepo: {self.repo_dir}",
            silent=False,
        )

        return self._safe_last_message(executor, coder)


    def _run_tester_stage(self, task: str) -> str:
        tester = autogen.AssistantAgent(
            name="tester",
            llm_config=get_llm_config(),
            system_message=f"""You are a pytest expert.
Repo directory: {self.repo_dir}

STEPS (do them in order, do not repeat):
1. Call list_repo_files ONCE to see the structure
2. Write test file to the repo
3. Run: python -m pytest -v --tb=short
4. Fix failures if any
5. Run tests one more time to confirm

When tests pass write ##TESTS_DONE## on its own line.
If tests cannot pass after 2 fix attempts, write ##TESTS_DONE## and explain.""",
        )

        executor = self._make_executor("##TESTS_DONE##")
        register_code_tools(tester, executor)

        executor.initiate_chat(
            tester,
            message=f"Write and run tests for this task:\n{task}\n\nRepo: {self.repo_dir}",
            silent=False,
        )

        return self._safe_last_message(executor, tester)


    def _run_reviewer_stage(self) -> str:
        reviewer = autogen.AssistantAgent(
            name="reviewer",
            llm_config=get_llm_config(),
            system_message=f"""You are a security-focused code reviewer.
Repo directory: {self.repo_dir}

STEPS (do them once, in order):
1. Call list_repo_files ONCE with extension_filter='.py'
2. Call read_repo_file on each Python file that was recently modified
3. Check for: security issues, bugs, missing error handling, performance

After reviewing ALL files, write your findings then write ##REVIEW_DONE##.
Do NOT call list_repo_files multiple times.""",
        )

        executor = self._make_executor("##REVIEW_DONE##")
        register_code_tools(reviewer, executor)

        executor.initiate_chat(
            reviewer,
            message=f"Review the modified Python files in: {self.repo_dir}",
            silent=False,
        )

        return self._safe_last_message(executor, reviewer)


    def _run_git_stage(self, task: str, plan: str) -> str:
        branch_match = re.search(r"Branch name:\s*([\w\-/]+)", plan)
        branch = (
            branch_match.group(1).strip()
            if branch_match
            else f"nexus/task-{self.task_id[:8]}"
        )

        git_agent = autogen.AssistantAgent(
            name="git_agent",
            llm_config=get_llm_config(),
            system_message=f"""You handle Git operations.
Repo directory: {self.repo_dir}

Do these steps IN ORDER, each step ONCE:
1. Call create_branch with branch name: {branch}
2. Call commit_changes with a descriptive message
3. Call create_pull_request with title and description

After PR is created write ##PR_CREATED## on its own line.""",
        )

        executor = self._make_executor("##PR_CREATED##")
        register_github_tools(git_agent, executor)

        executor.initiate_chat(
            git_agent,
            message=(
                f"TASK: {task}\n"
                f"BRANCH: {branch}\n"
                f"REPO DIR: {self.repo_dir}\n"
                f"REPO URL: {self.repo_url}"
            ),
            silent=False,
        )

        return self._safe_last_message(executor, git_agent)