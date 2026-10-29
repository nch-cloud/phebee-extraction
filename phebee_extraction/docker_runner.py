"""Run pipeline stages as containers on the local Docker daemon."""

import logging
import os
import time
from typing import Optional

import docker

from phebee_extraction.interface import CONTAINER_DATA_DIR, build_container_environment
from phebee_extraction.models import DockerError
from phebee_extraction.runner import StageResult

logger = logging.getLogger(__name__)


def initialize_docker_client() -> docker.DockerClient:
    """Initialize and validate Docker client connection"""
    try:
        client = docker.from_env()
        client.ping()
        logger.info("Docker client initialized successfully")
        return client
    except docker.errors.DockerException as e:
        raise DockerError(f"Failed to connect to Docker daemon: {e}") from e


def _repository(image: str) -> str:
    """Repository part of an image name: strips a ':tag' but not a registry ':port'"""
    last_segment_start = image.rfind("/") + 1
    colon = image.find(":", last_segment_start)
    return image if colon == -1 else image[:colon]


def image_reference_with_digest(
    image: str, repo_digests: Optional[list[str]], image_id: Optional[str]
) -> str:
    """
    Build the provenance reference for an image: the name exactly as given, plus a digest.

    The name (including its tag) is kept verbatim because tags often carry version
    information. Docker's RepoDigests entries are 'repository@sha256:...' with no tag, so
    they are used only as a source for the digest, preferring the entry whose repository
    matches the image. Without any registry digest (e.g. a locally built image), the local
    image ID is used. Returns the bare name if no digest can be determined.
    """
    if "@" in image:
        return image  # already pinned to a digest

    digests = [d for d in (repo_digests or []) if "@" in d]
    chosen = next((d for d in digests if d.split("@", 1)[0] == _repository(image)), None)
    if chosen is None and digests:
        chosen = digests[0]
    if chosen is not None:
        return f"{image}@{chosen.split('@', 1)[1]}"

    if image_id:
        digest = image_id if image_id.startswith("sha256:") else f"sha256:{image_id}"
        return f"{image}@{digest}"

    logger.warning(f"Could not determine digest for {image}, using name only")
    return image


def validate_docker_images(client: docker.DockerClient, images: list[str]) -> list[str]:
    """
    Verify Docker images exist locally or can be pulled

    Returns one reference per image, in order: the name as given plus its digest
    (name[:tag]@sha256:...). See image_reference_with_digest.
    """
    image_digests = []

    for image in images:
        try:
            img = client.images.get(image)
            logger.info(f"Docker image found: {image}")
        except docker.errors.ImageNotFound:
            logger.warning(f"Docker image not found locally: {image}")
            logger.info(f"Attempting to pull {image}...")
            try:
                img = client.images.pull(image)
                logger.info(f"Successfully pulled {image}")
            except docker.errors.DockerException as e:
                raise DockerError(f"Failed to pull Docker image {image}: {e}") from e

        image_digests.append(
            image_reference_with_digest(image, img.attrs.get("RepoDigests"), img.id)
        )

    return image_digests


def run_single_stage(
    client: docker.DockerClient,
    docker_image: str,
    input_path: str,
    output_path: str,
    gpu: bool,
    timeout: int,
) -> StageResult:
    """Run Docker container for a single pipeline stage"""
    start_time = time.time()

    # Prepare volume mounts. The host path must be absolute: Docker reads a relative source
    # as a named volume and rejects it, which broke relative --output directories.
    temp_dir = os.path.abspath(os.path.dirname(input_path))
    volumes = {temp_dir: {"bind": CONTAINER_DATA_DIR, "mode": "rw"}}

    environment = build_container_environment(input_path, output_path)

    # Prepare device requests for GPU
    device_requests = None
    if gpu:
        device_requests = [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]

    try:
        # Run container
        container = client.containers.run(
            docker_image,
            volumes=volumes,
            environment=environment,
            device_requests=device_requests,
            detach=True,
            remove=False,  # Don't auto-remove so we can inspect exit code
        )

        # Wait for completion
        result = container.wait(timeout=timeout if timeout > 0 else None)
        exit_code = result["StatusCode"]

        # Collect logs
        logs = container.logs().decode("utf-8", errors="replace")

        # Remove container
        container.remove()

        runtime_ms = int((time.time() - start_time) * 1000)

        if exit_code == 0:
            return StageResult("success", exit_code, logs, runtime_ms)
        return StageResult(
            "failed", exit_code, logs, runtime_ms, f"Container exited with code {exit_code}"
        )

    except Exception as e:
        runtime_ms = int((time.time() - start_time) * 1000)
        return StageResult("failed", -1, "", runtime_ms, str(e))


class DockerRunner:
    """StageRunner that runs each stage as a container on the local Docker daemon"""

    def __init__(
        self,
        client: Optional[docker.DockerClient] = None,
        gpu: bool = False,
        timeout: int = 3600,
    ):
        self.client = client if client is not None else initialize_docker_client()
        self.gpu = gpu
        self.timeout = timeout

    def prepare(self, images: list[str]) -> list[str]:
        return validate_docker_images(self.client, images)

    def run_stage(self, image: str, input_path: str, output_path: str) -> StageResult:
        return run_single_stage(self.client, image, input_path, output_path, self.gpu, self.timeout)
