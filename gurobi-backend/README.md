# Gurobi SAN-A* 后端

GitHub Pages只能托管前端，不能运行Python或Gurobi。本目录需部署到一台具有有效Gurobi许可证的Python服务器，前端再填写其 `/solve` 地址。

## 本地启动

```bash
python -m venv .venv
source .venv/bin/activate   # Windows使用 .venv\Scripts\activate
pip install -r requirements.txt
export ALLOWED_ORIGINS=https://Tye0426.github.io
uvicorn app:app --host 0.0.0.0 --port 8000
```

Windows PowerShell设置来源：

```powershell
$env:ALLOWED_ORIGINS="https://Tye0426.github.io"
uvicorn app:app --host 0.0.0.0 --port 8000
```

健康检查：`GET /health`。求解接口：`POST /solve`。

后端接收页面的 `searchStrategy`（`sanr`、`astar`、`dfs`、`brfs`、`best`、`cbfs`）。独立的SAN-R仅运行单路径奖励基线；其余策略先尝试短时SAN-R，必要时再通过合法SAN动作构造可行解，随后以该解作为上界运行所选分支树搜索。返回 `seedMethod`、`status`、`termination` 和每一步作业记录；时间或解池截断时有方案只标记为可行，不宣称全局最优。该模型仍与论文的有环SAN及延迟约束存在差异，详见上层《算法说明.md》。

生产环境必须使用HTTPS，否则HTTPS的GitHub Pages页面会阻止调用HTTP接口。Gurobi许可证由后端部署者自行配置；不要把许可证文件或密钥提交到GitHub。
