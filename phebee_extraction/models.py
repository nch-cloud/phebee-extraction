"""Shared data structures and exceptions."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class BatchInfo:
    """Metadata for a single batch"""

    batch_num: int
    batch_id: str  # e.g., "batch_000001"
    input_path: str
    output_path: str
    record_count: int
    status: str = "pending"  # 'pending'|'running'|'success'|'failed'
    error_message: Optional[str] = None
    runtime_ms: Optional[int] = None


class ExtractionError(Exception):
    """Base exception for extraction errors"""

    pass


class BatchProcessingError(ExtractionError):
    """Exception raised when batch processing fails"""

    def __init__(self, batch: BatchInfo, message: str):
        self.batch = batch
        self.message = message
        super().__init__(f"Batch {batch.batch_id} failed: {message}")


class DockerError(ExtractionError):
    """Exception raised for Docker-related errors"""

    pass
