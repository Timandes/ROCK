---
sidebar_position: 2
---

# Role System

This section describes the role architecture and configuration in the ROCK system.

## Role Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Layer                                │
│         SDK Users / CLI Operators / Tenant Users                 │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      Service Layer                               │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐           │
│  │ Admin Role  │   │ Proxy Role  │   │  Envhub     │           │
│  │  (Manage)   │   │  (Proxy)    │   │  (Registry) │           │
│  └─────────────┘   └─────────────┘   └─────────────┘           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                    Execution Layer                               │
│         Worker Nodes / Rocklet Runtime Agents                    │
└─────────────────────────────────────────────────────────────────┘
```

## Service Roles

The Admin service supports two runtime roles specified via the `--role` parameter:

| Role | Purpose | Responsibilities |
|------|---------|------------------|
| `admin` | Management node | Sandbox create/destroy, scheduling, resource allocation, Ray cluster initialization |
| `proxy` | Proxy node | Command execution, file read/write, session management, status queries, port forwarding |

### Feature Separation

| Operation Type | Admin Role | Proxy Role |
|----------------|------------|------------|
| Sandbox create/destroy | ✓ | ✗ |
| Scheduler tasks | ✓ | ✗ |
| Command execution | ✓ | ✓ |
| File operations | ✓ | ✓ |
| Status queries | ✓ | ✓ |
| Port forwarding | ✓ | ✓ |
| Session management | ✓ | ✓ |

### Deployment Recommendations

- Deploy multiple Proxy nodes in production to distribute read requests
- Admin nodes typically require only 1-2 instances

## Component Roles

| Component | Role | Description |
|-----------|------|-------------|
| **SDK** | Client | Environment development toolkit for developers |
| **CLI** | Operator | Command-line management tool |
| **Admin** | Orchestrator | Sandbox lifecycle scheduling node |
| **Worker** | Executor | Physical resource allocation node |
| **Rocklet** | Runtime Agent | Lightweight proxy service inside containers |
| **Envhub** | Registry | Environment data storage and CRUD service |

## Tenant User Identification

Tenant information is passed through HTTP request headers:

| Header | Default | Description |
|--------|---------|-------------|
| `X-User-Id` | `default` | User identifier |
| `X-Experiment-Id` | `default` | Experiment/project identifier |
| `X-Namespace` | `default` | Namespace (for isolation) |
| `X-Key` | `default` | Authorization token (encrypted) |
| `X-Cluster` | `default` | Cluster identifier |

### Multi-tenant Isolation

- Resources under the same `namespace` are isolated from each other
- `X-Key` is used for authorization verification

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ROCK_ADMIN_ENV` | `dev` | Runtime environment: local / dev / test |
| `ROCK_ADMIN_ROLE` | `write` | Admin role identifier (for monitoring) |
| `ROCK_WORKER_ENV_TYPE` | `local` | Worker type: local / docker |
| `ROCK_WORKER_ROCKLET_PORT` | None | Custom Rocklet port |

## Typical Deployment Architecture

### Single-node Deployment

```
Admin (role=admin) + Rocklet
```

### High-availability Deployment

```
                    ┌─────────────┐
                    │Load Balancer│
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │   Admin     │ │   Proxy     │ │   Proxy     │
    │ (role=admin)│ │ (role=proxy)│ │ (role=proxy)│
    └─────────────┘ └─────────────┘ └─────────────┘
           │               │               │
           └───────────────┼───────────────┘
                           ▼
                    ┌─────────────┐
                    │    Redis    │
                    │(Shared State)│
                    └─────────────┘
```

## Authorization Mechanism

ROCK uses token-based authorization rather than traditional RBAC:

- **Authorization Verification**: Via encrypted token `X-Key`
- **Namespace Isolation**: Via `X-Namespace` for multi-tenant isolation
- **Read/Write Separation**: Admin/Proxy roles separate management/runtime operations

## Monitoring Identifiers

All monitoring metrics include role attributes for differentiation:

| Attribute | Description |
|-----------|-------------|
| `host` | Hostname |
| `pod` | Pod identifier |
| `ip` | IP address |
| `env` | Environment identifier |
| `role` | Role identifier |
