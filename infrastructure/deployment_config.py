from __future__ import annotations

from pathlib import Path
import json


class DeploymentConfigGenerator:
    """
    Generates deployment configuration for JARVIS.

    This module ONLY creates configuration files.
    It does not deploy anything and does not create
    cloud resources.
    """

    def __init__(self, project_root=None):
        self.project_root = Path(
            project_root
            or Path(__file__).resolve().parent.parent
        )

        self.infrastructure_dir = (
            self.project_root / "infrastructure"
        )

    # ---------------------------------------------------------
    # PROJECT INSPECTION
    # ---------------------------------------------------------

    def inspect(self):
        files = {
            "main": self.project_root / "main.py",
            "requirements": self.project_root / "requirements.txt",
            "server": self.project_root / "cloud" / "server.py",
        }

        return {
            name: {
                "exists": path.exists(),
                "path": str(path),
            }
            for name, path in files.items()
        }

    # ---------------------------------------------------------
    # CLOUD RUN CONFIG
    # ---------------------------------------------------------

    def cloud_run_config(self):
        return {
            "service": "jarvis-cloud",
            "region": "asia-south1",
            "runtime": "python",
            "port": 8000,
            "min_instances": 0,
            "max_instances": 1,
            "deployment_enabled": False,
        }

    # ---------------------------------------------------------
    # GENERATE DOCKERFILE
    # ---------------------------------------------------------

    def generate_dockerfile(self):
        return """FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "cloud.server:app", "--host", "0.0.0.0", "--port", "8000"]
"""

    # ---------------------------------------------------------
    # GENERATE DEPLOYMENT CONFIG
    # ---------------------------------------------------------

    def generate(self):
        inspection = self.inspect()

        return {
            "success": True,
            "status": "configuration_generated",
            "project_root": str(self.project_root),
            "inspection": inspection,
            "cloud_run": self.cloud_run_config(),
            "dockerfile": self.generate_dockerfile(),
            "deployment_started": False,
            "requires_approval": True,
        }

    # ---------------------------------------------------------
    # WRITE CONFIGURATION FILES
    # ---------------------------------------------------------

    def write_files(self):
        self.infrastructure_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        dockerfile_path = (
            self.project_root / "Dockerfile"
        )

        config_path = (
            self.infrastructure_dir
            / "cloud_run_config.json"
        )

        dockerfile_path.write_text(
            self.generate_dockerfile(),
            encoding="utf-8",
        )

        config_path.write_text(
            json.dumps(
                self.cloud_run_config(),
                indent=2,
            ),
            encoding="utf-8",
        )

        return {
            "dockerfile": str(
                dockerfile_path
            ),
            "cloud_run_config": str(
                config_path
            ),
            "deployment_started": False,
        }


# ---------------------------------------------------------
# TEST
# ---------------------------------------------------------

if __name__ == "__main__":

    generator = DeploymentConfigGenerator()

    result = generator.generate()

    print()
    print("========================================")
    print("     JARVIS DEPLOYMENT CONFIG")
    print("========================================")

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    print()
    print(
        "No cloud resources were created."
    )
