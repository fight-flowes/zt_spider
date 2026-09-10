# IoT 设备历史数据爬虫

从 `ztx.zcgc.cn` 获取服务区 IoT 设备的历史功率数据，并导出为 CSV。项目只有一个入口 `scripts/spider`，所有操作均为无交互命令。

## 安装

```bash
cd /home/leisaihua/workspace/zt_analyse/code/spider
python3 -m pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中设置完整的浏览器 Cookie 请求头：

```dotenv
IOT_COOKIE=SESSION=...; access_token=...
```

也可通过进程环境变量设置 `IOT_COOKIE`，其优先级高于 `.env`。Cookie 过期后只需更新该值；程序不会询问、保存或输出 Cookie。

## 命令

```bash
# 查看全部命令
scripts/spider help

# 检查 Cookie 和接口；默认使用清单中的第一台设备
scripts/spider check
scripts/spider check --device 10018540

# 爬取全部设备
scripts/spider crawl --all

# 爬取一个或多个服务区
scripts/spider crawl --area 00_长安服务区
scripts/spider crawl --area 00_长安服务区 --area 01_临潼服务区

# 爬取一个或多个设备，服务区由设备清单确定
scripts/spider crawl --device 10018540
scripts/spider crawl --device 10018540 --device 10018541

# 转换逐设备 CSV，并生成合并宽表和设备映射
scripts/spider export

# 查看设备清单与本地数据统计
scripts/spider stats
```

`crawl` 必须显式指定 `--all`、`--area` 或 `--device`，三者不能混用。它还支持：

```text
--start YYYY-MM-DD   起始日期，默认 2022-01-01
--end YYYY-MM-DD     截止日期，默认当天
--force              重新请求并替换已有的成功 JSON
```

默认情况下，已有且内容有效、`code` 为 `200` 的 JSON 会被跳过，可直接重复执行实现断点续爬。单台设备失败不会中断其他设备；只有成功响应才会原子替换目标文件。

爬取时会显示已处理数量、百分比以及下载、跳过和失败计数。终端中进度会在同一行刷新；输出重定向到日志时，每次更新独占一行。

如需指定其他 Python 解释器：

```bash
SPIDER_PYTHON=/path/to/python scripts/spider stats
```

## 数据

设备清单必须包含 `deviceId,service_area` 两列，支持 UTF-8（含 BOM）和 GBK 编码。

```text
data/deviceId_info_all.csv                 设备清单
data/local/{服务区}/{设备ID}.json          原始 API 响应
data/csv/{服务区}/{设备ID}.csv             逐设备 time,value 数据
data/csv/merged_power_data.csv             按 time 外连接的宽表
data/csv/deviceId_info.csv                 已导出设备与服务区映射
```

接口实际可返回的最早时间取决于服务端的数据保留策略。

## 退出码

- `0`：命令成功完成。
- `1`：认证、网络、接口、爬取或导出过程中存在失败。
- `2`：参数或本地配置错误，例如缺少 `IOT_COOKIE`、设备清单不可读。

## 安全

Cookie 等同于临时登录凭证。`.env` 已被 Git 忽略，请勿提交或分享；也不要把真实 Cookie 写入命令参数、源码或问题日志。
