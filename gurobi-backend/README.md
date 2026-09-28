# Gurobi联合SAN-A*后端

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

健康检查：`GET /health`。求解接口：`POST /solve`。联合SAN版本会返回 `backendVersion: 2.0.0`、`gurobiMode: integrated-two-stage` 和 `sourceTrackDecision: joint-t-variables`，可据此确认服务器已经更新。部署后还可访问 `GET /self-test`：它会实际建立并求解一个小型非平凡案例，同时检查Gurobi许可证、联合SAN候选生成和外层方案搜索；返回的 `ok` 应为 `true`。

## 联合SAN整数规划

每个外层状态只建立一个Gurobi模型。模型同时创建全部非空股道的源股道选择变量 `t[k]` 和车辆弧变量 `x[k,a]`，通过 `Σt[k]=1` 让Gurobi联合决定本轮源股道与车辆分组/推送路径。解池在所有源股道方案之间统一排序，`poolSize`表示整个联合SAN模型的解池上限，不再是每条源股道各自的上限。

第一阶段使用 `PoolSearchMode=1` 快速取得联合候选，默认最多0.5秒；获得完整方案上界后，第二阶段对同一个联合模型使用 `PoolSearchMode=2` 系统性补充高质量候选，默认最多3秒。只有联合解池没有产生可解码合法动作时才启用确定性整组移动回退。

可通过环境变量调整：

```powershell
$env:GUROBI_SAN_FAST_MAX_SECONDS="0.50"
$env:GUROBI_SAN_MODEL_MAX_SECONDS="3.0"
$env:GUROBI_THREADS="0"
```

`GUROBI_THREADS=0`表示由Gurobi自动选择线程数。通常不建议把强化阶段单模型上限提高到5秒以上，否则困难的联合SAN模型仍可能占用大量总搜索时间。

后端接收页面的 `searchStrategy`（`auto`、`sanr`、`astar`、`dfs`、`brfs`、`best`、`cbfs`）。独立的SAN-R仅运行单路径奖励基线；其余策略先尝试短时SAN-R，必要时再通过合法SAN动作构造可行解，随后以该解作为上界运行所选分支树搜索。返回 `seedMethod`、`status`、`termination` 和每一步作业记录；时间或解池截断时有方案只标记为可行，不宣称全局最优。该模型仍与论文的有环SAN及延迟约束存在差异，详见上层《算法说明.md》。

生产环境必须使用HTTPS，否则HTTPS的GitHub Pages页面会阻止调用HTTP接口。Gurobi许可证由后端部署者自行配置；不要把许可证文件或密钥提交到GitHub。
