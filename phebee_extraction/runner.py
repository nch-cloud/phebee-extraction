"""
The stage-runner interface: how a single pipeline stage gets executed.

The pipeline only needs two things from a runner: check that the stage images are usable
(and report their digests for provenance), and run one image over one batch file. Local
Docker is the implementation in this repo; other orchestrators (a cloud batch service, a
cluster scheduler) implement the same protocol and reuse everything else.
"""

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class StageResult:
    """Outcome of running one stage over one batch"""

    status: str  # 'success' | 'failed'
    exit_code: int
    logs: str
    runtime_ms: int
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


class StageRunner(Protocol):
    """Protocol implemented by anything that can run a pipeline stage"""

    def prepare(self, images: list[str]) -> list[str]:
        """
        Verify the stage images are available.

        Returns one reference per image, in order, including a digest where one can be
        determined (``name[:tag]@sha256:...``). These feed the evidence creator fields.
        """
        ...

    def run_stage(self, image: str, input_path: str, output_path: str) -> StageResult:
        """Run ``image`` reading the batch at ``input_path`` and writing to ``output_path``"""
        ...
