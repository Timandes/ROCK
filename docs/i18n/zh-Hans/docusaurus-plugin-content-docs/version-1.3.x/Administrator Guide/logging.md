---
sidebar_position: 1
---

# 日志管理

本章节介绍 ROCK 系统的日志配置与管理方法。

## 日志配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ROCK_LOGGING_PATH` | 无 | 日志存储路径。未设置时输出到标准输出 |
| `ROCK_LOGGING_FILE_NAME` | `rocklet.log` | 日志文件名 |
| `ROCK_LOGGING_LEVEL` | `INFO` | 日志级别：DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `ROCK_TIME_ZONE` | `Asia/Shanghai` | 日志时区 |

### 配置方式

**命令行：**

```bash
export ROCK_LOGGING_PATH=/var/log/rock
export ROCK_LOGGING_LEVEL=INFO
```

**Docker 部署：**

通过 `-e` 参数传递环境变量，日志目录会自动挂载到容器内。

**Kubernetes 部署：**

在 Deployment 的 `env` 字段配置环境变量，并通过 volume 挂载日志存储。

## 日志文件说明

| 文件名 | 用途 |
|--------|------|
| `rocklet.log` | Rocklet 服务主日志（默认） |
| `rock.log` | Admin 服务主日志 |
| `scheduler.log` | 调度器日志 |
| `billing.log` | 计费记录日志 |
| `access.log` | HTTP 访问日志 |
| `command.log` | 沙箱命令执行日志 |

## 日志格式

每条日志包含以下信息：

```
时间戳 级别:源文件:行号 [日志器名] [沙箱ID] [追踪ID] -- 消息内容
```

**示例：**

```
2026-01-21T20:00:20.358+08:00 INFO:billing.py:11 [billing] [sandbox_123] [trace_abc] -- 计费记录
```

**字段说明：**

- **时间戳**：ISO 8601 格式，含时区
- **沙箱ID**：关联的沙箱实例标识
- **追踪ID**：用于链路追踪，跨服务请求可串联

## 常见排查场景

| 问题场景 | 查看日志 |
|----------|----------|
| 服务启动失败 | 主日志文件 |
| 沙箱创建超时 | `scheduler.log` |
| 命令执行异常 | `command.log` |
| 计费问题 | `billing.log` |
| HTTP 请求异常 | `access.log` |

## 生产环境建议

### 日志级别选择

| 环境 | 推荐级别 |
|------|----------|
| 开发/测试 | DEBUG |
| 生产 | INFO |
| 高负载生产 | WARNING |

### 存储建议

- **容器环境**：输出到 stdout，由日志收集系统统一处理
- **物理机/虚拟机**：设置 `ROCK_LOGGING_PATH`，配合外部日志轮转工具

### 注意事项

- 服务每次启动会覆盖同名日志文件
- 长期运行需配置外部日志轮转机制
- 建议使用日志收集系统进行集中管理

## 日志收集集成

ROCK 日志为纯文本格式，可通过标准方式接入：

- **ELK Stack**：Filebeat → Logstash → Elasticsearch
- **Fluentd**：Tail 插件采集
- **云平台**：阿里云 SLS、腾讯云 CLS 等

关键配置：

- 日志路径：`$ROCK_LOGGING_PATH` 或容器 stdout
- 编码：UTF-8
- 时间格式：ISO 8601
