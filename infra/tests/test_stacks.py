"""Deployment design §4, §6.1, §6.9 and the foundation portion of §9."""

import json

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template
from mothusi_infra.budget import MothusiBudget
from mothusi_infra.ci import MothusiCi
from mothusi_infra.data import MothusiData
from mothusi_infra.network import AZS, MothusiNetwork
from mothusi_infra.stage import PRODUCTION

A = PRODUCTION.adopted  # the stem of every name AWS can't change (stage.py)


@pytest.fixture(scope="module")
def stacks():
    app = App()
    env = Environment(region="af-south-1")
    instances = [
        cls(app, cls.__name__, stage=PRODUCTION, env=env)
        for cls in (MothusiNetwork, MothusiData, MothusiCi)
    ]
    return {stack.stack_name: Template.from_stack(stack) for stack in instances}


def resources(template, kind):
    return template.find_resources(kind)


def test_network_topology(stacks):
    t = stacks["MothusiNetwork"]
    t.resource_count_is("AWS::EC2::NatGateway", 0)
    t.resource_count_is("AWS::EC2::VPC", 1)
    t.has_resource_properties("AWS::EC2::VPC", {"CidrBlock": "10.40.0.0/16"})
    subnets = resources(t, "AWS::EC2::Subnet")
    assert len(subnets) == 2
    assert {s["Properties"]["AvailabilityZone"] for s in subnets.values()} == {
        "af-south-1a",
        "af-south-1b",
    }
    assert all(s["Properties"]["MapPublicIpOnLaunch"] for s in subnets.values())
    t.resource_count_is("AWS::EC2::InternetGateway", 1)
    t.has_resource_properties("AWS::EC2::VPCEndpoint", {"VpcEndpointType": "Gateway"})
    t.has_resource_properties(
        "AWS::ServiceDiscovery::PrivateDnsNamespace", {"Name": f"{A}.internal"}
    )


def test_security_groups_have_exact_ingress(stacks):
    t = stacks["MothusiNetwork"]
    groups = resources(t, "AWS::EC2::SecurityGroup")
    ids = {g["Properties"]["GroupName"]: key for key, g in groups.items()}
    assert set(ids) == {"alb-sg", "service-sg", "model-sg", "db-sg"}
    rules = []
    for key, group in groups.items():
        rules.extend(
            {"GroupId": {"Fn::GetAtt": [key, "GroupId"]}, **rule}
            for rule in group["Properties"].get("SecurityGroupIngress", [])
        )
    rules.extend(r["Properties"] for r in resources(t, "AWS::EC2::SecurityGroupIngress").values())
    assert len(rules) == 5
    expected = {
        ("service-sg", "alb-sg", 8000),
        ("service-sg", "service-sg", 8000),
        ("model-sg", "service-sg", 11434),
        ("db-sg", "service-sg", 5432),
    }
    for target, source, port in expected:
        assert any(
            rule.get("GroupId") == {"Fn::GetAtt": [ids[target], "GroupId"]}
            and rule.get("SourceSecurityGroupId") == {"Fn::GetAtt": [ids[source], "GroupId"]}
            and rule["FromPort"] == rule["ToPort"] == port
            and rule["IpProtocol"] == "tcp"
            for rule in rules
        )
    alb_rules = [r for r in rules if r["GroupId"] == {"Fn::GetAtt": [ids["alb-sg"], "GroupId"]}]
    assert len(alb_rules) == 1
    assert alb_rules[0]["FromPort"] == alb_rules[0]["ToPort"] == 80
    assert alb_rules[0]["SourcePrefixListId"] == {"Ref": "CloudFrontPrefixListId"}
    assert all("CidrIp" not in r and "CidrIpv6" not in r for r in rules)


def test_data_encryption_retention_and_database(stacks):
    t = stacks["MothusiData"]
    keys = resources(t, "AWS::KMS::Key")
    assert len(keys) == 1
    key_id = next(iter(keys))
    key_arn = {"Fn::GetAtt": [key_id, "Arn"]}
    t.has_resource_properties("AWS::KMS::Alias", {"AliasName": "alias/mothusi-data"})
    t.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})
    buckets = resources(t, "AWS::S3::Bucket")
    assert len(buckets) == 6
    for bucket in buckets.values():
        p = bucket["Properties"]
        assert p["PublicAccessBlockConfiguration"] == dict.fromkeys(
            ["BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"],
            True,
        )
        console = f"{A}-console-" in json.dumps(p["BucketName"])
        encryption = p["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
            "ServerSideEncryptionByDefault"
        ]
        assert encryption["SSEAlgorithm"] == ("AES256" if console else "aws:kms")
        if not console:
            assert encryption["KMSMasterKeyID"] == key_arn
        assert bucket["DeletionPolicy"] == bucket["UpdateReplacePolicy"] == "Retain"
        assert "CorsConfiguration" not in p
        if f"{A}-uploads-" in json.dumps(p["BucketName"]):
            assert p["LifecycleConfiguration"]["Rules"][0]["ExpirationInDays"] == 30
    t.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "DBInstanceIdentifier": "mothusi-db",
            "Engine": "postgres",
            "EngineVersion": "17",
            "DBInstanceClass": "db.t4g.small",
            "AllocatedStorage": "20",
            "StorageType": "gp3",
            "MultiAZ": False,
            "PubliclyAccessible": False,
            "StorageEncrypted": True,
            "KmsKeyId": key_arn,
            "MasterUserPassword": Match.any_value(),
            "VPCSecurityGroups": [Match.any_value()],
        },
    )
    db = next(iter(resources(t, "AWS::RDS::DBInstance").values()))
    assert db["DeletionPolicy"] == db["UpdateReplacePolicy"] == "Retain"
    assert f"/{A}/sg/db" in json.dumps(t.to_json())
    assert next(iter(keys.values()))["DeletionPolicy"] == "Retain"


def test_secrets_repositories_trail(stacks):
    t = stacks["MothusiData"]
    t.has_resource_properties("AWS::SecretsManager::Secret", {"Name": f"{A}/elevenlabs"})
    t.has_resource_properties(
        "AWS::SecretsManager::Secret",
        {
            "Name": f"{A}/note-key",
            "GenerateSecretString": {"PasswordLength": 48, "ExcludePunctuation": True},
        },
    )
    t.resource_count_is("AWS::SecretsManager::RotationSchedule", 0)
    for name in ("core", "intake", "knowledge"):
        t.has_resource_properties(
            "AWS::ECR::Repository",
            {
                "RepositoryName": f"{A}-{name}",
                "LifecyclePolicy": {
                    "LifecyclePolicyText": Match.serialized_json(
                        {
                            "rules": [
                                Match.object_like(
                                    {
                                        "selection": {
                                            "tagStatus": "any",
                                            "countType": "imageCountMoreThan",
                                            "countNumber": 10,
                                        },
                                        "action": {"type": "expire"},
                                    }
                                )
                            ]
                        }
                    )
                },
            },
        )
    t.has_resource_properties(
        "AWS::CloudTrail::Trail",
        {
            "TrailName": A,
            "IsLogging": True,
            "EventSelectors": [{"IncludeManagementEvents": True, "ReadWriteType": "All"}],
        },
    )


def test_budget_thresholds_and_email_context(stacks):
    stacks["MothusiData"].resource_count_is("AWS::Budgets::Budget", 0)  # not in af-south-1
    us_east = Environment(region="us-east-1")
    budget_t = Template.from_stack(MothusiBudget(App(), "MothusiBudget", env=us_east))
    budget = next(iter(resources(budget_t, "AWS::Budgets::Budget").values()))
    p = budget["Properties"]
    assert p["Budget"] == {
        "BudgetName": "mothusi-monthly",
        "BudgetType": "COST",
        "TimeUnit": "MONTHLY",
        "BudgetLimit": {"Amount": 60, "Unit": "USD"},
    }
    alerts = p["NotificationsWithSubscribers"]
    assert {
        (a["Notification"]["NotificationType"], a["Notification"]["Threshold"]) for a in alerts
    } == {("ACTUAL", 50), ("ACTUAL", 80), ("ACTUAL", 100), ("FORECASTED", 100)}
    app = App(context={"budgetEmail": "ops@example.org"})
    t = Template.from_stack(MothusiBudget(app, "EmailTest", env=us_east))
    for entry in next(iter(resources(t, "AWS::Budgets::Budget").values()))["Properties"][
        "NotificationsWithSubscribers"
    ]:
        assert entry["Subscribers"] == [{"Address": "ops@example.org", "SubscriptionType": "EMAIL"}]


def test_exact_ssm_contract_and_no_exports(stacks):
    expected = {
        "MothusiNetwork": {
            "vpc-id",
            "subnet-ids",
            "sg/alb",
            "sg/service",
            "sg/model",
            "sg/db",
            "namespace-id",
        },
        "MothusiData": {
            "kms-key-arn",
            "bucket/uploads",
            "bucket/bundles",
            "bucket/console",
            "bucket/exhibits",
            "bucket/curriculum",
            "db/endpoint",
            "db/secret-arn",
            "ecr/core",
            "ecr/intake",
            "ecr/knowledge",
            "secret/elevenlabs-arn",
        },
        "MothusiCi": {"ci/deploy-role-arn"},
    }
    for name, t in stacks.items():
        assert {r["Properties"]["Name"] for r in resources(t, "AWS::SSM::Parameter").values()} == {
            f"/{A}/{suffix}" for suffix in expected[name]
        }
        assert "Fn::ImportValue" not in json.dumps(t.to_json())
        assert all("Export" not in o for o in t.to_json().get("Outputs", {}).values())
        assert "ami-" not in json.dumps(t.to_json())


def test_ci_trust_and_permissions(stacks):
    t = stacks["MothusiCi"]
    t.resource_count_is("AWS::IAM::OIDCProvider", 0)  # the account's existing provider is imported
    assert "oidc-provider/token.actions.githubusercontent.com" in json.dumps(t.to_json())
    created = Template.from_stack(
        MothusiCi(
            App(context={"createGithubOidcProvider": "true"}),
            "CiCreate",
            stage=PRODUCTION,
            env=Environment(region="af-south-1"),
        )
    )
    created.has_resource_properties(
        "AWS::IAM::OIDCProvider",
        {
            "Url": "https://token.actions.githubusercontent.com",
            "ClientIdList": ["sts.amazonaws.com"],
        },
    )
    role = next(
        r["Properties"]
        for r in resources(t, "AWS::IAM::Role").values()
        if r["Properties"]["RoleName"] == "mothusi-github-release"
    )
    trust = role["AssumeRolePolicyDocument"]["Statement"]
    assert len(trust) == 1
    assert trust[0]["Action"] == "sts:AssumeRoleWithWebIdentity"
    condition = trust[0]["Condition"]["StringEquals"]
    assert condition["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    assert condition["token.actions.githubusercontent.com:sub"] == {
        "Fn::Join": ["", ["repo:", {"Ref": "GitHubRepository"}, ":environment:production"]]
    }
    policies = json.dumps(resources(t, "AWS::IAM::Policy"))
    for action in (
        "ecr:PutImage",
        "sts:AssumeRole",
        "cloudformation:*",
        "s3:ListBucket",
        "s3:PutObject",
        "s3:DeleteObject",
        "cloudfront:CreateInvalidation",
    ):
        assert action in policies
    # A failed release prints its own diagnostics: read-only ECS and the services' log groups.
    for action in ("ecs:DescribeServices", "ecs:DescribeTasks", "logs:FilterLogEvents"):
        assert action in policies
    assert "log-group:/mothusi/*" in policies
    assert "ecs:UpdateService" not in policies and "logs:Delete" not in policies
    assert "iam:PassRole" not in policies
    assert "AdministratorAccess" not in policies


@pytest.fixture(scope="module")
def front():
    from mothusi_infra.front import MothusiFront

    return Template.from_stack(
        MothusiFront(App(), "MothusiFront", stage=PRODUCTION, env=Environment(region="af-south-1"))
    )


def service_template(services="core"):
    from mothusi_infra.service import MothusiService

    return Template.from_stack(
        MothusiService(
            App(context={"services": services, "imageTag": "test"}),
            "MothusiService",
            stage=PRODUCTION,
            env=Environment(region="af-south-1"),
        )
    )


def properties(template, kind):
    return [r["Properties"] for r in resources(template, kind).values()]


def test_front_routes_and_origin_access(front):
    config = properties(front, "AWS::CloudFront::Distribution")[0]["DistributionConfig"]
    behaviors = {b["PathPattern"]: b for b in config["CacheBehaviors"]}
    assert set(behaviors) == {"/api/*", "/n/*"}
    api, note = behaviors["/api/*"], behaviors["/n/*"]
    assert api["TargetOriginId"] == note["TargetOriginId"]
    for behavior in (api, note):
        assert behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
        assert behavior["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac"
        assert behavior["ViewerProtocolPolicy"] == "redirect-to-https"
        assert set(behavior["AllowedMethods"]) == {
            "GET",
            "HEAD",
            "OPTIONS",
            "PUT",
            "PATCH",
            "POST",
            "DELETE",
        }
    assert not note.get("FunctionAssociations") and not note.get("LambdaFunctionAssociations")
    assert api["FunctionAssociations"][0]["EventType"] == "viewer-request"
    origin = next(o for o in config["Origins"] if o["Id"] == note["TargetOriginId"])
    assert not origin.get("OriginPath")
    assert origin["CustomOriginConfig"]["OriginProtocolPolicy"] == "http-only"
    assert origin["CustomOriginConfig"]["OriginReadTimeout"] == 60
    assert origin["OriginCustomHeaders"][0]["HeaderName"] == "X-Mothusi-Origin"
    default = config["DefaultCacheBehavior"]
    assert default["TargetOriginId"] != note["TargetOriginId"]
    assert default["FunctionAssociations"][0]["EventType"] == "viewer-request"
    front.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    assert "cloudfront.amazonaws.com" in json.dumps(
        properties(front, "AWS::CloudFormation::CustomResource")
    )
    assert "AWS:SourceArn" in json.dumps(properties(front, "AWS::CloudFormation::CustomResource"))
    front.resource_count_is("AWS::EC2::SecurityGroupIngress", 0)
    front.resource_count_is("AWS::EC2::SecurityGroup", 0)


def test_front_listener_and_target_ownership(front):
    alb = properties(front, "AWS::ElasticLoadBalancingV2::LoadBalancer")[0]
    assert alb["Scheme"] == "internet-facing"
    assert {x["Key"]: x["Value"] for x in alb["LoadBalancerAttributes"]}[
        "idle_timeout.timeout_seconds"
    ] == "3600"
    listener = properties(front, "AWS::ElasticLoadBalancingV2::Listener")[0]
    assert listener["Port"] == 80 and listener["Protocol"] == "HTTP"
    assert listener["DefaultActions"] == [
        {"Type": "fixed-response", "FixedResponseConfig": {"StatusCode": "403"}}
    ]
    rules = properties(front, "AWS::ElasticLoadBalancingV2::ListenerRule")
    assert len(rules) == 1
    condition = rules[0]["Conditions"]
    assert (
        len(condition) == 1
        and condition[0]["HttpHeaderConfig"]["HttpHeaderName"] == "X-Mothusi-Origin"
    )
    config = properties(front, "AWS::CloudFront::Distribution")[0]["DistributionConfig"]
    header = next(o for o in config["Origins"] if "CustomOriginConfig" in o)["OriginCustomHeaders"][
        0
    ]
    assert condition[0]["HttpHeaderConfig"]["Values"] == [header["HeaderValue"]]
    target = properties(front, "AWS::ElasticLoadBalancingV2::TargetGroup")[0]
    assert target["Port"] == 8000 and target["TargetType"] == "ip"
    assert target["HealthCheckPath"] == "/health/live" and target["HealthCheckProtocol"] == "HTTP"
    assert f"/{A}/front/core-target-group-arn" in json.dumps(front.to_json())


def test_cors_secret_and_cognito(front):
    calls = properties(front, "Custom::AWS")
    cors = next(c for c in calls if "putBucketCors" in json.dumps(c))
    for event in ("Create", "Update"):
        text = json.dumps(cors[event])
        for required in ("AllowedOrigins", "https://", "PUT", "Content-Type", "3000"):
            assert required in text
        assert "localhost" not in text
    assert f"/{A}/bucket/uploads" in json.dumps(front.to_json())
    writers = properties(front, "AWS::Lambda::Function")
    secret_writer = next(
        p for p in writers if "SecureString" in p.get("Code", {}).get("ZipFile", "")
    )
    secret_code = secret_writer["Code"]["ZipFile"]
    assert "process.env.PARAMETER_NAME" in secret_code
    assert secret_writer["Environment"]["Variables"] == {
        "PARAMETER_NAME": f"/{A}/front/origin-secret"
    }
    assert "NoEcho: true" in secret_code
    assert "randomBytes(36)" in secret_code
    front.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "UserPoolName": "mothusi-teachers",
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        },
    )
    client = properties(front, "AWS::Cognito::UserPoolClient")[0]
    assert client["GenerateSecret"] is False and client["AllowedOAuthFlows"] == ["code"]
    assert "/auth/callback" in json.dumps(client["CallbackURLs"])
    assert "localhost" not in json.dumps(client)


def test_dev_cors_is_opt_in():
    from mothusi_infra.front import MothusiFront

    t = Template.from_stack(
        MothusiFront(
            App(context={"devCors": True}),
            "DevFront",
            stage=PRODUCTION,
            env=Environment(region="af-south-1"),
        )
    )
    cors = next(c for c in properties(t, "Custom::AWS") if "putBucketCors" in json.dumps(c))
    assert "http://localhost:5173" in json.dumps(cors)


def test_functions_execute_without_mutating_queries(front):
    import subprocess

    functions = properties(front, "AWS::CloudFront::Function")
    strip = next(f["FunctionCode"] for f in functions if f["Name"] == "strip-api-prefix")
    spa = next(f["FunctionCode"] for f in functions if f["Name"] == "spa-rewrite")
    for code, cases in (
        (
            strip,
            {
                "/api": "/",
                "/api/assessments": "/assessments",
                "/n/token": "/n/token",
                "/apiculture": "/apiculture",
            },
        ),
        (
            spa,
            {
                "/auth/callback": "/index.html",
                "/": "/index.html",
                "/assets/app.js": "/assets/app.js",
            },
        ),
    ):
        script = (
            code
            + "\nconst cases = "
            + json.dumps(cases)
            + """;
for (const [uri, expected] of Object.entries(cases)) {
    const querystring = {code:{value:'abc'},state:{value:'xyz'}};
    const out = handler({request:{uri,querystring}});
    if(out.uri !== expected || out.querystring !== querystring) process.exit(1);
}"""
        )
        subprocess.run(["node", "-e", script], check=True)


def test_core_singleton_and_ssm_wiring():
    t = service_template()
    services = properties(t, "AWS::ECS::Service")
    assert len(services) == 1
    core = services[0]
    assert core["ServiceName"] == "core" and core["DesiredCount"] == 1
    assert core["DeploymentConfiguration"]["MaximumPercent"] == 100
    assert core["DeploymentConfiguration"]["MinimumHealthyPercent"] == 0
    assert core["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"] == "ENABLED"
    assert core["LoadBalancers"][0]["ContainerPort"] == 8000
    t.resource_count_is("AWS::ApplicationAutoScaling::ScalableTarget", 0)
    t.resource_count_is("AWS::ApplicationAutoScaling::ScalingPolicy", 0)
    t.resource_count_is("AWS::ElasticLoadBalancingV2::TargetGroup", 0)
    t.resource_count_is("AWS::EC2::SecurityGroupIngress", 0)
    task = properties(t, "AWS::ECS::TaskDefinition")[0]
    assert task["Cpu"] == "1024" and task["Memory"] == "2048"
    container = task["ContainerDefinitions"][0]
    env = {e["Name"]: e["Value"] for e in container["Environment"]}
    assert env["MOTHUSI_PROVIDER"] == "ollama" and env["MOTHUSI_OLLAMA_MODEL"] == "qwen3:14b"
    assert env["MOTHUSI_DATA"] == "/data" and env["MOTHUSI_NOTE_LINK_DAYS"] == "14"
    assert "KNOWLEDGE_URL" not in env and "POSTGRES_URL" not in env
    assert env["MOTHUSI_PUBLIC_URL"] == env["MOTHUSI_WEB_ORIGINS"]
    assert env["POSTGRES_DB"] == "mothusi"  # fixed by MothusiData; not a key of the RDS secret
    assert env["MOTHUSI_OLLAMA_MODERATOR_MODEL"] == "phi4"
    assert env["MOTHUSI_OLLAMA_READER_MODEL"] == "qwen3-vl:8b-instruct"
    secrets = {s["Name"]: s["ValueFrom"] for s in container["Secrets"]}
    assert set(secrets) == {  # no ELEVENLABS_API_KEY until -c voice=true: an empty secret
        "MOTHUSI_NOTE_KEY",  # would stop ECS starting the task
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    }
    for key in ("host", "port", "username", "password"):
        assert f":{key}::" in json.dumps(secrets)
    text = json.dumps(t.to_json())
    for path in (
        "front/core-target-group-arn",
        "model-host/url",
        "db/secret-arn",
        "bucket/uploads",
        "bucket/bundles",
        "cognito/user-pool-id",
        "cognito/client-id",
        "front/distribution-domain",
    ):
        assert f"/{A}/{path}" in text
    for absent in ("ANTHROPIC_API_KEY", "RABBITMQ_URL", "MOTHUSI_TEACHER_EMAIL", "Fn::ImportValue"):
        assert absent not in text
    assert f"{A}/note-key" in text and "s3:DeleteObject" in text


def test_optional_services_and_knowledge_permissions():
    t = service_template("core,intake,knowledge")
    assert {s["ServiceName"] for s in properties(t, "AWS::ECS::Service")} == {
        "core",
        "intake",
        "knowledge",
    }
    tasks = {p["Family"]: p for p in properties(t, "AWS::ECS::TaskDefinition")}
    assert tasks["mothusi-intake"]["Cpu"] == "512" and tasks["mothusi-intake"]["Memory"] == "1024"
    assert (
        tasks["mothusi-knowledge"]["Cpu"] == "1024"
        and tasks["mothusi-knowledge"]["Memory"] == "4096"
    )
    envs = {
        n: {e["Name"]: e["Value"] for e in p["ContainerDefinitions"][0]["Environment"]}
        for n, p in tasks.items()
    }
    assert envs["mothusi-core"]["KNOWLEDGE_URL"] == f"http://knowledge.{A}.internal:8000"
    assert "KNOWLEDGE_SNAPSHOT" in envs["mothusi-knowledge"]
    assert "OLLAMA_URL" not in envs["mothusi-knowledge"]
    assert "POSTGRES_HOST" not in json.dumps(tasks["mothusi-intake"])
    assert f"/{A}/knowledge/snapshot" in json.dumps(t.to_json())
    policies = json.dumps(properties(t, "AWS::IAM::Policy"))
    assert "kms:ViaService" in policies and "s3.af-south-1.amazonaws.com" in policies
    for log in properties(t, "AWS::Logs::LogGroup"):
        assert log["RetentionInDays"] == 7
    t.resource_count_is("AWS::ServiceDiscovery::Service", 3)
    assert "Fn::ImportValue" not in json.dumps(t.to_json())


def test_empty_and_invalid_service_selection():
    service_template("").resource_count_is("AWS::ECS::Service", 0)
    with pytest.raises(ValueError, match="services"):
        service_template("core,unknown")


def test_console_policy_has_one_owner_and_preserves_data_policy(front):
    # MothusiData already owns the bucket's CloudFormation BucketPolicy. Front
    # merges only its OAC statement instead of declaring a second owner.
    front.resource_count_is("AWS::S3::BucketPolicy", 0)
    code = next(
        p["Code"]["ZipFile"]
        for p in properties(front, "AWS::Lambda::Function")
        if "GetBucketPolicyCommand" in p.get("Code", {}).get("ZipFile", "")
    )
    import subprocess

    script = (
        "const vm = require('vm'); const assert = require('assert');\n"
        + "const code = "
        + json.dumps(code)
        + ";\n"
        + r"""
let policy = {Version: '2012-10-17', Statement: [{Sid:'DataSslOnly', Effect:'Deny'}]};
const commands = [];
class Command { constructor(input) { this.input = input; } }
class GetBucketPolicyCommand extends Command {}
class PutBucketPolicyCommand extends Command {}
class S3Client { async send(command) {
    commands.push(command);
    if (command instanceof GetBucketPolicyCommand) return {Policy: JSON.stringify(policy)};
    policy = JSON.parse(command.input.Policy); return {};
} }
const sandbox = {exports: {}, require: () => ({
    S3Client, GetBucketPolicyCommand, PutBucketPolicyCommand
})};
vm.runInNewContext(code, sandbox);
const handler = sandbox.exports.handler;
(async () => {
    const props = {Bucket: 'console', Statement: {Sid:'MothusiConsoleOac', Effect:'Allow'}};
    await handler({RequestType:'Create', ResourceProperties:props});
    assert.deepStrictEqual(policy.Statement.map(s=>s.Sid), ['DataSslOnly','MothusiConsoleOac']);
    await handler({RequestType:'Update', ResourceProperties:props});
    assert.strictEqual(policy.Statement.length, 2);
    await handler({RequestType:'Delete', ResourceProperties:props});
    assert.deepStrictEqual(policy.Statement.map(s=>s.Sid), ['DataSslOnly']);
})().catch(e => { console.error(e); process.exit(1); });
"""
    )
    subprocess.run(["node", "-e", script], check=True)


def test_origin_secret_is_persisted_and_never_rotated_by_update(front):
    import subprocess

    code = next(
        p["Code"]["ZipFile"]
        for p in properties(front, "AWS::Lambda::Function")
        if "SecureString" in p.get("Code", {}).get("ZipFile", "")
    )
    script = (
        "const vm = require('vm'); const assert = require('assert');\n"
        + f"const ADOPTED = {json.dumps(A)};\n"
        + "const code = "
        + json.dumps(code)
        + ";\n"
        + r"""
let value;
let writes = 0;
let fail = false;
class Command { constructor(input) { this.input = input; } }
class GetParameterCommand extends Command {}
class PutParameterCommand extends Command {}
class SSMClient { async send(command) {
    assert.strictEqual(command.input.Name, '/' + ADOPTED + '/front/origin-secret');
    if (fail) throw new Error('AccessDenied');
    if (command instanceof GetParameterCommand) {
        assert.strictEqual(command.input.WithDecryption, true);
        if (value === undefined) throw {name: 'ParameterNotFound'};
        return {Parameter: {Value: value}};
    }
    assert.strictEqual(command.input.Type, 'SecureString');
    value = command.input.Value; writes++; return {};
} }
const env = {PARAMETER_NAME: '/' + ADOPTED + '/front/origin-secret'};
const sandbox = {exports: {}, process: {env},
    require: name => name === 'crypto' ? require('crypto') : {
    SSMClient, GetParameterCommand, PutParameterCommand
}};
vm.runInNewContext(code, sandbox);
(async () => {
    const handler = sandbox.exports.handler;
    const first = await handler({RequestType:'Create'});
    assert.strictEqual(first.NoEcho, true);
    assert.strictEqual(first.Data.Value.length, 48);
    const second = await handler({RequestType:'Update'});
    assert.strictEqual(first.Data.Value, second.Data.Value);
    assert.strictEqual(writes, 1);
    await handler({RequestType:'Delete'});
    assert.strictEqual(writes, 1);
    fail = true;
    await assert.rejects(() => handler({RequestType:'Update'}), /AccessDenied/);
})().catch(e => { console.error(e); process.exit(1); });
"""
    )
    subprocess.run(["node", "-e", script], check=True)


def test_upload_cors_exact_rule_and_front_outputs(front):
    def resolved(value):
        if isinstance(value, dict):
            if "Fn::Join" in value:
                delimiter, parts = value["Fn::Join"]
                return delimiter.join(resolved(p) for p in parts)
            if "Ref" in value:
                return "ref:" + value["Ref"]
            if "Fn::GetAtt" in value:
                return "attr:" + ".".join(value["Fn::GetAtt"])
        return value

    cors = next(c for c in properties(front, "Custom::AWS") if "putBucketCors" in json.dumps(c))
    call = json.loads(resolved(cors["Create"]))
    params = front.to_json()["Parameters"]
    bucket_ref = next(k for k, v in params.items() if v.get("Default") == f"/{A}/bucket/uploads")
    assert call["parameters"]["Bucket"] == "ref:" + bucket_ref
    dist_id = next(iter(resources(front, "AWS::CloudFront::Distribution")))
    assert call["parameters"]["CORSConfiguration"]["CORSRules"] == [
        {
            "AllowedOrigins": [f"https://attr:{dist_id}.DomainName"],
            "AllowedMethods": ["PUT"],
            "AllowedHeaders": ["Content-Type", "x-amz-*"],
            "MaxAgeSeconds": 3000,
        }
    ]
    assert resolved(cors["Create"]) == resolved(cors["Update"])
    outputs = {p["Name"] for p in properties(front, "AWS::SSM::Parameter")}
    assert outputs == {
        f"/{A}/{suffix}"
        for suffix in (
            "front/distribution-domain",
            "front/distribution-id",
            "front/alb-dns",
            "front/core-target-group-arn",
            "cognito/user-pool-id",
            "cognito/client-id",
            "cognito/domain",
        )
    }
    assert "Fn::ImportValue" not in json.dumps(front.to_json())


def test_deploy_role_can_read_the_mothusi_parameters(stacks):
    policy = json.dumps(resources(stacks["MothusiCi"], "AWS::IAM::Policy"))
    assert "ssm:GetParameter" in policy
    assert f"parameter/{A}/*" in policy


# --------------------------------------------------------------- MothusiModelHost (§6, §6.2)


@pytest.fixture(scope="module")
def model_host():
    from mothusi_infra.model_host import MothusiModelHost

    app = App()
    stack = MothusiModelHost(
        app, "MothusiModelHost", stage=PRODUCTION, env=Environment(region="af-south-1")
    )
    return Template.from_stack(stack)


def test_model_host_instance_ami_and_imdsv2(model_host):
    model_host.resource_count_is("AWS::EC2::Instance", 1)
    model_host.has_resource_properties("AWS::EC2::Instance", {"InstanceType": "g4dn.xlarge"})
    params = model_host.to_json()["Parameters"]
    ami = next(p for p in params.values() if p["Type"].endswith("<AWS::EC2::Image::Id>"))
    assert ami["Default"] == (
        "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
    )
    data = next(iter(resources(model_host, "AWS::EC2::LaunchTemplate").values()))["Properties"][
        "LaunchTemplateData"
    ]
    assert data["MetadataOptions"]["HttpTokens"] == "required"
    (root,) = data["BlockDeviceMappings"]
    assert root["Ebs"] | {"KmsKeyId": None} == {
        "VolumeSize": 100,
        "VolumeType": "gp3",
        "Encrypted": True,
        "DeleteOnTermination": True,
        "KmsKeyId": None,
    }
    assert "kms-key-arn" in json.dumps(model_host.to_json()["Parameters"])


def test_model_host_admin_is_ssm_only(model_host):
    model_host.resource_count_is("AWS::EC2::KeyPair", 0)
    instance = next(iter(resources(model_host, "AWS::EC2::Instance").values()))["Properties"]
    assert "KeyName" not in instance
    model_host.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": f"{A}-model-host",
            "ManagedPolicyArns": [
                {
                    "Fn::Join": [
                        "",
                        [
                            "arn:",
                            {"Ref": "AWS::Partition"},
                            ":iam::aws:policy/AmazonSSMManagedInstanceCore",
                        ],
                    ]
                }
            ],
        },
    )
    model_host.resource_count_is("AWS::IAM::Policy", 0)  # nothing beyond Session Manager
    model_host.has_resource_properties(
        "AWS::IAM::InstanceProfile", {"InstanceProfileName": f"{A}-model-host"}
    )


def test_model_host_lands_where_g4dn_is_offered(model_host):
    # g4dn.xlarge is offered in af-south-1b and 1c only (DescribeInstanceTypeOfferings on the
    # account, 2026-11-11). /mothusi/subnet-ids lists the public subnets in network.py's AZ order,
    # af-south-1a then af-south-1b, so the model host must take index 1.
    assert AZS == ["af-south-1a", "af-south-1b"]
    instance = next(iter(resources(model_host, "AWS::EC2::Instance").values()))
    assert instance["Properties"]["SubnetId"]["Fn::Select"][0] == 1


def test_model_host_network_and_outputs_come_from_ssm(model_host):
    template = json.dumps(model_host.to_json())
    for name in (f"/{A}/subnet-ids", f"/{A}/sg/model"):
        assert name in template
    names = {p["Properties"]["Name"] for p in resources(model_host, "AWS::SSM::Parameter").values()}
    assert names == {f"/{A}/model-host/instance-id", f"/{A}/model-host/url"}
    url = next(
        p["Properties"]["Value"]
        for p in resources(model_host, "AWS::SSM::Parameter").values()
        if p["Properties"]["Name"] == f"/{A}/model-host/url"
    )
    (instance_id,) = resources(model_host, "AWS::EC2::Instance")
    assert url == {
        "Fn::Join": ["", ["http://", {"Fn::GetAtt": [instance_id, "PrivateIp"]}, ":11434"]]
    }


def test_model_host_user_data_sets_home_before_the_ollama_cli():
    # cloud-init runs user data without HOME, and the Ollama CLI panics without it
    # ("panic: $HOME is not defined"): the first deploy on 2026-11-11 pulled no model.
    from mothusi_infra.model_host import USER_DATA

    script = USER_DATA.read_text()
    assert 'export HOME="${HOME:-/root}"' in script
    assert script.index('export HOME="${HOME:-/root}"') < script.index("ollama pull")


def test_model_host_ollama_waits_long_enough_to_load_the_marker():
    # The root gp3 volume read qwen3:14b (9.3 GB) at about 50 MB/s after a stop/start, past
    # Ollama's default 5-minute load timeout (2026-11-11), so every warm-up failed.
    from mothusi_infra.model_host import USER_DATA

    assert 'Environment="OLLAMA_LOAD_TIMEOUT=15m"' in USER_DATA.read_text()


def test_model_host_serves_four_requests_at_once():
    # deployment-aws.md §6.2: four decode together (~25 tok/s against ~17 for one, 2026-11-12),
    # matching the Planner's marking batch of 4.
    from mothusi_infra.model_host import USER_DATA

    assert 'Environment="OLLAMA_NUM_PARALLEL=4"' in USER_DATA.read_text()


def test_model_host_user_data_pins_ollama_and_the_models():
    from mothusi_infra.model_host import USER_DATA

    script = USER_DATA.read_text()
    assert 'OLLAMA_VERSION="0.40.15"' in script
    assert "628c018404a4206493efd38e46251c154ede5141b8b8dc85c659b3b027c47379" in script
    assert "sha256sum -c -" in script
    for setting in (
        "OLLAMA_HOST=0.0.0.0:11434",
        "OLLAMA_KEEP_ALIVE=30m",
        "OLLAMA_MAX_LOADED_MODELS=1",
    ):
        assert setting in script
    assert 'MODELS=("qwen3-vl:8b-instruct" "qwen3:14b" "phi4")' in script
    assert "/opt/mothusi/model-ready.sh" in script and 'grep -q "T4"' in script


def test_model_ready_script_requires_every_model(tmp_path):
    """Run the generated model-ready.sh against fake nvidia-smi and curl."""
    import os
    import re
    import subprocess

    from mothusi_infra.model_host import USER_DATA

    body = re.search(r"<<'READY'\n(.*?)\nREADY\n", USER_DATA.read_text(), re.S)
    assert body
    ready = tmp_path / "model-ready.sh"
    ready.write_text(body.group(1) + "\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "nvidia-smi").write_text("#!/bin/sh\necho 'Tesla T4'\n")

    def run(models):
        tags = json.dumps({"models": [{"name": m, "digest": f"d-{m}"} for m in models]})
        (bin_dir / "curl").write_text(f"#!/bin/sh\ncat <<'J'\n{tags}\nJ\n")
        for f in bin_dir.iterdir():
            f.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
        return subprocess.run(
            ["bash", str(ready), "--digests"], env=env, capture_output=True, text=True, check=False
        )

    full = run(["qwen3-vl:8b-instruct", "qwen3:14b", "phi4:latest"])
    assert full.returncode == 0, full.stderr
    assert "qwen3:14b d-qwen3:14b" in full.stdout
    partial = run(["qwen3-vl:8b-instruct", "phi4:latest"])
    assert partial.returncode != 0 and "qwen3:14b" in partial.stderr


# ----------------------------------------------------------- the adopted environment


def test_the_app_names_the_adopted_stacks(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    infra = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "app.py"],
        cwd=infra,
        env={**os.environ, "CDK_OUTDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    stacks = {
        k for k, v in manifest["artifacts"].items() if v["type"] == "aws:cloudformation:stack"
    }
    # CloudFormation can't rename a stack: adopted ones keep their names, new ones are Mothusi's.
    parts = ("Network", "Data", "Front", "ModelHost", "Service")
    assert stacks == {PRODUCTION.stack(part) for part in parts} | {
        "MothusiCi",
        "MothusiBudget",
    }


def test_renamable_names_are_mothusi(stacks, front):
    data = stacks["MothusiData"]
    data.has_resource_properties("AWS::KMS::Alias", {"AliasName": "alias/mothusi-data"})
    front.has_resource_properties("AWS::Cognito::UserPool", {"UserPoolName": "mothusi-teachers"})
    oac = properties(front, "AWS::CloudFront::OriginAccessControl")[0]
    assert oac["OriginAccessControlConfig"]["Name"] == "mothusi-console"
    t = service_template("core,knowledge")
    assert {p["Family"] for p in properties(t, "AWS::ECS::TaskDefinition")} == {
        "mothusi-core",
        "mothusi-knowledge",
    }
    assert {p["LogGroupName"] for p in properties(t, "AWS::Logs::LogGroup")} == {
        "/mothusi/core",
        "/mothusi/knowledge",
    }


def test_the_gpu_host_boots_from_the_script_it_was_built_with():
    # Any change to an instance's user data stops and starts it. The adopted host's script is the
    # template with the adopted stem, so a deploy never touches the running GPU through it.
    from mothusi_infra.model_host import USER_DATA, user_data

    script = user_data(PRODUCTION)
    assert "mothusi" not in script.lower()
    assert script.replace(A, "mothusi").replace(A.title(), "Mothusi") == USER_DATA.read_text()
    assert f"{PRODUCTION.opt_dir}/model-ready.sh" in script


def test_the_release_role_takes_githubs_immutable_subject():
    import re

    t = Template.from_stack(
        MothusiCi(App(), "CiPattern", stage=PRODUCTION, env=Environment(region="af-south-1"))
    )
    pattern = t.to_json()["Parameters"]["GitHubRepository"]["AllowedPattern"]
    assert re.fullmatch(pattern, "Katlego-tech@115792195/Mothusi@1456381806")
    assert re.fullmatch(pattern, "owner/repo")
    assert not re.fullmatch(pattern, "owner/repo:ref:refs/heads/main")


def test_the_database_is_mothusis_and_the_adopted_one_is_retained(stacks):
    t = stacks["MothusiData"]
    t.has_resource_properties(
        "AWS::RDS::DBInstance", {"DBInstanceIdentifier": "mothusi-db", "DBName": "mothusi"}
    )
    secret = next(
        r
        for r in resources(t, "AWS::SecretsManager::Secret").values()
        if r["Properties"]["Name"] == "mothusi/db"
    )
    template = secret["Properties"]["GenerateSecretString"]["SecretStringTemplate"]
    assert json.loads(template) == {"username": "mothusi"}
    # A new identifier replaces the instance: the old one must be kept, never deleted.
    for r in [*resources(t, "AWS::RDS::DBInstance").values(), secret]:
        assert r["DeletionPolicy"] == r["UpdateReplacePolicy"] == "Retain"


# --------------------------------------------------------------------- the local watchdog


def test_the_local_watchdog_reads_the_cores_markers_and_only_reads():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "watchdog.sh").read_text()
    # mothusi_core.events writes these (the infra project can't import the core).
    assert "WATCHDOG_RUN_HALTED" in script and "WATCHDOG_NEEDS_TEACHER" in script
    for verb in ("stop-", "start-", "update-", "delete-", "put-", "create-", "ops.sh"):
        assert f"aws_ ecs {verb}" not in script and f"aws_ ec2 {verb}" not in script
        assert f"aws_ rds {verb}" not in script
