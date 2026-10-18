# AI服务器 监控系统 Prometheus 部署文档

- **服务器**：192.168.1.5（Ubuntu · RTX 5090 32G · 62G RAM）
- **部署日期**：2026-10-10
- **部署方式**：Docker Compose（与 WeKnora 同规范）
- **状态**：✅ 全部完成并验证通过

---

## 1. 概述

为 AI 服务器（vLLM ×3 + WeKnora 全家桶）部署显存/内存/CPU/磁盘实时监控与预警，重点预防 **RAM OOM**（此前服务器曾因内存压力影响连接）。

监控系统由 5 个开源组件组成，**不占显存、仅占约 1G 内存**，不干扰现有 vLLM / WeKnora 服务。

## 2. 组件清单

| 组件 | 镜像 | 端口 | 作用 |
|---|---|---|---|
| node_exporter | prom/node-exporter:v1.8.2 | **9102** | 内存/CPU/磁盘 |
| dcgm-exporter | nvidia/dcgm-exporter:4.6.1-4.8.4 | **9400** | GPU 显存/温度/利用率/功耗（DCGM 官方） |
| Prometheus | prom/prometheus:v2.53.3 | 9090 | 指标存储 + 告警规则 |
| Alertmanager | prom/alertmanager:v0.27.0 | 9093 | 告警分发 |
| Grafana | grafana/grafana:11.3.1 | **9300** | 可视化看板 |

> **端口避让说明**：Grafana 常用 3000 已被 `WeKnora-langfuse-web` 占用 → 改 **9300**；node_exporter 常用 9100 已被 `langfuse-minio` 占用 → 改 **9102**；dcgm-exporter 用 **9400**（原计划端口，未被占用）。已按现有端口规范避让。

## 3. 目录与数据规范

- 配置目录：`/home/ubuntu/monitoring/`（与 weknora 同级，符合应用目录规范）
- 配置文件：
  - `docker-compose.yml` — 5 服务编排
  - `prometheus.yml` — 抓取配置
  - `rules.yml` — 告警规则
  - `alertmanager.yml` — 告警路由（钉钉 webhook 待配）
  - `gpu_textfile.sh` — GPU 指标采集脚本（cron 每分钟执行）
- 数据卷：docker named volumes（`monitoring-prometheus` / `monitoring-alertmanager` / `monitoring-grafana`），落盘 `/data/docker/volumes`
- GPU 指标临时文件：`/home/ubuntu/monitoring/textfile/gpu.prom`
- 防火墙：**放行 9300/tcp**（Grafana 对外访问）、**9090/tcp**（Prometheus 管理页），其余监控端口内网访问

## 4. 部署步骤

### 4.1 写配置文件
在 `/home/ubuntu/monitoring/` 下创建 `docker-compose.yml`（内容见本文件附录），包含 4 个服务：node-exporter、prometheus、alertmanager、grafana。

### 4.2 镜像拉取（国内源）
服务器直连 Docker Hub 超时，须用国内源手动拉取并 tag：

```bash
docker pull docker.m.daocloud.io/prom/node-exporter:v1.8.2
docker tag docker.m.daocloud.io/prom/node-exporter:v1.8.2 prom/node-exporter:v1.8.2
# prometheus / alertmanager / grafana 同法
```

> **注意**：`nvidia/dcgm-exporter` 镜像在国内源不可用（daocloud 拒绝、其他源不存在），故 **GPU 显存监控改用 node_exporter textfile + nvidia-smi 方案**（见 4.3），效果等同，无需额外镜像。

### 4.3 GPU 指标采集（dcgm-exporter 官方方案）

GPU 监控采用 **NVIDIA 官方 dcgm-exporter**（`nvidia/dcgm-exporter:4.6.1-4.8.4`），DCGM 库随镜像自带，识别 RTX 5090 输出 20 个 DCGM 官方指标。

**接入背景**：服务器 Docker 未配置 nvidia-container-toolkit（vLLM 走 systemd 不用容器 GPU），为不影响现有 15 个容器，采用**手动挂载 GPU 设备 + 驱动库**的隔离方式接入，不改 Docker 全局配置：

- 挂载 `/dev/nvidia0`、`/dev/nvidiactl`、`/dev/nvidia-modeset`、`/dev/nvidia-uvm`、`/dev/nvidia-uvm-tools`
- 挂载宿主机 `libnvidia-ml.so.1`（驱动 595.99.02）到容器内
- 端口 `9400`，输出 `/metrics`

采集到的主要指标（DCGM_FI_*）：`DCGM_FI_DEV_FB_FREE/USED`（显存空闲/已用，MiB）、`DCGM_FI_DEV_GPU_TEMP`（温度）、`DCGM_FI_DEV_GPU_UTIL`（利用率）、`DCGM_FI_DEV_POWER_USAGE`（功耗）、SM/内存时钟、编解码/拷贝利用率、PCIe 等。

> **旧 textfile 方案已退役**：此前因国内源拉不到 dcgm-exporter 曾用 node_exporter textfile + nvidia-smi 脚本过渡；现用户提供镜像 tar（`docker load` 导入），已切换为 dcgm-exporter，并停止 `gpu_textfile.sh` 的 cron 任务（指标名由 `node_gpu_*` 改为 `DCGM_FI_*`）。

### 4.4 启动
```bash
cd /home/ubuntu/monitoring && docker compose up -d
```

### 4.5 验证
```bash
curl http://192.168.1.5:9090/-/healthy        # Prometheus Healthy
curl http://192.168.1.5:9090/api/v1/targets   # node target health=up
curl 'http://192.168.1.5:9090/api/v1/query?query=node_gpu_memory_free_bytes'  # 返回显存值
```

## 5. 告警规则（rules.yml）

| 规则 | 触发条件 | 级别 |
|---|---|---|
| 内存可用不足 | MemAvailable < 15G（持续2m） | warning |
| **内存严重不足(OOM高风险)** | MemAvailable < 10G（持续1m） | **critical** |
| **GPU显存不足** | 显存空闲 < 8%（持续1m） | **critical** |
| GPU温度过高 | 温度 > 85℃ | warning |
| 磁盘使用率过高 | 根分区 > 85% | warning |
| 监控目标不可达 | 采集失败 | critical |

## 6. Grafana 看板

- **地址**：http://192.168.1.5:9300
- **账号/密码**：`admin` / `<WEPASSWORD>`
- **数据源**：Prometheus（URL `http://monitoring-prometheus:9090`，已设为默认）
- **看板**：`AI服务器监控`（uid: ai-server-mon），含 12 个面板：
  - 内存使用率 / 可用内存 / 内存历史(GB)
  - **GPU显存使用率 / 显存空闲 / 显存历史(GB) / GPU利用率 / GPU温度 / GPU功耗**（DCGM 指标）
  - CPU使用率 / 系统负载
  - 根分区磁盘使用

## 7. 告警通知配置（邮箱 · 已启用）

告警通过 **QQ邮箱 SMTP** 发送到 `63817027@qq.com`（已配置并验证）。

- SMTP 服务器：`smtp.qq.com:587`（STARTTLS；QQ 465 端口为隐式 SSL 不被 Alertmanager 支持，须用 587）
- 发件/收件：`63817027@qq.com`
- 已用测试告警验证邮件通道正常（无发送失败日志）
- 修改配置：编辑 `/home/ubuntu/monitoring/alertmanager.yml` 后 `docker compose restart alertmanager`

> 若日后改用钉钉/企业微信，替换 receivers 的 `email_configs` 为对应 webhook 即可。
> 未发送成功时告警仍会在 Prometheus/Alertmanager 中记录，只是不推送。

## 8. 访问方式

| 服务 | 地址 | 防火墙 |
|---|---|---|
| Grafana 看板 | http://192.168.1.5:9300 | ✅ 已放行(对外) |
| Prometheus | http://192.168.1.5:9090 | ✅ 已放行(管理) |
| Alertmanager | http://192.168.1.5:9093 | 内网 |

> **Grafana 界面语言**：已通过 API 设为中文简体（zh-Hans），刷新页面生效。仅界面框架中文化，指标名（如 `node_gpu_memory_free_bytes`）为英文属 Prometheus 通用规范。
> **Prometheus 无中文界面**（官方仅英文），日常看数据/告警用 Grafana，9090 用于排障与手动 PromQL 查询。

## 9. 运维与故障

- **查看告警**：Prometheus 首页 → Alerts；Grafana 看板实时曲线
- **查 OOM 历史**：监控装好后，内存历史(GB)面板可直接回看，无需再翻日志
- **重启监控**：`cd /home/ubuntu/monitoring && docker compose restart`
- **改配置**：修改 yml 后 `docker compose up -d`（prometheus 支持 `/-/reload` 热重载）
- **清理数据**：`docker compose down -v`（会清空监控历史数据，慎用）

## 10. 验证结果（2026-10-10 实测）

| 检查项 | 结果 |
|---|---|
| 5 个监控容器 | ✅ 全部 Up |
| Prometheus 健康 | ✅ Healthy |
| node 采集目标 | ✅ health=up（抓取耗时 0.14s） |
| **dcgm 采集目标** | ✅ health=up（抓取耗时 0.009s） |
| **GPU DCGM 指标** | ✅ 识别 RTX 5090，显存空闲 5149MiB/已用 26961MiB、温度26℃、功耗19.7W |
| 内存可用指标 | ✅ 查询到 36.5G |
| 告警规则 | ✅ 6 条加载，health=ok（GPU 用 DCGM 指标） |
| Grafana 数据源 | ✅ Prometheus 默认数据源 |
| Grafana 看板 | ✅ AI服务器监控（12面板，含 DCGM+功耗）导入成功 |
| Grafana 中文界面 | ✅ 已设为 zh-Hans |
| 防火墙 9300 / 9090 | ✅ 均已放行 |
| 邮箱告警 | ✅ 已启用，测试邮件用户已确认收到 |

---

## 附录：docker-compose.yml

```yaml
services:
  node-exporter:
    image: prom/node-exporter:v1.8.2
    container_name: monitoring-node-exporter
    restart: unless-stopped
    command:
      - '--path.rootfs=/host'
      - '--web.listen-address=:9102'
      - '--collector.textfile.directory=/textfile'
    pid: host
    ports:
      - '9102:9102'
    volumes:
      - '/:/host:ro,rslave'
      - '/home/ubuntu/monitoring/textfile:/textfile:ro'

  prometheus:
    image: prom/prometheus:v2.53.3
    container_name: monitoring-prometheus
    restart: unless-stopped
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./rules.yml:/etc/prometheus/rules.yml:ro
      - monitoring-prometheus:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=15d'
      - '--web.enable-lifecycle'
    ports:
      - '9090:9090'
    depends_on:
      - node-exporter

  alertmanager:
    image: prom/alertmanager:v0.27.0
    container_name: monitoring-alertmanager
    restart: unless-stopped
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
      - monitoring-alertmanager:/alertmanager
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
      - '--storage.path=/alertmanager'
    ports:
      - '9093:9093'

  grafana:
    image: grafana/grafana:11.3.1
    container_name: monitoring-grafana
    restart: unless-stopped
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=<WEPASSWORD>
      - GF_SERVER_ROOT_URL=http://192.168.1.5:9300
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - monitoring-grafana:/var/lib/grafana
    ports:
      - '9300:3000'
    depends_on:
      - prometheus

  dcgm-exporter:
    image: nvidia/dcgm-exporter:4.6.1-4.8.4
    container_name: monitoring-dcgm-exporter
    restart: unless-stopped
    devices:
      - /dev/nvidia0:/dev/nvidia0
      - /dev/nvidiactl:/dev/nvidiactl
      - /dev/nvidia-modeset:/dev/nvidia-modeset
      - /dev/nvidia-uvm:/dev/nvidia-uvm
      - /dev/nvidia-uvm-tools:/dev/nvidia-uvm-tools
    volumes:
      - /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1:/usr/local/nvidia/lib64/libnvidia-ml.so.1
      - /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1:/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1
    environment:
      - DCGM_EXPORTER_LISTEN_PORT=9400
      - LD_LIBRARY_PATH=/usr/local/nvidia/lib64:/usr/lib/x86_64-linux-gnu
    ports:
      - '9400:9400'

volumes:
  monitoring-prometheus:
  monitoring-alertmanager:
  monitoring-grafana:
```
