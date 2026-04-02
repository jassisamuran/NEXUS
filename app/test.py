# from app.tasks.worker import run_task
# run_task.delay("test-task-001", "user-001",
#     "https://github.com/pallets/flask",
#     "Add a health check endpoint")
print("now")
from app.tasks.workers import run_task
run_task.delay('test-task-001',
'user-001',"https://github.com/pallets/flask",
'Add a health check endpoint')

