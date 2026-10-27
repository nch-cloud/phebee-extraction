"""Run pipeline stages as containers on the local Docker daemon."""

import logging
import os
import time
from typing import List, Optional

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


def validate_docker_images(client: docker.DockerClient, images: List[str]) -> List[str]:
    """
    Verify Docker images exist locally or can be pulled

    Returns list of image references with digests (image@sha256:...)
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

        # Get the image digest (RepoDigests is a list, take first if available)
        if img.attrs.get("RepoDigests"):
            # Use the full digest reference from registry
            digest_ref = img.attrs["RepoDigests"][0]
            image_digests.append(digest_ref)
        elif img.id:
            # Fall back to local image ID if no registry digest
            # Format: image@sha256:...
            image_id = img.id.replace("sha256:", "")
            digest_ref = f"{image}@sha256:{image_id}"
            image_digests.append(digest_ref)
        else:
            # Should never happen, but fallback to original name
            logger.warning(f"Could not determine digest for {image}, using name only")
            image_digests.append(image)

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

    # Prepare volume mounts
    temp_dir = os.path.dirname(input_path)
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

    def prepare(self, images: List[str]) -> List[str]:
        return validate_docker_images(self.client, images)

    def run_stage(self, image: str, input_path: str, output_path: str) -> StageResult:
        return run_single_stage(self.client, image, input_path, output_path, self.gpu, self.timeout)
