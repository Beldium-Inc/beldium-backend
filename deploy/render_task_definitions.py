"""Write one ECS task definition per role (web, worker, beat) for an environment.

Staging and production are rendered from the same deploy/ecs/config.json, so
they can only differ in the values stored under their own SSM prefix, never in
shape. Every setting is injected from SSM Parameter Store; nothing
environment-specific lives in this repository.

    python deploy/render_task_definitions.py --env staging --image <ecr-uri>:<sha> \
        --account-id 123456789012 --execution-role-arn ... --task-role-arn ... --out-dir build/ecs
"""
import argparse
import json
from pathlib import Path

CONFIG = Path(__file__).with_name("ecs") / "config.json"
ENVIRONMENTS = ("staging", "production")


def render(config, *, env, image, account_id, execution_role_arn, task_role_arn):
    region = config["region"]
    ssm_prefix = config["ssm_prefix"].format(env=env)
    secrets = [
        {"name": name, "valueFrom": f"arn:aws:ssm:{region}:{account_id}:parameter{ssm_prefix}{name}"}
        for name in config["parameters"]
    ]

    definitions = {}
    for role, spec in config["roles"].items():
        environment = {"ENVIRONMENT": env, **spec["environment"]}
        container = {
            # Express Mode requires the web container to be called "Main"; the
            # other roles use the same name so one-off overrides look alike.
            "name": "Main",
            "image": image,
            "essential": True,
            "command": spec["command"],
            "environment": [{"name": k, "value": v} for k, v in sorted(environment.items())],
            "secrets": secrets,
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": config["log_group"].format(env=env),
                    "awslogs-region": region,
                    "awslogs-stream-prefix": role,
                },
            },
        }
        if "port" in spec:
            container["portMappings"] = [{"containerPort": spec["port"], "protocol": "tcp", "name": role}]
        if "stop_timeout" in spec:
            container["stopTimeout"] = spec["stop_timeout"]

        definitions[role] = {
            "family": f"{config['family_prefix']}-{env}-{role}",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "runtimePlatform": {"operatingSystemFamily": "LINUX", "cpuArchitecture": "X86_64"},
            "cpu": spec["cpu"],
            "memory": spec["memory"],
            "executionRoleArn": execution_role_arn,
            "taskRoleArn": task_role_arn,
            "containerDefinitions": [container],
        }
    return definitions


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", required=True, choices=ENVIRONMENTS)
    parser.add_argument("--image", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--execution-role-arn", required=True)
    parser.add_argument("--task-role-arn", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for role, definition in render(
        config,
        env=args.env,
        image=args.image,
        account_id=args.account_id,
        execution_role_arn=args.execution_role_arn,
        task_role_arn=args.task_role_arn,
    ).items():
        path = args.out_dir / f"{role}.json"
        path.write_text(json.dumps(definition, indent=2) + "\n")
        print(path)


if __name__ == "__main__":
    main()
