# Microservices Platform on AWS EKS

A complete, production-ready microservices platform deployed on AWS EKS with comprehensive observability, security, and GitOps capabilities.

## Architecture Overview

This infrastructure deploys a fully-featured Kubernetes platform with:

### Core Infrastructure
- **AWS EKS Cluster**: Managed Kubernetes v1.31 with auto-scaling node groups
- **VPC**: Multi-AZ networking with public and private subnets
- **NAT Gateway**: Secure outbound internet access for private subnets

### Service Mesh
- **Istio v1.24.2**: Advanced traffic management, security, and observability
- **Ingress Gateway**: Load-balanced entry point with AWS NLB

### Observability Stack
- **ELK Stack**: Centralized logging with Elasticsearch, Kibana, and Fluent Bit
- **Jaeger**: Distributed tracing integrated with Istio
- **Prometheus + Grafana**: Metrics collection and visualization with pre-configured dashboards

### API Gateway
- **Kong Gateway**: Cloud-native API gateway with ingress controller

### Security
- **cert-manager**: Automatic TLS certificate management with Let's Encrypt
- **Network Policies**: Pod-to-pod security and namespace isolation
- **Istio mTLS**: Mutual TLS for service-to-service communication

### GitOps
- **ArgoCD**: Continuous deployment with GitOps workflow

## Prerequisites

- AWS Account with appropriate permissions
- Pulumi CLI installed
- Python 3.11+
- AWS CLI configured
- kubectl installed

## Configuration

The following configuration values can be set via `pulumi config`:

```bash
# Required
pulumi config set aws:region us-west-2

# Optional (with defaults)
pulumi config set vpc_cidr 10.0.0.0/16
pulumi config set cluster_name microservices-cluster
pulumi config set k8s_version 1.31
pulumi config set node_instance_type t3.large
pulumi config set desired_capacity 3
pulumi config set min_size 2
pulumi config set max_size 6
pulumi config set letsencrypt_email admin@example.com
```

## Deployment

### Local Deployment

1. Clone the repository:
```bash
git clone https://github.com/isaac-pulumi/microservices-cluster.git
cd microservices-cluster
```

2. Create and activate virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Initialize Pulumi stack:
```bash
pulumi stack init dev
```

5. Configure AWS region:
```bash
pulumi config set aws:region us-west-2
```

6. Deploy the infrastructure:
```bash
pulumi up
```

### CI/CD Deployment

The repository includes a GitHub Actions workflow for automated deployments:

1. Set up GitHub Secrets:
   - `PULUMI_ACCESS_TOKEN`: Your Pulumi access token
   - `AWS_ROLE_ARN`: AWS IAM role ARN for OIDC authentication

2. Push to main branch or create a pull request
   - Pull requests trigger `pulumi preview`
   - Merges to main trigger `pulumi up`

## Accessing Services

After deployment, retrieve service endpoints:

```bash
# Get kubeconfig
pulumi stack output kubeconfig --show-secrets > kubeconfig.yaml
export KUBECONFIG=./kubeconfig.yaml

# Get service URLs
kubectl get svc -n istio-system istio-ingress
kubectl get svc -n logging kibana-kibana
kubectl get svc -n observability jaeger-query
kubectl get svc -n monitoring kube-prometheus-stack-grafana
kubectl get svc -n kong kong-proxy
kubectl get svc -n argocd argocd-server
```

### Default Credentials

- **Grafana**: admin / admin (change after first login)
- **ArgoCD**: admin / (retrieve with `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d`)

## Resource Requirements

The platform requires significant compute resources:

- **Minimum**: 3 x t3.large nodes (6 vCPU, 24 GB RAM total)
- **Recommended**: 3 x t3.xlarge nodes (12 vCPU, 48 GB RAM total)

Estimated monthly cost: $200-400 depending on configuration and data transfer.

## Network Policies

The platform implements defense-in-depth security with network policies:

- Default deny-all ingress in logging and observability namespaces
- Explicit allow rules for required service communication
- Namespace isolation with Istio service mesh

## Monitoring and Alerting

### Pre-configured Grafana Dashboards

- Istio Mesh Dashboard
- Istio Service Dashboard
- Istio Workload Dashboard
- Kubernetes Cluster Overview

### Prometheus Metrics

- Cluster metrics (CPU, memory, disk)
- Node metrics
- Pod metrics
- Istio service mesh metrics
- Application metrics (via service monitors)

## Logging

Logs are collected by Fluent Bit and sent to Elasticsearch:

- Container logs from all pods
- Kubernetes audit logs
- Istio access logs
- Application logs

Access logs via Kibana dashboard.

## Distributed Tracing

Jaeger collects traces from Istio-enabled services:

- 100% sampling rate (adjust for production)
- Elasticsearch backend for trace storage
- Integration with Istio service mesh

## Cleanup

To destroy all resources:

```bash
pulumi destroy
```

**Warning**: This will delete all resources including persistent volumes and data.

## Troubleshooting

### Pods not starting

Check node capacity:
```bash
kubectl top nodes
kubectl describe nodes
```

### Network connectivity issues

Verify network policies:
```bash
kubectl get networkpolicies -A
```

### Certificate issues

Check cert-manager logs:
```bash
kubectl logs -n cert-manager -l app=cert-manager
```

## Security Considerations

1. **Change default passwords** for Grafana and ArgoCD
2. **Configure proper Let's Encrypt email** for certificate notifications
3. **Enable AWS IAM roles for service accounts (IRSA)** for pod-level permissions
4. **Review and adjust network policies** based on your security requirements
5. **Enable AWS CloudTrail** for audit logging
6. **Configure AWS KMS** for encryption at rest

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see LICENSE file for details

## Support

For issues and questions:
- GitHub Issues: https://github.com/isaac-pulumi/microservices-cluster/issues
- Pulumi Community: https://pulumi.com/community
