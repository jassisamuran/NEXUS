import os
import time
from typing import Callable, Optional

import autogen

from app.config import get_llm_config, settings
from app.rag.retriever import format_chunks_for_agent, search_codebase
from app.tools.github_tools import register_github_tools


class NexusOrchestrator:
    """
    Orhestrates all agents for a single task.
    uses a seuqential pipeline with specialist agents:
    Architect -> coder -> tester -> reviewer -> git agent
    """

    def __init__(
        self,
        task_id: str,
        repo_dir: str,
        repo_url: str,
        log_callback: Optional[Callable] = None,
    ):
        self.task_id = task_id
        self.repo_dir = repo_dir
        self.repo_url = self.repo_url
        self.log_callback = log_callback or (lambda *args: None)

        # create the excutor  - shared across all stages
        self.executor = autogen.UserProxyAgetn(
            name="executor",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=20,
            is_termination_msg=lambda m: any(
                token in m.get("content", "")
                for token in [
                    "##DONE##",
                    "##FAILED##",
                    "##PLAN_READY##",
                    "##CODE_COMPLETE##",
                    "##TESTS_DONE##",
                    "##REVIEW_DONE##",
                    "##PR_CREATED##",
                ]
            ),
            code_execution_config={
                "work_dir": self.repo_dir,
                "use_docker": False,
                "timeout": 60,
                "last_n_message": 5,
            },
        )

        #       register all tools
        register_github_tools()

    def _create_dummy_agent(self):
        """Creates a temporary agent just for tool registration."""
        return autogen.AssitantAgent("_temp", llm_config=get_llm_config)

    def _log(self, event_type: str, agent: str, message: str):
        self.log_callback(event_type, agent, message)

    def run(self, task_descripition: str) -> dict:
        results = {}

        # stage 1
        self.log("Start_stage", "system", "Stage 1: Architect is analyzing code...")
        plan = self._run_architect_stage(task_descripition)
        results["plan"] = plan

        # stage 2: Coding with rag context
        self._log("STAGE_START", "system", "Stage 2: Coder is implementing changes...")
        code_result = self._run_coder_stage(task_descripition, plan)
        results["code"] = code_result

        # stage 3 Testing
        self._log(
            "STAGE_START", "system", "Stage 3: Tester is writing and running tests..."
        )
        test_result = self._run_tester_stage(task_descripition)
        results["tests"] = test_result

        # stage 4 : Code Review
        self._log(
            "STAGE_START", "system", "Stage4: Reviewer is checking code quality..."
        )
        review_result = self._run_reviewer_stage()
        results["review"] = review_result

        # stage 5: Create PR
        self._log("STAGE_START", "system", "Stage 5: Creating pull Request...")
        pr_result = self._run_git_stage(task_descripition, plan)
        results["pr"] = pr_result

        return results

    def _run_architect_stage(self, task: str) -> str:
        """RAG-powered planning: search the codebase and create a precise plan."""

        # search for relevant code
        relevant_chunks = search_codebase(task, self.task_id, n_results=10)
        code_context = format_chunks_for_agent(relevant_chunks)

        # also search specifically for tests, models, routes
        test_chunks = search_codebase(
            f"test {task}", self.task_id, n_results=5, filter_by_type="function"
        )
        model_chunks = search_codebase(
            "models databae schema", self.task_id, n_results=5, filter_by_type="class"
        )

        architect = autogen.AssistantAgent(
            name="Architect",
            llm_config=get_llm_config,
            system_message=f"""You are a senior software architect.
            You have deep knowledge of the codebase from semantic search results.
            Your job: create a precise, file-by-file implementation plan.

            REPO DIRECTORY: {self.repo_dir}
            REPO URL: {self.repo_url}

            OUTPUT FORMAT:
            ## Branch name: feature/[descriptive-name]

            ## Files to modify:
            1. [exact/filepath.py]
            - Current purpose: [what it does now]
            - Changes needed: [specific changes, line numbers if possible]

            ## Files to create:
            1. [exact/new_file.py]
            - Purpose: [what it will do]
            - Key functions/classes to implement

            ## Implementation order:
            1. [file] — [reason for this order]

            ## Dependencies to add (pip packages if any):
            - [package==version]

            ## Test approach:
            - [what to test and how]

            After the plan, say ##PLAN_READY##""",
        )

        proxy = autogen.userProxyAgent(
            name="ArchitectProxy",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=2,
            is_termination_msg=lambda m: "##PLAN_READY" in m.get("content", ""),
            code_execution_config=False,
        )

        self.log("AGENT_MESSAGE", "Architect", "Reading codebase with RAG...")

        proxy.initiate_chat(
            architect,
            message=f"""Task: {task}

        RELEVANT CODE FROM CODEBASE:
        {code_context}

        EXISTING TESTS:
        {format_chunks_for_agent(test_chunks)}

        EXISTING MODELS:
        {format_chunks_for_agent(model_chunks)}

        Create a detailed implementation plan.""",
            silent=True,
        )

        plan = proxy.last_message(architect)["content"]
        self._log("AGENT_MESSAGE", "Architect", f"Plan created: \n{plan[:5000]}...")
        return plan

    def _run_coder_stage(self, task: str, plan: str) -> str:
        """Coder implements changes file by file based on the architect's plan."""

        coder = autogen.AssistantAgent(
            name="Coder",
            llm_config=get_llm_config(fast=False),
            system_message=f"""Your are senior software engineer implementing code changes.

            REPO DIRECTORY: {self.repo_dir}

            CRITICAL RULES:
            - Always read a file fully before modifying it (use read_repo_file tool)
            - Write the complete file content, not just the changed parts
            - Never use placeholders like "# existing code here" - Write everything
            - After writing each file, say what you wrote and why
            - Run any test commands to verify changes don't break existing tests
            - Search the codebase for patterns before writing new code
            
            when ALL files from the plan are implemented, say ##CODE_COMPLETE##
            """,
        )

        register_github_tools(coder, self.executor)
        # register_code_tools here

        search_result = search_codebase(task, self.task_id, n_results=6)

        self.executor.initiate_chat(
            coder,
            message=f"""
        Implement this task completely:

        TASK: {task}

        ARCHITECT'S PLAN:
        {plan}

        RELEVANT CODE CONTEXT:
        {format_chunks_for_agent(search_result)}

        start by  reading the files you'll modify, then implement all changes.
        Repo directory: {self.repo_dir}""",
            silent=True,
        )

        result = self.executor.last_message(coder)["content"]
        self.log("AGENT_MESSAGE", "Coder", "Implementation complete")
        return result

    def _run_tester_stage(self, task: str) -> str:
        """Tester writes and runs tests for the new code."""

        tester = autogen.AssistantAgent(
            name="Tester",
            llm_config=get_llm_config(),
            system_message=f"""You are a test engineer
            
            REPO DIRECTORY: {self.repo_dir}

            Write pytest tests for the newly implemented code.
            Find existing test files first with list_repo_files, then write tests that:
            - Test happy path
            - Test error cases
            - Test edge cases
            -  Use mocks for external dependecies

            Run tests with: python -m pytest [test_file ] -v
            
            If tests pass: say ##TESTS_DONE##
            If source has unfixable bugs: say ##TESTS_DONE## (with_issues) and describe them            
            """,
        )
        register_github_tools(tester, self.executor)
        # register_code_tools

        test_context = search_codebase("test pytest fixture", self.task_id, n_results=5)

        self.executor.intiate_chat(
            tester,
            message=f"""Write and run tests for the recently implemented code.

            TASK THAT WAS IMPLEMENTED: {task}
            EXISTING TEST PATTERNS: {format_chunks_for_agent(test_context)}
            REPO DIRECTORY: {self.repo_dir}

            FIND the new code, write tests, and run them.""",
            silent=True,
        )

        result = self.executor.last_message(tester)["context"]
        self._log("AGENT_MESSAGE", "Tester", "Testing complete")
        return result

    def _run_reviewer_stage(self) -> str:
        """Reviewer checks all changed files."""
        reviewer = autogen.AssistantAgent(
            name="Reviewer",
            llm_config=get_llm_config(fast=True),
            system_message="""You are a security-focused code reviewer.
            
            check for :
            1. Security vulnerabilities (SQL injection, XSS, auth bypass,secrets in code)
            2: Logic errors and bugs
            3: Missing error handling
            4. Performance issues (N+1 queries, missing indexes, blocking I/O)
            5. code that doesn't follow existing patterns in the codebase

            For each issue: state the file, line, problem, and fix.

            After reviewing all changes say ##REVIEW_DONE##  and give a PASS or FAIL verdict.""",
        )

        register_github_tools(reviewer, self.executor)

        self.executor.intiate_chat(
            reviewer,
            message=f"""Review all recently modified in the repository.
            List files first, then read and review each modified one.

            REPO DIRECTORY: {self.repo_dir}""",
            silent=True,
        )

        result = self.executor.last_message(reviewer)["content"]
        self.log("Agent_MESSAGE", "Reviewer", "Review complete")
        return result

    def _run_git_stage(self, task: str, plan: str) -> str:
        """Git agent creates branch, commits, and opens PR."""

        import re

        branch_match = re.search(r"Branch name:\s*([\w\-/]+)", plan)
        branch_name = (
            branch_match.group(1) if branch_match else "nexux/task-{self.task_id[:8]}"
        )
        branch_name = branch_name.strip()

        git_agent = autogen.AssistantAgent(
            name="Git Agent",
            llm_config=get_llm_config(fast=True),
            system_message="""You are a Git operations specialist.
        Your job: create a branch, commit changes open a PR.

        Steps:
        1. create_branch with provided name
        2. commit_changes with a meaningful message
        3. commit_pull_request with detailed description
        
        write a PR description that:
        - Explains what changed and why
        - Lists all modified files
        - Mentions any new dependencies
        - Describes how to test the changes

        After PR is created, say ##PR_CREATED##""",
        )

        register_github_tools(git_agent, self.executor)

        self.executor.intiate_chat(
            git_agent,
            message=f"""Create a PR for these changes.
            ORIGINAL TASK:{task}
            REPO URL: {self.repo_url}
            REPO DIR: {self.repo_dir}
            BRANCH NAME TO CREATE: {branch_name} 
            Go through the steps: create branch -> commit -> create PR.""",
            silent=True,
        )
        result = self.executor.last_message(git_agent)["content"]
        self._log("AGENT_MESSAGE", "GIT AGENT", f"PR result: {result[:200]}")
        return result
