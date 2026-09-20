FROM --platform=linux/amd64 python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    JARVIS_HOST=0.0.0.0 \
    JARVIS_DATA_DIR=/var/lib/jarvis

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin jarvis \
    && mkdir -p /var/lib/jarvis \
    && chown -R jarvis:jarvis /var/lib/jarvis

COPY requirements-cloud.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-cloud.txt

# The cloud process needs the planner, action declarations, API, and served
# HUD. The desktop main.py and Mac Agent are intentionally not copied or run.
COPY --chown=jarvis:jarvis cloud/ cloud/
COPY --chown=jarvis:jarvis automation/autonomous_engine.py automation/autonomous_planner.py automation/learning_memory.py automation/permission_manager.py automation/simple_intent_router.py automation/
COPY --chown=jarvis:jarvis actions/ actions/
COPY --chown=jarvis:jarvis core/__init__.py core/action_loader.py core/confirm.py core/runtime_config.py core/undo.py core/
COPY --chown=jarvis:jarvis config/__init__.py config/
COPY --chown=jarvis:jarvis infrastructure/ infrastructure/
COPY --chown=jarvis:jarvis dashboard/static/ dashboard/static/

USER jarvis

EXPOSE 8080

# exec keeps uvicorn as PID 1 so Docker/Blitz SIGTERM reaches it directly.
CMD ["sh", "-c", "exec python -m uvicorn cloud.server:app --host 0.0.0.0 --port \"${PORT:-8080}\""]
