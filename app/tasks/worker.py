import os
import time
import json
import shutil
from datetime import datetime
from celery import Celery
from app.config import settings
from app.streaming.websocket import publish_event
from app.tasks.models import TaskStatus
celery_app=Celery(
    'nexus',
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)


celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@celery_app.task(bind=True,max_retries=0,name='nexus.run_task')
def run_task(self,task_id:str,user_id:str,repo_url:str,task_description:str):
    """
    MAIN celery task. Runs the entire piepline
    """
    from app.rag.indexer import index_repository
    from app.tools.github_tools import clone_repository
    from app.agents.orchestrator import NexusOrchestrator
    import re

    repo_dir=os.path.join(settings.REPOS_DIR,task_id)
    start_time=time.time()

    def log(event_type:str,agent:str,message:str,data:dict=None):
        publish_event(task_id,event_type,agent,message,data)


    def update_db_status(status:str,progress:int,stage:str=None, **kwargs):
        """Update task status in PostgreSQL synchronously."""
        import psycopg2
        from app.config import settings as s
        try:
            db_url = s.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
            conn=psycopg2.connect(db_url)
            cur=conn.cursor()
            sets=['status= %s',"progress_percent = %s"]
            vals=[status,progress]
            if stage:
                sets.append("current_stage = %s")
                vals.append(stage)
            for k,v in kwargs.items():
                sets.append(f"{k} = %s")
                vals.append(v)
            vals.append(task_id)
            cur.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s::uuid", vals)
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"DB update error: {e}")
            raise

    try:
        log('STAGE_START',"system",f"Cloning repository: {repo_url}")
        update_db_status(TaskStatus.CLONING,5,"Cloning repository")
        clone_repository(repo_url,repo_dir)
        log("STAGE_COMPLETE",'system','Repository cloned successfully')

        # index
        log('START_STAGE','system','Indexing codebase with semantic embedding....')
        update_db_status(TaskStatus.INDEXING,15,'INDEXING CODEBASE')

        def index_progress(pct,msg):
            log("PROGRESS",'Indexer',msg,{"percent":pct})

        chunk_count=index_repository(repo_url,task_id,progress_callback=index_progress)
        log("STAGE_COMPLETE", "system", f"Indexed {chunk_count} code chunks into vector DB")

        # run orchestrator
        update_db_status(TaskStatus.PLANNING,25,"planning implementation")

        def agent_log_callback(event_type:str,agent:str,message:str):
            log(event_type,agent,message)

        orchestrator=NexusOrchestrator(
            task_id=task_id,
            repo_dir=repo_dir,
            repo_url=repo_url,
            log_callback=agent_log_callback
        )

        update_db_status(TaskStatus.PLANNING,30)
        results=orchestrator.run(task_description)

        pr_url = None
        pr_number = None
        pr_text = results.get("pr", "")
        url_match = re.search(r'https://github\.com/\S+/pull/\d+', pr_text)
        num_match = re.search(r'PR #(\d+)', pr_text)
        if url_match:
            pr_url = url_match.group(0)
        if num_match:
            pr_number = int(num_match.group(1))

        
        changed_files = []

        if os.path.exists(repo_dir):
            import git as gitpython
            try:
                repo = gitpython.Repo(repo_dir)
                changed_files = [item.a_path for item in repo.index.diff("HEAD~1")]
            except Exception:
                pass

        duration=int(time.time()-start_time)

        update_db_status(
            TaskStatus.COMPLETE, 100, "Complete",
            pr_url=pr_url,
            pr_number=pr_number,
            files_changed=json.dumps(changed_files),
            completed_at=datetime.utcnow().isoformat(),
            duration_seconds=duration,
        )

        log("COMPLETE", "system", f"Task complete! Duration: {duration}s. PR: {pr_url or 'N/A'}", {
            "pr_url": pr_url,
            "duration": duration,
            "files_changed": changed_files,
        })

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        print(f"[TASK FAILED] {error_msg}")

        log("FAILED", "system", f"Task failed: {str(e)}")
        update_db_status(TaskStatus.FAILED, 0, "Failed", error_message=str(e)[:2000])
        raise

    finally:
        if os.path.exists(repo_dir):
            try:
                shutil.rmtree(repo_dir)
            except Exception:
                pass




