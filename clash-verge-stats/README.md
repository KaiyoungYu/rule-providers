# Clash Verge Rev 域名流量仪表盘

按连接 ID 读取 mihomo `/connections` 的下载和上传字节计数，计算每次采样的增量，并按域名（无域名时按目标 IP）汇总。数据保存在本机 `traffic.sqlite3`，展示滚动最近 24 小时，按分钟划分时间窗口。适用于 Python 3.10+ 和本机运行的 Clash Verge Rev / mihomo。

## 启动

在本目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run clash_web_dash.py --server.address 127.0.0.1
```

Windows PowerShell 可使用：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:CLASH_API_URL = 'http://127.0.0.1:9097'
$env:CLASH_SECRET = '你的 API 密钥'
python -m streamlit run clash_web_dash.py --server.address 127.0.0.1
```

浏览器打开终端提示的本机地址，通常是 `http://127.0.0.1:8501`。首次打开网页后开始采集。保持终端进程运行，关闭浏览器标签页不会停止采集；按 `Ctrl+C` 停止。电脑休眠、Clash 内核停止或仪表盘进程退出期间无法采集，重新打开页面会从现有数据库继续展示最近 24 小时内已记录的数据。

macOS 上会自动读取 Clash Verge Rev 配置中的 `external-controller-unix` 路径，使用本地 Unix Socket。请确保 Clash Verge Rev 内核已启动。若版本或系统只提供 TCP 控制接口，可在启动前设置：

```bash
export CLASH_API_URL='http://127.0.0.1:9097'
export CLASH_SECRET='你的 API 密钥'
python -m streamlit run clash_web_dash.py --server.address 127.0.0.1
```

端口和密钥请以 Clash Verge Rev 的“设置 → 外部控制”页面为准。URL 可以是控制服务根地址或以 `/connections` 结尾。若没有密钥，可不设置 `CLASH_SECRET`。也可用 `CLASH_SOCKET` 显式指定 Unix Socket 路径。请勿把真实密钥写进代码、README 或提交到 Git。仪表盘只绑定 `127.0.0.1`；若用 HTTP 连接远程控制接口，密钥会以明文在网络上传输，应改用 HTTPS 或本机 Socket。

## 统计边界

- 启动后的**首次成功采样**以及监控中断超过 10 秒后的首次采样用作基线，不把停机期间的流量补算到恢复的那一分钟。
- `/connections` 只返回仍活跃的连接。1 秒采样间隔内开始并结束的短连接可能被漏掉；停机期间也无法补采。
- “域名 / 目标地址”是单条连接提供的目标域名或 IP，不等同于浏览器中的完整网站、页面 URL 或按主域名合并的站点。
- 数据和导出的 CSV 含浏览目标，请妥善保管本机 `traffic.sqlite3` 与报表。

## 发布到 GitHub

`.gitignore` 已排除虚拟环境、数据库及其 WAL 文件、运行日志和 Python 缓存。发布前运行 `git status --short`，确认没有添加浏览目标记录、密钥或个人配置文件。公开仓库如需允许他人使用和修改代码，请自行选择合适的开源许可证。
