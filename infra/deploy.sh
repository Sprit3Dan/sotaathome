#!/bin/bash
set -e

export KUBECONFIG="${KUBECONFIG:-$(pwd)/kubeconfig.yaml}"

echo "=== Deploying with Pulumi ==="
# Ensure Pulumi Python dependencies are installed
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Select the target stack from environment, defaulting to dev
PULUMI_STACK=autoresearch-infra
pulumi stack select "$PULUMI_STACK" 2>/dev/null || pulumi stack init "$PULUMI_STACK"

# Deploy the k3s infrastructure
pulumi up --yes --stack "$PULUMI_STACK"

echo "=== Deployment Complete! ==="
echo ""
echo "You can view the orchestrator logs using:"
echo "kubectl logs -l app=orchestrator -f"
echo ""
echo "You can inspect the deployed services with:"
echo "kubectl get deploy,svc,pvc"
