"""Mothusi's CDK app: the one environment, adopted in place (mothusi_infra/stage.py).

npx cdk deploy MothusiCi    # once: the GitHub release role
npx cdk deploy MothusiWatchdog -c watchdogEmail=<address>
# the release workflow redeploys only the service stack, on a v* tag
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
from mothusi_infra.stage import PRODUCTION
from mothusi_infra.watchdog import MothusiWatchdog

app = App()
stage = PRODUCTION
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
env = Environment(account=account, region="af-south-1")

network = MothusiNetwork(app, stage.stack("Network"), stage=stage, env=env)
data = MothusiData(app, stage.stack("Data"), stage=stage, env=env)
data.add_dependency(network)
front = MothusiFront(app, stage.stack("Front"), stage=stage, env=env)
front.add_dependency(network)
front.add_dependency(data)
# Deployed on its own (it is the one GPU cost) and never by the release workflow.
model_host = MothusiModelHost(app, stage.stack("ModelHost"), stage=stage, env=env)
model_host.add_dependency(network)
model_host.add_dependency(data)
service = MothusiService(app, stage.stack("Service"), stage=stage, env=env)
service.add_dependency(front)
MothusiCi(app, "MothusiCi", stage=stage, env=env)
# Emails within a minute when a deploy fails, the GPU host stops or a run halts (watchdog.py).
MothusiWatchdog(app, "MothusiWatchdog", stage=stage, env=env)
# Budgets has no CloudFormation support in af-south-1; the budget lives in us-east-1.
MothusiBudget(app, "MothusiBudget", env=Environment(account=account, region="us-east-1"))
app.synth()
