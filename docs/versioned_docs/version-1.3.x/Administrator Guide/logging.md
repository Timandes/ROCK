---
sidebar_position: 1
---

# Logging Management

This section describes the log configuration and management of the ROCK system.

## Log Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ROCK_LOGGING_PATH` | None | Log storage path. Outputs to stdout if not set |
| `ROCK_LOGGING_FILE_NAME` | `rocklet.log` | Log file name |
| `ROCK_LOGGING_LEVEL` | `INFO` | Log level: DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `ROCK_TIME_ZONE` | `Asia/Shanghai` | Log timezone |

### Configuration Methods

**Command Line:**

```bash
export ROCK_LOGGING_PATH=/var/log/rock
export ROCK_LOGGING_LEVEL=INFO
```

**Docker Deployment:**

Pass environment variables via `-e` parameter. The log directory will be automatically mounted into the container.

**Kubernetes Deployment:**

Configure environment variables in the Deployment's `env` field and mount log storage via volume.

## Log Files

| File Name | Purpose |
|-----------|---------|
| `rocklet.log` | Rocklet service main log (default) |
| `rock.log` | Admin service main log |
| `scheduler.log` | Scheduler log |
| `billing.log` | Billing record log |
| `access.log` | HTTP access log |
| `command.log` | Sandbox command execution log |

## Log Format

Each log entry contains the following information:

```
Timestamp Level:SourceFile:LineNumber [LoggerName] [SandboxID] [TraceID] -- Message
```

**Example:**

```
2026-01-21T20:00:20.358+08:00 INFO:billing.py:11 [billing] [sandbox_123] [trace_abc] -- Billing record
```

**Field Description:**

- **Timestamp**: ISO 8601 format with timezone
- **Sandbox ID**: Associated sandbox instance identifier
- **Trace ID**: Used for distributed tracing, links requests across services

## Common Troubleshooting Scenarios

| Issue | Log to Check |
|-------|--------------|
| Service startup failure | Main log file |
| Sandbox creation timeout | `scheduler.log` |
| Command execution error | `command.log` |
| Billing issues | `billing.log` |
| HTTP request errors | `access.log` |

## Production Environment Recommendations

### Log Level Selection

| Environment | Recommended Level |
|-------------|-------------------|
| Development/Testing | DEBUG |
| Production | INFO |
| High-load Production | WARNING |

### Storage Recommendations

- **Container Environment**: Output to stdout, handled by centralized log collection system
- **Physical/Virtual Machine**: Set `ROCK_LOGGING_PATH` and configure external log rotation tools

### Important Notes

- Log files are overwritten on each service startup
- Long-running deployments require external log rotation configuration
- Recommended to use a centralized log collection system

## Log Collection Integration

ROCK logs are in plain text format and can be integrated via standard methods:

- **ELK Stack**: Filebeat → Logstash → Elasticsearch
- **Fluentd**: Tail plugin collection
- **Cloud Platforms**: Alibaba Cloud SLS, Tencent Cloud CLS, etc.

Key Configuration:

- Log path: `$ROCK_LOGGING_PATH` or container stdout
- Encoding: UTF-8
- Time format: ISO 8601