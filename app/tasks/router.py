from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc,func
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


@router.get("/analytics/costs")
async def get_cost_analytics(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(
            func.date(Task.created_at).label("date"),
            func.sum(Task.cost_usd).label("total_cost"),
            func.sum(Task.tokens_used).label("total_tokens"),
            func.count(Task.id).label("task_count"),
            func.avg(Task.duration_seconds).label("avg_duration"),
        )
        .where(Task.user_id == user.id)
        .group_by(func.date(Task.created_at))
        .order_by(func.date(Task.created_at).desc())
        .limit(30)
    )
    rows = result.fetchall()
    return {
        "daily": [
            {
                "date": str(row.date),
                "cost": round(float(row.total_cost or 0), 4),
                "tokens": row.total_tokens or 0,
                "tasks": row.task_count,
                "avg_duration": round(float(row.avg_duration or 0)),
            }
            for row in rows
        ],
        "total_cost": sum(r.total_cost or 0 for r in rows),
        "total_tasks": sum(r.task_count for r in rows),
    }

@router.post("/{task_id}/retry")
async def retry_task(
    task_id:str,
    db:AsyncSession=Depends(get_db),
    user:User=Depends(get_current_db)
):
    task=await get_task_by_id(db,task_id,user.id)
    if not task:
        raise HttpException(404,"Task not found")
    if task.status!=TaskStatus.FAILED:
        raise HTTPException(400,'Only failed tasks can be retried')

    task.status=TaskStatus.QUEUED
    task.progress_percent=0
    task.error_message=None
    task.pr_url=None
    task.started_at=None
    task.completed_at=None
    await db.flush()

    celery_result=run_task.delay(
        str(task.id),
        str(user.id),
        task.repo_url,
        task.task_description,
    )
    task.celery_task_id=celery_result.id

    return {"task_id":str(task.id),"status":"retrying"}