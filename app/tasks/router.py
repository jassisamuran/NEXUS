from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel
import uuid
from app.database.connection import get_db
from app.tasks.models import Task, TaskLog, TaskStatus
from app.auth.models import User
from app.auth.dependencies import get_current_user
from app.tasks.worker import run_task
from app.streaming.websocket import ws_manager

router = APIRouter(prefix="/tasks", tags=["tasks"])

class CreateTaskRequest(BaseModel):
    repo_url: str
    task_description: str

@router.post("/", status_code=202)
async def create_task(
    req: CreateTaskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if "github.com" not in req.repo_url:
        raise HTTPException(status_code=400, detail="Only GitHub repositories supported")

    result = await db.execute(
        select(Task).where(
            Task.user_id == user.id,
            Task.status.in_([TaskStatus.QUEUED, TaskStatus.CLONING,
                              TaskStatus.INDEXING, TaskStatus.PLANNING,
                              TaskStatus.CODING, TaskStatus.TESTING])
        )
    )
    running = result.scalars().all()
    if len(running) >= 2:
        raise HTTPException(status_code=429, detail="Maximum 2 concurrent tasks allowed")

    task = Task(
        user_id=user.id,
        repo_url=req.repo_url,
        task_description=req.task_description,
        status=TaskStatus.QUEUED,
    )
    db.add(task)
    await db.flush()

    celery_result = run_task.delay(
        str(task.id),
        str(user.id),
        req.repo_url,
        req.task_description,
    )
    task.celery_task_id = celery_result.id

    user.total_tasks += 1

    return {
        "task_id": str(task.id),
        "status": "queued",
        "message": "Task queued. Connect to WebSocket for live updates.",
        "websocket_url": f"/ws/{task.id}",
    }

@router.get("/")
async def list_tasks(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Task)
        .where(Task.user_id == user.id)
        .order_by(desc(Task.created_at))
        .limit(20)
    )
    tasks = result.scalars().all()
    return {
        "tasks": [
            {
                "id": str(t.id),
                "repo_url": t.repo_url,
                "task_description": t.task_description[:100],
                "status": t.status,
                "progress": t.progress_percent,
                "pr_url": t.pr_url,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "duration_seconds": t.duration_seconds,
            }
            for t in tasks
        ]
    }

@router.get("/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "id": str(task.id),
        "repo_url": task.repo_url,
        "task_description": task.task_description,
        "status": task.status,
        "progress": task.progress_percent,
        "current_stage": task.current_stage,
        "pr_url": task.pr_url,
        "pr_number": task.pr_number,
        "files_changed": task.files_changed,
        "error_message": task.error_message,
        "tokens_used": task.tokens_used,
        "cost_usd": task.cost_usd,
        "duration_seconds": task.duration_seconds,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }

@router.websocket("/ws/{task_id}")
async def task_websocket(websocket: WebSocket, task_id: str):
    """Stream live agent activity for a task via WebSocket."""
    await ws_manager.connect(websocket, task_id)
    try:
        await ws_manager.listen_and_forward(task_id, websocket)
    except WebSocketDisconnect: 
        ws_manager.disconnect(websocket, task_id)