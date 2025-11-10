"""
Microservices Platform on AWS EKS
Complete Kubernetes platform with service mesh, observability, and GitOps
"""

import pulumi
import pulumi_awsx as awsx
import pulumi_eks as eks
import pulumi_kubernetes as k8s

# Get configuration
config = pulumi.Config()
aws_config = pulumi.Config("aws")

# Get AWS region
aws_region = aws_config.get("region") or "us-west-2"

# Cluster configuration
cluster_name = config.get("cluster_name") or "microservices-cluster"
k8s_version = config.get("k8s_version") or "1.31"
node_instance_type = config.get("node_instance_type") or "t3.large"
desired_capacity = config.get_int("desired_capacity") or 3
min_size = config.get_int("min_size") or 2
max_size = config.get_int("max_size") or 6

# VPC Configuration
vpc_cidr = config.get("vpc_cidr") or "10.0.0.0/16"

# Create VPC with public and private subnets across 3 availability zones
# This provides high availability and proper network segmentation
vpc = awsx.ec2.Vpc(
    "microservices-vpc",
    cidr_block=vpc_cidr,
    number_of_availability_zones=3,
    nat_gateways=awsx.ec2.NatGatewayConfigurationArgs(
        strategy=awsx.ec2.NatGatewayStrategy.SINGLE,
    ),
    subnet_strategy=awsx.ec2.SubnetAllocationStrategy.AUTO,
    subnet_specs=[
        # Private subnets for EKS nodes and pods
        awsx.ec2.SubnetSpecArgs(
            type=awsx.ec2.SubnetType.PRIVATE,
            cidr_mask=19,
            tags={"kubernetes.io/role/internal-elb": "1"},
        ),
        # Public subnets for load balancers and NAT gateways
        awsx.ec2.SubnetSpecArgs(
            type=awsx.ec2.SubnetType.PUBLIC,
            cidr_mask=22,
            tags={"kubernetes.io/role/elb": "1"},
        ),
    ],
    tags={
        "Name": "microservices-vpc",
        "Project": "microservices-platform",
        "ManagedBy": "Pulumi",
    },
)

# Create EKS Cluster
# Using the EKS module for simplified cluster creation with best practices
cluster = eks.Cluster(
    cluster_name,
    vpc_id=vpc.vpc_id,
    public_subnet_ids=vpc.public_subnet_ids,
    private_subnet_ids=vpc.private_subnet_ids,
    version=k8s_version,
    instance_type=node_instance_type,
    desired_capacity=desired_capacity,
    min_size=min_size,
    max_size=max_size,
    # Enable cluster logging for audit and diagnostics
    enabled_cluster_log_types=[
        "api",
        "audit",
        "authenticator",
        "controllerManager",
        "scheduler",
    ],
    # Create OIDC provider for IAM roles for service accounts (IRSA)
    create_oidc_provider=True,
    # Enable private endpoint access for enhanced security
    endpoint_private_access=True,
    endpoint_public_access=True,
    tags={
        "Name": cluster_name,
        "Project": "microservices-platform",
        "ManagedBy": "Pulumi",
    },
)

# Create a Kubernetes provider instance using the cluster's kubeconfig
k8s_provider = k8s.Provider(
    "k8s-provider",
    kubeconfig=cluster.kubeconfig,
)

# ============================================================================
# ISTIO SERVICE MESH
# ============================================================================

# Create istio-system namespace
istio_namespace = k8s.core.v1.Namespace(
    "istio-system",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="istio-system",
        labels={
            "name": "istio-system",
        },
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)

# Install Istio Base (CRDs and cluster-wide resources)
istio_base = k8s.helm.v3.Release(
    "istio-base",
    k8s.helm.v3.ReleaseArgs(
        chart="base",
        version="1.24.2",
        namespace=istio_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://istio-release.storage.googleapis.com/charts",
        ),
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[istio_namespace],
    ),
)

# Install Istiod (Control Plane)
istiod = k8s.helm.v3.Release(
    "istiod",
    k8s.helm.v3.ReleaseArgs(
        chart="istiod",
        version="1.24.2",
        namespace=istio_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://istio-release.storage.googleapis.com/charts",
        ),
        values={
            "global": {
                "hub": "docker.io/istio",
                "tag": "1.24.2",
            },
            "pilot": {
                "resources": {
                    "requests": {
                        "cpu": "500m",
                        "memory": "2048Mi",
                    },
                },
            },
            # Enable tracing for Jaeger integration
            "meshConfig": {
                "enableTracing": True,
                "defaultConfig": {
                    "tracing": {
                        "sampling": 100.0,
                    },
                },
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[istio_base],
    ),
)

# Install Istio Ingress Gateway
istio_ingress = k8s.helm.v3.Release(
    "istio-ingress",
    k8s.helm.v3.ReleaseArgs(
        chart="gateway",
        version="1.24.2",
        namespace=istio_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://istio-release.storage.googleapis.com/charts",
        ),
        values={
            "service": {
                "type": "LoadBalancer",
                "annotations": {
                    "service.beta.kubernetes.io/aws-load-balancer-type": "nlb",
                },
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[istiod],
    ),
)

# ============================================================================
# ELK STACK (ELASTICSEARCH, LOGSTASH, KIBANA) + FLUENT BIT
# ============================================================================

# Create logging namespace
logging_namespace = k8s.core.v1.Namespace(
    "logging",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="logging",
        labels={
            "name": "logging",
            "istio-injection": "enabled",
        },
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)

# Install Elasticsearch using Helm
elasticsearch = k8s.helm.v3.Release(
    "elasticsearch",
    k8s.helm.v3.ReleaseArgs(
        chart="elasticsearch",
        version="8.5.1",
        namespace=logging_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://helm.elastic.co",
        ),
        values={
            "replicas": 3,
            "minimumMasterNodes": 2,
            "resources": {
                "requests": {
                    "cpu": "1000m",
                    "memory": "2Gi",
                },
                "limits": {
                    "cpu": "2000m",
                    "memory": "4Gi",
                },
            },
            "volumeClaimTemplate": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {
                    "requests": {
                        "storage": "30Gi",
                    },
                },
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[logging_namespace],
    ),
)

# Install Kibana
kibana = k8s.helm.v3.Release(
    "kibana",
    k8s.helm.v3.ReleaseArgs(
        chart="kibana",
        version="8.5.1",
        namespace=logging_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://helm.elastic.co",
        ),
        values={
            "elasticsearchHosts": "http://elasticsearch-master:9200",
            "resources": {
                "requests": {
                    "cpu": "500m",
                    "memory": "1Gi",
                },
                "limits": {
                    "cpu": "1000m",
                    "memory": "2Gi",
                },
            },
            "service": {
                "type": "LoadBalancer",
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[elasticsearch],
    ),
)

# Install Fluent Bit for log collection
fluent_bit = k8s.helm.v3.Release(
    "fluent-bit",
    k8s.helm.v3.ReleaseArgs(
        chart="fluent-bit",
        version="0.47.10",
        namespace=logging_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://fluent.github.io/helm-charts",
        ),
        values={
            "config": {
                "outputs": "[OUTPUT]\n    Name es\n    Match *\n    Host elasticsearch-master\n    Port 9200\n    Logstash_Format On\n    Logstash_Prefix kubernetes\n    Retry_Limit False\n",
            },
            "resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "128Mi",
                },
                "limits": {
                    "cpu": "200m",
                    "memory": "256Mi",
                },
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[elasticsearch],
    ),
)

# ============================================================================
# JAEGER DISTRIBUTED TRACING
# ============================================================================

# Create observability namespace
observability_namespace = k8s.core.v1.Namespace(
    "observability",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="observability",
        labels={
            "name": "observability",
            "istio-injection": "enabled",
        },
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)

# Install Jaeger Operator
jaeger_operator = k8s.helm.v3.Release(
    "jaeger-operator",
    k8s.helm.v3.ReleaseArgs(
        chart="jaeger-operator",
        version="2.57.0",
        namespace=observability_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://jaegertracing.github.io/helm-charts",
        ),
        values={
            "rbac": {
                "create": True,
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[observability_namespace],
    ),
)

# Deploy Jaeger instance with Elasticsearch storage
jaeger_instance = k8s.apiextensions.CustomResource(
    "jaeger-instance",
    api_version="jaegertracing.io/v1",
    kind="Jaeger",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="jaeger",
        namespace=observability_namespace.metadata.name,
    ),
    spec={
        "strategy": "production",
        "storage": {
            "type": "elasticsearch",
            "options": {
                "es": {
                    "server-urls": "http://elasticsearch-master.logging:9200",
                    "index-prefix": "jaeger",
                },
            },
        },
        "ingress": {
            "enabled": True,
        },
        "query": {
            "serviceType": "LoadBalancer",
        },
    },
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[jaeger_operator, elasticsearch],
    ),
)

# ============================================================================
# KONG API GATEWAY
# ============================================================================

# Create kong namespace
kong_namespace = k8s.core.v1.Namespace(
    "kong",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="kong",
        labels={
            "name": "kong",
            "istio-injection": "enabled",
        },
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)

# Install Kong Gateway
kong = k8s.helm.v3.Release(
    "kong",
    k8s.helm.v3.ReleaseArgs(
        chart="kong",
        version="2.45.0",
        namespace=kong_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://charts.konghq.com",
        ),
        values={
            "ingressController": {
                "enabled": True,
                "installCRDs": False,
            },
            "proxy": {
                "type": "LoadBalancer",
                "annotations": {
                    "service.beta.kubernetes.io/aws-load-balancer-type": "nlb",
                },
            },
            "env": {
                "database": "off",
                "nginx_worker_processes": "2",
                "proxy_access_log": "/dev/stdout",
                "admin_access_log": "/dev/stdout",
                "admin_gui_access_log": "/dev/stdout",
                "portal_api_access_log": "/dev/stdout",
                "proxy_error_log": "/dev/stderr",
                "admin_error_log": "/dev/stderr",
                "admin_gui_error_log": "/dev/stderr",
                "portal_api_error_log": "/dev/stderr",
            },
            "resources": {
                "requests": {
                    "cpu": "500m",
                    "memory": "512Mi",
                },
                "limits": {
                    "cpu": "1000m",
                    "memory": "1Gi",
                },
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[kong_namespace],
    ),
)

# ============================================================================
# CERT-MANAGER FOR AUTOMATIC TLS CERTIFICATES
# ============================================================================

# Create cert-manager namespace
cert_manager_namespace = k8s.core.v1.Namespace(
    "cert-manager",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="cert-manager",
        labels={
            "name": "cert-manager",
        },
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)

# Install cert-manager
cert_manager = k8s.helm.v3.Release(
    "cert-manager",
    k8s.helm.v3.ReleaseArgs(
        chart="cert-manager",
        version="v1.16.2",
        namespace=cert_manager_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://charts.jetstack.io",
        ),
        values={
            "crds": {
                "enabled": True,
            },
            "global": {
                "leaderElection": {
                    "namespace": "cert-manager",
                },
            },
            "resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "128Mi",
                },
                "limits": {
                    "cpu": "200m",
                    "memory": "256Mi",
                },
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[cert_manager_namespace],
    ),
)

# Create Let's Encrypt ClusterIssuer for staging (for testing)
letsencrypt_staging = k8s.apiextensions.CustomResource(
    "letsencrypt-staging",
    api_version="cert-manager.io/v1",
    kind="ClusterIssuer",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="letsencrypt-staging",
    ),
    spec={
        "acme": {
            "server": "https://acme-staging-v02.api.letsencrypt.org/directory",
            "email": config.get("letsencrypt_email") or "admin@example.com",
            "privateKeySecretRef": {
                "name": "letsencrypt-staging",
            },
            "solvers": [
                {
                    "http01": {
                        "ingress": {
                            "class": "kong",
                        },
                    },
                },
            ],
        },
    },
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[cert_manager],
    ),
)

# Create Let's Encrypt ClusterIssuer for production
letsencrypt_prod = k8s.apiextensions.CustomResource(
    "letsencrypt-prod",
    api_version="cert-manager.io/v1",
    kind="ClusterIssuer",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="letsencrypt-prod",
    ),
    spec={
        "acme": {
            "server": "https://acme-v02.api.letsencrypt.org/directory",
            "email": config.get("letsencrypt_email") or "admin@example.com",
            "privateKeySecretRef": {
                "name": "letsencrypt-prod",
            },
            "solvers": [
                {
                    "http01": {
                        "ingress": {
                            "class": "kong",
                        },
                    },
                },
            ],
        },
    },
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[cert_manager],
    ),
)

# ============================================================================
# NETWORK POLICIES FOR POD-TO-POD SECURITY
# ============================================================================

# Default deny all ingress traffic in logging namespace
logging_deny_ingress = k8s.networking.v1.NetworkPolicy(
    "logging-deny-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="deny-all-ingress",
        namespace=logging_namespace.metadata.name,
    ),
    spec=k8s.networking.v1.NetworkPolicySpecArgs(
        pod_selector=k8s.meta.v1.LabelSelectorArgs(),
        policy_types=["Ingress"],
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[logging_namespace],
    ),
)

# Allow Fluent Bit to send logs to Elasticsearch
logging_allow_fluent_to_es = k8s.networking.v1.NetworkPolicy(
    "logging-allow-fluent-to-es",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="allow-fluent-to-elasticsearch",
        namespace=logging_namespace.metadata.name,
    ),
    spec=k8s.networking.v1.NetworkPolicySpecArgs(
        pod_selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels={
                "app": "elasticsearch-master",
            },
        ),
        policy_types=["Ingress"],
        ingress=[
            k8s.networking.v1.NetworkPolicyIngressRuleArgs(
                from_=[
                    k8s.networking.v1.NetworkPolicyPeerArgs(
                        pod_selector=k8s.meta.v1.LabelSelectorArgs(
                            match_labels={
                                "app.kubernetes.io/name": "fluent-bit",
                            },
                        ),
                    ),
                ],
                ports=[
                    k8s.networking.v1.NetworkPolicyPortArgs(
                        protocol="TCP",
                        port=9200,
                    ),
                ],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[logging_namespace],
    ),
)

# Allow Kibana to access Elasticsearch
logging_allow_kibana_to_es = k8s.networking.v1.NetworkPolicy(
    "logging-allow-kibana-to-es",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="allow-kibana-to-elasticsearch",
        namespace=logging_namespace.metadata.name,
    ),
    spec=k8s.networking.v1.NetworkPolicySpecArgs(
        pod_selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels={
                "app": "elasticsearch-master",
            },
        ),
        policy_types=["Ingress"],
        ingress=[
            k8s.networking.v1.NetworkPolicyIngressRuleArgs(
                from_=[
                    k8s.networking.v1.NetworkPolicyPeerArgs(
                        pod_selector=k8s.meta.v1.LabelSelectorArgs(
                            match_labels={
                                "app": "kibana",
                            },
                        ),
                    ),
                ],
                ports=[
                    k8s.networking.v1.NetworkPolicyPortArgs(
                        protocol="TCP",
                        port=9200,
                    ),
                ],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[logging_namespace],
    ),
)

# Default deny all ingress traffic in observability namespace
observability_deny_ingress = k8s.networking.v1.NetworkPolicy(
    "observability-deny-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="deny-all-ingress",
        namespace=observability_namespace.metadata.name,
    ),
    spec=k8s.networking.v1.NetworkPolicySpecArgs(
        pod_selector=k8s.meta.v1.LabelSelectorArgs(),
        policy_types=["Ingress"],
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[observability_namespace],
    ),
)

# Allow Istio sidecars to send traces to Jaeger
observability_allow_istio_to_jaeger = k8s.networking.v1.NetworkPolicy(
    "observability-allow-istio-to-jaeger",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="allow-istio-to-jaeger",
        namespace=observability_namespace.metadata.name,
    ),
    spec=k8s.networking.v1.NetworkPolicySpecArgs(
        pod_selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels={
                "app": "jaeger",
            },
        ),
        policy_types=["Ingress"],
        ingress=[
            k8s.networking.v1.NetworkPolicyIngressRuleArgs(
                from_=[
                    k8s.networking.v1.NetworkPolicyPeerArgs(
                        namespace_selector=k8s.meta.v1.LabelSelectorArgs(
                            match_labels={
                                "istio-injection": "enabled",
                            },
                        ),
                    ),
                ],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[observability_namespace],
    ),
)

# ============================================================================
# PROMETHEUS OPERATOR AND GRAFANA FOR MONITORING
# ============================================================================

# Create monitoring namespace
monitoring_namespace = k8s.core.v1.Namespace(
    "monitoring",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="monitoring",
        labels={
            "name": "monitoring",
        },
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)

# Install kube-prometheus-stack (Prometheus Operator + Grafana)
kube_prometheus_stack = k8s.helm.v3.Release(
    "kube-prometheus-stack",
    k8s.helm.v3.ReleaseArgs(
        chart="kube-prometheus-stack",
        version="67.7.0",
        namespace=monitoring_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://prometheus-community.github.io/helm-charts",
        ),
        values={
            "prometheus": {
                "prometheusSpec": {
                    "retention": "30d",
                    "resources": {
                        "requests": {
                            "cpu": "500m",
                            "memory": "2Gi",
                        },
                        "limits": {
                            "cpu": "1000m",
                            "memory": "4Gi",
                        },
                    },
                    "storageSpec": {
                        "volumeClaimTemplate": {
                            "spec": {
                                "accessModes": ["ReadWriteOnce"],
                                "resources": {
                                    "requests": {
                                        "storage": "50Gi",
                                    },
                                },
                            },
                        },
                    },
                    # Enable service monitors for Istio
                    "serviceMonitorSelectorNilUsesHelmValues": False,
                    "podMonitorSelectorNilUsesHelmValues": False,
                },
            },
            "grafana": {
                "enabled": True,
                "adminPassword": "admin",
                "service": {
                    "type": "LoadBalancer",
                },
                "resources": {
                    "requests": {
                        "cpu": "250m",
                        "memory": "512Mi",
                    },
                    "limits": {
                        "cpu": "500m",
                        "memory": "1Gi",
                    },
                },
                # Pre-configured dashboards
                "dashboardProviders": {
                    "dashboardproviders.yaml": {
                        "apiVersion": 1,
                        "providers": [
                            {
                                "name": "default",
                                "orgId": 1,
                                "folder": "",
                                "type": "file",
                                "disableDeletion": False,
                                "editable": True,
                                "options": {
                                    "path": "/var/lib/grafana/dashboards/default",
                                },
                            },
                        ],
                    },
                },
                "dashboards": {
                    "default": {
                        "istio-mesh": {
                            "gnetId": 7639,
                            "revision": 183,
                            "datasource": "Prometheus",
                        },
                        "istio-service": {
                            "gnetId": 7636,
                            "revision": 183,
                            "datasource": "Prometheus",
                        },
                        "istio-workload": {
                            "gnetId": 7630,
                            "revision": 183,
                            "datasource": "Prometheus",
                        },
                        "kubernetes-cluster": {
                            "gnetId": 7249,
                            "revision": 1,
                            "datasource": "Prometheus",
                        },
                    },
                },
            },
            "alertmanager": {
                "enabled": True,
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[monitoring_namespace],
    ),
)

# ============================================================================
# ARGOCD FOR GITOPS CONTINUOUS DEPLOYMENT
# ============================================================================

# Create argocd namespace
argocd_namespace = k8s.core.v1.Namespace(
    "argocd",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="argocd",
        labels={
            "name": "argocd",
        },
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)

# Install ArgoCD
argocd = k8s.helm.v3.Release(
    "argocd",
    k8s.helm.v3.ReleaseArgs(
        chart="argo-cd",
        version="7.7.12",
        namespace=argocd_namespace.metadata.name,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://argoproj.github.io/argo-helm",
        ),
        values={
            "global": {
                "domain": "argocd.example.com",
            },
            "server": {
                "service": {
                    "type": "LoadBalancer",
                },
                "extraArgs": [
                    "--insecure",
                ],
                "resources": {
                    "requests": {
                        "cpu": "250m",
                        "memory": "512Mi",
                    },
                    "limits": {
                        "cpu": "500m",
                        "memory": "1Gi",
                    },
                },
            },
            "controller": {
                "resources": {
                    "requests": {
                        "cpu": "500m",
                        "memory": "1Gi",
                    },
                    "limits": {
                        "cpu": "1000m",
                        "memory": "2Gi",
                    },
                },
            },
            "repoServer": {
                "resources": {
                    "requests": {
                        "cpu": "250m",
                        "memory": "512Mi",
                    },
                    "limits": {
                        "cpu": "500m",
                        "memory": "1Gi",
                    },
                },
            },
            "dex": {
                "enabled": False,
            },
            "configs": {
                "params": {
                    "server.insecure": True,
                },
            },
        },
        skip_await=False,
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[argocd_namespace],
    ),
)

# Export cluster information
pulumi.export("vpc_id", vpc.vpc_id)
pulumi.export("vpc_cidr", vpc_cidr)
pulumi.export("public_subnet_ids", vpc.public_subnet_ids)
pulumi.export("private_subnet_ids", vpc.private_subnet_ids)
pulumi.export("aws_region", aws_region)
pulumi.export("cluster_name", cluster.eks_cluster.name)
pulumi.export("cluster_endpoint", cluster.eks_cluster.endpoint)
pulumi.export("cluster_security_group_id", cluster.cluster_security_group.id)
pulumi.export("kubeconfig", cluster.kubeconfig)
pulumi.export("oidc_provider_arn", cluster.core.oidc_provider.arn)
pulumi.export("oidc_provider_url", cluster.core.oidc_provider.url)
