from infrastructure.manager import InfrastructureManager
from infrastructure.deployment_planner import DeploymentPlanner
from infrastructure.deployment_config import DeploymentConfigGenerator


manager = InfrastructureManager()
deployment_planner = DeploymentPlanner()
config_generator = DeploymentConfigGenerator()


def infrastructure_handler(parameters=None, **kwargs):
    parameters = parameters or {}

    action = parameters.get("action")

    if not action:
        return {
            "success": False,
            "status": "invalid_request",
            "message": "No infrastructure action specified.",
        }

    action_parameters = parameters.get(
        "parameters",
        {},
    )

    confirmed = parameters.get(
        "confirmed",
        False,
    )

    # ---------------------------------------------------------
    # DEPLOYMENT PLAN
    # ---------------------------------------------------------

    if action == "create_deployment_plan":
        return deployment_planner.create_plan()

    # ---------------------------------------------------------
    # DEPLOYMENT CONFIG
    # ---------------------------------------------------------

    if action == "generate_deployment_config":
        return config_generator.generate()

    # ---------------------------------------------------------
    # WRITE CONFIG FILES
    # ---------------------------------------------------------

    if action == "write_deployment_config":

        if not confirmed:
            return {
                "success": False,
                "status": "confirmation_required",
                "message": (
                    "Writing deployment files requires "
                    "explicit confirmation."
                ),
            }

        return config_generator.write_files()

    # ---------------------------------------------------------
    # NORMAL INFRASTRUCTURE ACTION
    # ---------------------------------------------------------

    return manager.execute(
        action,
        action_parameters,
        confirmed=confirmed,
    )


TOOL = {
    "name": "infrastructure",
    "description": (
        "Manage JARVIS infrastructure, inspect the project, "
        "create deployment plans, generate deployment "
        "configuration, select cloud providers, deploy, "
        "update, restart, and manage cloud resources."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Infrastructure operation.",
                "enum": [
                    "cloud_status",
                    "list_cloud_providers",
                    "select_cloud_provider",
                    "deployment_status",
                    "prepare_deployment",
                    "create_deployment_plan",
                    "generate_deployment_config",
                    "write_deployment_config",
                    "deploy_jarvis",
                    "update_jarvis",
                    "restart_service",
                    "destroy_resource",
                ],
            },
            "parameters": {
                "type": "OBJECT",
                "description": "Parameters for the operation.",
            },
            "confirmed": {
                "type": "BOOLEAN",
                "description": (
                    "Explicit confirmation for protected actions."
                ),
            },
        },
        "required": ["action"],
    },
    "handler": infrastructure_handler,
}
