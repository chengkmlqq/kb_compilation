"""KB compilation worker package.

Task modules live under worker/tasks/ (scheduler / doc_process / wiki_graph /
agent_gateway). Task-handler registration into TASK_CLASS_REGISTRY happens on
module import; worker/celery_app.py force-imports them at app-construction time
(after `include` wiring) so the registry is populated before dispatch.
"""
