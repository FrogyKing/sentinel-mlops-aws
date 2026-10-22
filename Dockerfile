FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM public.ecr.aws/lambda/python:3.12
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR ${LAMBDA_TASK_ROOT}
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt > /tmp/requirements.txt && uv pip install --system --no-cache -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY src/sentinel ./sentinel
ENV SENTINEL_ARTIFACT_DIR=/tmp/sentinel
CMD ["sentinel.api.handler"]
