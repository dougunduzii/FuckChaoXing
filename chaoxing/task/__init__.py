from .base import BaseTask, TaskResult
from .document import DocumentTask
from .quiz import QuizTask
from .video import VideoTask

HANDLERS: dict[str, type[BaseTask]] = {
    "video": VideoTask,
    "document": DocumentTask,
    "read": DocumentTask,
    "emptypage": DocumentTask,
    "work": QuizTask,
}

__all__ = ["BaseTask", "TaskResult", "VideoTask", "DocumentTask", "QuizTask", "HANDLERS"]
