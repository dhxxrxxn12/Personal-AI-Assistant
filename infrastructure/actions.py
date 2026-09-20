from infrastructure.manager import InfrastructureManager


manager = InfrastructureManager()


TOOL = {
    "name": "infrastructure",
    "description": (
        "Manage JARVIS cloud infrastructure, deployment, "
        "updates, and service status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "cloud_status",
                    "deployment_status",
                    "prepare_deployment",
                    "update_jarvis",
                    "restart_service",
                ],
            },
            "parameters": {
                "type": "object",
            },
            "confirmed": {
                "type": "boolean",
            },
        },
        "required": ["action"],
    },
}


def run(
    action,
    parameters=None,
    confirmed=False,
):
    return manager.execute(
        action,
        parameters or {},
        confirmed=confirmed,
    )
