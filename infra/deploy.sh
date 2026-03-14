#!/bin/bash
set -e

echo "=== Building Docker Images ==="
# Build the agent image
docker build -t autoresearch-agent:latest ../agent/

# Build the queue test image
docker build -t autoresearch-queue:latest ../queue/

echo "=== Deploying with Pulumi ==="
# Ensure Pulumi Python dependencies are installed
pip install -r requirements.txt

# Select the dev stack, or initialize it if it doesn't exist
pulumi stack select dev 2>/dev/null || pulumi stack init dev

# Deploy the k3s infrastructure
pulumi up --yes

echo "=== Deployment Complete! ==="
echo ""
echo "You can view the agent logs using:"
echo "kubectl logs -l app=agent -f"
echo ""
echo "To enqueue test tasks, run a temporary pod:"
echo "kubectl run -i --rm --tty queue-client --image=autoresearch-queue:latest --image-pull-policy=Never --restart=Never --env=\"QUEUE_URL=http://queue-api:8000\""