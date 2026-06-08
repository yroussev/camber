# Reproducible runtime for the BAS analysis tool.
#
# Python is pinned to 3.10 to match the pinned wheels in requirements.txt
# (numpy 1.26.4 / pandas 2.3.3 / pyarrow 24.0.0 all ship cp310 manylinux wheels,
# so no compiler/build tools are needed in the image).
#
# Default command runs the test suite -- building the image and letting it run is
# a clean-room proof that the tool works outside the author's machine. Override
# the command to run an example or open a shell (see DOCKER.md).
FROM python:3.10-slim

# Faster, quieter, no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so the layer caches across source edits.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Source. .dockerignore keeps reference material, caches, and data out of the
# build context (and therefore out of the image).
COPY camber ./camber
COPY tests ./tests
COPY examples ./examples

# Run as a non-root user (good hygiene for anything that may go public).
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Default: prove the build by running the suite. Override for examples/shell.
CMD ["python", "-m", "pytest", "-q"]
