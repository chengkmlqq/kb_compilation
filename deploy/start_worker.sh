#!/bin/bash
set -e

# Force import all task modules to register handlers
python3 -c "
from worker.tasks import doc_process, wiki_graph, agent_gateway
from worker.tasks.scheduler import TASK_CLASS_REGISTRY
print(f'Tasks registered: {list(TASK_CLASS_REGISTRY.keys())}')
"

# Start Celery worker
exec celery -A worker.celery_app worker --loglevel=info --concurrency=2
