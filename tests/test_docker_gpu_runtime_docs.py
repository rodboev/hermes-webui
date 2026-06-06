from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
DOCKER_DOCS = (REPO / "docs" / "docker.md").read_text(encoding="utf-8")


def test_dockerfile_gpu_libraries_are_opt_in():
    """The production image must stay CPU-only unless the GPU build arg is set."""
    assert "ARG INSTALL_GPU_LIBS=0" in DOCKERFILE
    assert 'if [ "$INSTALL_GPU_LIBS" = "1" ]' in DOCKERFILE

    opt_in_block = DOCKERFILE[DOCKERFILE.index("ARG INSTALL_GPU_LIBS=0"):]
    for package in (
        "libva2",
        "vainfo",
        "mesa-va-drivers",
        "intel-media-va-driver-non-free",
    ):
        assert package in opt_in_block, (
            f"{package} must only appear in the INSTALL_GPU_LIBS opt-in block."
        )
        assert package not in DOCKERFILE[:DOCKERFILE.index("ARG INSTALL_GPU_LIBS=0")]


def test_dockerfile_handles_missing_intel_non_free_driver():
    """Debian slim repos may not expose the non-free Intel VA-API package."""
    assert "apt-cache show intel-media-va-driver-non-free" in DOCKERFILE
    assert "skipping Intel non-free VA-API driver" in DOCKERFILE


def test_docker_docs_show_gpu_build_command():
    assert "Optional GPU runtime image" in DOCKER_DOCS
    assert "--build-arg INSTALL_GPU_LIBS=1" in DOCKER_DOCS
    assert "default Hermes WebUI Docker image stays CPU-only" in DOCKER_DOCS


def test_docker_docs_cover_intel_amd_dri_mapping():
    assert "Intel and AMD VA-API" in DOCKER_DOCS
    assert "--device /dev/dri:/dev/dri" in DOCKER_DOCS
    assert "/dev/dri:/dev/dri" in DOCKER_DOCS
    assert "group_add:" in DOCKER_DOCS
    assert "video" in DOCKER_DOCS
    assert "render" in DOCKER_DOCS
    assert "vainfo" in DOCKER_DOCS


def test_docker_docs_cover_nvidia_host_runtime_guidance():
    assert "NVIDIA Container Toolkit" in DOCKER_DOCS
    assert "--gpus all" in DOCKER_DOCS
    assert "gpus: all" in DOCKER_DOCS
    assert "host NVIDIA driver" in DOCKER_DOCS
    assert "host kernel drivers" in DOCKER_DOCS
    assert "NVIDIA runtime" in DOCKER_DOCS


def test_docker_docs_do_not_claim_native_gpu_passthrough_verification():
    assert "not a claim that native GPU passthrough was verified" in DOCKER_DOCS
    assert "depends on host drivers" in DOCKER_DOCS
