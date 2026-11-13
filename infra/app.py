"""Mothusi's CDK app: the account-level stacks once, and every per-stage stack once per stage.

    npx cdk deploy MothusiCi                       # once per account: ECR + deploy roles
    npx cdk deploy 'MothusiStaging*'               # one stage (CI does this on every merge)
    npx cdk deploy MothusiProductionService ...    # production: only through deploy.yml

Stage stacks are Mothusi<Stage><Part>: MothusiStagingNetwork, MothusiProductionData, ...
"""

import os

from aws_cdk import App, Environment
from mothusi_infra.budget import MothusiBudget
from mothusi_infra.ci import MothusiCi
from mothusi_infra.data import MothusiData
from mothusi_infra.front import MothusiFront
from mothusi_infra.model_host import MothusiModelHost
from mothusi_infra.network import MothusiNetwork
from mothusi_infra.service import MothusiService
from mothusi_infra.stage import STAGES

app = App()
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
env = Environment(account=account, region="af-south-1")

ci = MothusiCi(app, "MothusiCi", env=env)
for stage in STAGES.values():
    name = stage.stack_prefix
    network = MothusiNetwork(app, f"{name}Network", stage=stage, env=env)
    data = MothusiData(app, f"{name}Data", stage=stage, env=env)
    data.add_dependency(network)
    front = MothusiFront(app, f"{name}Front", stage=stage, env=env)
    front.add_dependency(network)
    front.add_dependency(data)
    # Deployed on its own (it is the one GPU cost): cdk deploy <Stage>ModelHost, then ops.sh stop.
    model_host = MothusiModelHost(app, f"{name}ModelHost", stage=stage, env=env)
    model_host.add_dependency(network)
    model_host.add_dependency(data)
    service = MothusiService(app, f"{name}Service", stage=stage, env=env)
    service.add_dependency(front)
    service.add_dependency(ci)
# Budgets has no CloudFormation support in af-south-1; one account budget lives in us-east-1.
MothusiBudget(app, "MothusiBudget", env=Environment(account=account, region="us-east-1"))
app.synth()
