---
sidebar_position: 3
---

# Port Configuration

This section describes the port architecture and configuration in the ROCK system.

## Port Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                              │
│              SDK / CLI / HTTP/WebSocket Clients                  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                       Service Layer                              │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐           │
│  │   Admin     │   │  Rocklet    │   │   Envhub    │           │
│  │   :8080     │   │  :22555     │   │   :8081     │           │
│  └─────────────┘   └─────────────┘   └─────────────┘           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                     Container Layer                              │
│         Dynamic Port Mapping (find_free_port)                    │
└─────────────────────────────────────────────────────────────────┘
```

## Service Ports

### Default Port Assignments

| Service | Default Port | Description |
|---------|--------------|-------------|
| Admin | 8080 | Main management service |
| Rocklet | 22555 | Sandbox runtime proxy |
| Rocklet WebSocket | 8000 | WebSocket service inside container |
| Envhub | 8081 | Environment registry service |

### Port Constants

| Port Type | Port Number | Purpose |
|-----------|-------------|---------|
| PROXY | 22555 | Rocklet proxy communication |
| SERVER | 8080 | WebSocket server |
| SSH | 22 | SSH service (inside container) |

## Configuration Methods

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ROCK_BASE_URL` | `http://localhost:8080` | Admin service URL |
| `ROCK_ENVHUB_BASE_URL` | `http://localhost:8081` | Envhub service URL |
| `ROCK_WORKER_ROCKLET_PORT` | None | Custom Rocklet port (uses 22555 if not set) |

### Command Line Parameters

**Admin Service:**

```bash
python -m rock.admin.main --port 8080 --env dev --role admin
```

**Rocklet Service:**

```bash
rocklet --host 0.0.0.0 --port 8000
```

**Envhub Service:**

```bash
python -m rock.envhub.server --host 0.0.0.0 --port 8081
```

### Configuration File (K8s)

```yaml
k8s:
  ports:
    proxy: 8000    # HTTP API port
    server: 8080   # WebSocket server port
    ssh: 22        # SSH service port
```

## Port Forwarding

### API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/sandboxes/{id}/portforward?port={port}` | External access endpoint |
| `/portforward?port={port}` | Rocklet internal endpoint |

### Port Forwarding Rules

| Rule | Value | Description |
|------|-------|-------------|
| Minimum Port | 1024 | Privileged ports (0-1023) excluded |
| Maximum Port | 65535 | Upper limit |
| Excluded Ports | 22 | SSH port not allowed for forwarding |

### Forwarding Flow

```
Client WebSocket → Admin Proxy → Rocklet /portforward → Container TCP Port
```

## Dynamic Port Allocation

When deploying containers, ROCK automatically allocates available ports:

- Uses `find_free_port()` to find available ports
- Maintains a registry of allocated ports to avoid conflicts
- Maps container internal ports to host ports dynamically

### Container Port Mapping

| Container Internal | Host Mapping | Description |
|-------------------|--------------|-------------|
| 22555 (PROXY) | Dynamic | Rocklet proxy port |
| 8080 (SERVER) | Dynamic | WebSocket service |
| 22 (SSH) | Dynamic | SSH service |

## Port Conflict Handling

### Validation Rules

1. Port must be `>=` 1024 (non-privileged)
2. Port must be `<=` 65535
3. Port must not be in the excluded list (SSH: 22)

### Conflict Avoidance

- Dynamic port allocation uses system-assigned free ports
- Global port registry tracks all allocated ports
- Retry mechanism with configurable attempts

## Multi-Node Deployment

### High-Availability Configuration

```
                    ┌─────────────┐
                    │Load Balancer│
                    │    :80      │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │   Admin     │ │   Proxy     │ │   Proxy     │
    │   :8080     │ │   :8080     │ │   :8080     │
    └─────────────┘ └─────────────┘ └─────────────┘
```

### Port Planning Recommendations

| Environment | Admin | Proxy | Rocklet | Envhub |
|-------------|-------|-------|---------|--------|
| Development | 8080 | - | 22555 | 8081 |
| Production (Single) | 8080 | - | 22555 | 8081 |
| Production (HA) | 8080 | 8080 | 22555 | 8081 |

## Troubleshooting

### Common Issues

| Issue | Possible Cause | Solution |
|-------|----------------|----------|
| Port already in use | Conflicting service | Change port or stop conflicting service |
| Connection refused | Service not started | Verify service status |
| Port forward failed | Port outside allowed range | Use port `>=` 1024 and `!=` 22 |

### Port Status Check

```bash
# Check port usage
netstat -tlnp | grep -E '8080|22555|8081'

# Check service status
curl http://localhost:8080/health
```
