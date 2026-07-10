# easyLattice

**中文** | [English](README.md)

面向格密码参数选择的本地优先、开源原型工具。

公开的 GitHub Pages 页面只是浏览器 UI，本身不提供共享后端，也不会调用 LLM、Sage
或 `lattice-estimator`。运行下文的本地运行器后，同一页面只会连接本机回环地址，
实时 Sage 和 estimator 计算均在用户本机完成，无需 clone 本仓库或编辑
`config.local.json`。

## 概览

easyLattice 采用分层设计，默认流程不需要 LLM token：

1. 确定性核心：固定搜索策略和本地安全筛选；
2. estimator 适配层：可选的用户自备 Sage/lattice-estimator 验证；
3. agent 层：默认确定性运行，可选 LLM 意图解析；
4. provider 层：用户自有的 OpenAI-compatible endpoint 与认证信息。

RLWE 选择器支持：

- 二次幂分圆环 `Z_q[x] / (x^n + 1)`；
- 三元分圆环 `Z_q[x] / (x^n - x^(n/2) + 1)`，其中 `n` 为偶数且素因子只含
  `2` 与 `3`；
- 适合 NTT 的素数模数，并满足 `n | q - 1`；优先完全分裂的 `2n | q - 1`，
  但保留一层未分解 NTT 也视为接近可接受；
- 中心二项分布和 iid 稀疏三元秘密分布；
- 对 LWE/RLWE/MLWE 独立搜索中心二项或 iid 稀疏三元误差分布；稀疏三元会使用
  固定权重 estimator 近似；
- 对 LWR/RLWR/MLWR 使用由所选 `q -> p` 压缩模数生成的压缩噪声误差；
- 本地快速筛选，以及可选的 Sage/lattice-estimator 验证。

同一个 agent API 也提供初始版 NTRU 选择器：

- `Z_q[x] / (x^n + 1)` 上的二次幂分圆 NTRU，对应 NEV/BAT/DAWN 风格变体常用
  的环族；
- 二次幂环使用宽松的 NTT 默认条件 `n/2 | q - 1`；
- 两阶段分布选择：先用离散高斯代理校准最小标准差，再选取标准差不低于下界且最接近
  的快速采样分布。快速分布可以是单个块，也可以是稀疏三元、对称均匀与中心二项块的
  短和。求和分布作为 estimator 的矩近似，并受高斯校准结果限制；
- HPS-like 和 HRSS-like 对比候选；
- 可选的本地 NTRU 验证：同一组参数会用 MATZOV 与 ADPS16 的经典、量子规约代价
  模型评估。量子 NTRU 目标必须启用 Sage 估计，不能使用仅有经典参考值的快速筛选。

## 搜索模型

用户请求的安全比特被视为下界。选择器先确定多项式/环族，再选择维度 `n`，然后找出
满足所选 NTT 规模的最小模数，最后选择秘密与误差分布。在固定模数内，它会避免不必要
的安全余量。

JSON 输出会分开给出 `secret` 与 `error` 字段。对于 LWE/RLWE/MLWE，原型独立搜索
`Xs` 和 `Xe`。对于 LWR/RLWR/MLWR，秘密选择器控制 `Xs`，而误差控件是压缩模数
`p`。误差分布是将 `vi in {0, ..., q-1}` 从 `q` 压缩到 `p` 再 lift 回 `q` 所诱导
的中心化压缩噪声分布。

快速筛选使用预期的单调性：更小的 `q`、更大的维度和更大的误差标准差通常会提高
LWE/RLWE 难度。正确性和方案编码可能施加相反约束，因此这些检查应放在具体方案模块中。

对于稀疏三元候选，easyLattice 包含
`Pr[+1] = Pr[-1] = (2^l0 - 1) / 2^(2*l0 + l1)`，其余概率在 `0` 上的分布。
这类分布可用 bit 操作低成本采样。由于 `lattice-estimator` 以固定汉明重量建模稀疏
三元向量，easyLattice 会将期望 `+1` 和 `-1` 个数作为固定权重近似传入，并在 JSON
输出中报告该近似。

easyLattice 是本地工具，不是托管的参数认证服务。用户自备 estimator 安装、可选模型
endpoint/API key，以及具体方案所需的纠错、拒绝采样或 smoothing 参数脚本。除非本地
设置 `llm.enabled=true`，否则 LLM 层保持关闭；启用后它也只会把自由文本意图转换为
确定性搜索约束。

## 公开网页与本地运行器

托管 UI 不提供共享计算服务。下载
[`easyLattice-runner.pyz`](https://github.com/Icarid-Liu/easyLattice/releases/latest/download/easyLattice-runner.pyz)，
并使用 Python 3.10 或更高版本运行：

```bash
python3 easyLattice-runner.pyz
```

运行器固定监听 `127.0.0.1:8127`，并会从 `SAGE_BINARY`、
`LATTICE_ESTIMATOR_PATH`、命令路径和常见本地目录检测 Sage 与
`lattice-estimator`。之后直接打开普通 GitHub Pages 地址即可自动发现正在运行的
运行器。只有自动检测不完整时，网页才要求填写 Sage 可执行文件和 estimator 根目录；
根目录必须包含 `estimator/__init__.py`，无需填写 API Base 或复制 token。

运行器只监听 `127.0.0.1`，并使用进程生命周期内有效的随机 token。只有已配置的公开
页面来源能取得这个 token，后续所有 API 请求仍必须携带它。本地路径、Sage 输出、
estimator 源码和 API key 不会上传到公开站点。

从本仓库构建开发版：

```bash
python3 scripts/build-runner.py
python3 dist/easyLattice-runner.pyz
```

下表中的原型设置仅作示例。实时输出请使用本地运行器，展示数值不构成参数认证。

表格固定使用以下控件：

- 目标安全比特：`128`；
- 安全度量：`Classical`；
- 规约代价模型：`MATZOV`；
- 分布：`Auto`；
- 环族：`x^n + 1`；
- NTT 规模：`n/2 | q - 1`；
- estimator 验证：关闭。

| 公开 UI 选项 | n | q | NTT 条件 | 秘密分布 | 误差分布 | LWR p | 经典比特 | 状态 |
| --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- |
| NTRU / 矩阵 | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | 相同 | - | 128.0 | 示例 |
| NTRU / 环 | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | 相同 | - | 128.0 | 示例 |
| LWE / LWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 示例 |
| LWE / RLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 示例 |
| LWE / LWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | 示例 |
| LWE / RLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | 示例 |
| LWE / MLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 示例 |
| LWE / MLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | 示例 |
| SIS / SIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 分类占位 |
| SIS / MSIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 分类占位 |

`SIS / SIS` 和 `SIS / MSIS` 目前只出现在 UI 分类中，真正的 SIS/MSIS 选择器尚未
实现。表中这些行复用了当前 LWE/RLWE 快速筛选框架，不应解读为 SIS 难度估计。

## 解密错误率

本地 UI 提供独立的有限分布 DFR 计算器，使用以下纠错前系数模型：

- NTRU：`p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e`；
- LWE：`((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2`。

成功边界为 `|E| <= Delta`。计算器会以 `log2(DFR)` 给出单系数和向量 union bound
DFR。显式原始概率字段保留在 API 和复制的 JSON 中，供外部 ECC 计算使用。默认工作
精度为 512 比特；离散高斯使用可配置的 128 比特尾界。

输入支持常见的 `lattice-estimator` 分布族、LWR 向下取整压缩、Kyber 最近整数压缩和
自定义有限 PMF JSON 对象。estimator 的 `NoiseDistribution` 只公开矩而不提供唯一
采样律，因此 DFR 计算时必须为它提供自定义 PMF。固定权重稀疏三元输入使用其单系数
边缘分布，并报告由此产生的相关性近似。

计算器刻意不建模纠错码。LAC、DAWN 等带编码的方案应将纠错前输出交给具体方案的
纠错概率脚本。

## 本地使用

对于新的本地 checkout，最简单的启动方式是：

```bash
./scripts/setup-local.sh --start
```

该脚本会创建 `config.local.json`，保持 LLM 关闭，尽量检测可用的
Sage/lattice-estimator 路径，运行一个小型 smoke test，并在
`http://127.0.0.1:8000` 启动服务。

如果只想生成本地配置而不启动服务：

```bash
./scripts/setup-local.sh
```

若未检测到本地 estimator 路径，可将 `malb/lattice-estimator` clone 到
`.external/lattice-estimator`：

```bash
./scripts/setup-local.sh --with-estimator
```

快速筛选模式仍不强制需要 Sage；只有 `useEstimator=true` 时才需要 Sage。也可手动
启动：

```bash
python3 -m app.server
```

打开 `http://127.0.0.1:8000`。如需换端口：

```bash
PORT=8010 python3 -m app.server
```

## 本地配置

推荐使用上面的 setup 脚本。手动配置时，复制示例文件：

```bash
cp config.local.example.json config.local.json
```

`config.local.json` 已被 git 忽略。主要设置包括：

- `estimator.sage_binary`：`sage` 或 Sage 可执行文件的绝对路径；
- `estimator.lattice_estimator_path`：当 Sage 不能直接 import `estimator` 时，
  填写 `malb/lattice-estimator` 的绝对路径；
- `estimator.default_timeout_seconds`：estimator 验证的请求级超时；
- `estimator.remote_url`：可选的 Hugging Face estimator worker URL；
- `estimator.remote_timeout_seconds`：远程 worker 超时，面向 180-300 秒运行；
- `estimator.remote_poll_interval_seconds`：远程任务轮询间隔；
- `llm.enabled`：默认关闭；只有需要 LLM 意图解析时设为 `true`；
- `llm.base_url`、`llm.model`、`llm.api_key_env`、`llm.auth_header` 和
  `llm.auth_prefix`：用户自备 OpenAI-compatible 模型设置；
- `scripts.decrypt_error` 与 `scripts.signature_smoothing`：未来用于具体方案检查
  的本地 hook。

等价环境变量：

```bash
SAGE_BINARY=/path/to/sage \
LATTICE_ESTIMATOR_PATH=/path/to/lattice-estimator \
python3 -m app.server
```

远程 estimator worker：

```bash
EASYLATTICE_ESTIMATOR_REMOTE_URL=https://your-estimator-space.hf.space \
EASYLATTICE_ESTIMATOR_REMOTE_TIMEOUT=240 \
python3 -m app.server
```

可选的 LLM 增强：

```bash
export EASYLATTICE_LLM_ENABLED=true
export EASYLATTICE_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
export EASYLATTICE_LLM_MODEL=your-model
export EASYLATTICE_LLM_API_KEY=your-token
python3 -m app.server
```

如果本地 endpoint 不需要认证，在 `config.local.json` 中设置
`"auth_header": ""`。API 只会在 `/api/config/public` 暴露不含密钥的配置。

## API

主要推荐接口：

```text
POST /api/agent/recommend
```

当 `useLLM=false` 或省略时，只运行确定性核心。当 `useLLM=true` 时，需要本地 LLM
配置和 `intent` 字符串。兼容旧版的 `/api/rlwe/recommend` 路由仍然可用，并使用同一个
agent 层。

较长的 estimator 运行使用异步任务：

```text
POST /api/agent/jobs
GET /api/agent/jobs/{job_id}
```

浏览器在 `useEstimator=true` 时使用这些接口，因此 3-5 分钟的 Sage 或
lattice-estimator 运行不依赖单个长 HTTP 请求。

解密错误率同步接口：

```text
POST /api/decryption-failure/calculate
```

它接受 `type: "ntru" | "lwe"`、维度、系数、分布对象，以及可选的
`precisionBits` / `tailBits`；返回纠错前的 `log2(DFR)`、供 ECC 脚本使用的显式
原始概率字段、支持集摘要、尾界和近似警告。

使用 `"problem": "ntru"` 调用 NTRU 选择器：

```json
{
  "problem": "ntru",
  "targetSecurity": 128,
  "ringFamily": "power2",
  "useEstimator": true
}
```

## 可选在线后端

GitHub Pages 不提供共享的实时后端。若需要自托管动态估计，
[`deploy/huggingface-live`](deploy/huggingface-live) 中的 Docker 模板会在与本地
服务相同的 API 后面运行确定性选择器和可选 Sage/lattice-estimator 验证。Hugging Face
的 Docker Spaces 可能需要付费 PRO 账号。

若只需要更小的 estimator-only worker，
[`deploy/huggingface-estimator`](deploy/huggingface-estimator) 提供：

- `POST /jobs`：异步 estimator 任务；
- `GET /jobs/{job_id}`：任务轮询；
- `POST /estimate`：仅用于同步调试；
- 默认 240 秒超时，最大限制为 300 秒。

该 worker 只接受通过校验的 estimator payload，并转发给
`app/estimator_runner.py`；不会运行任意用户代码或任何 LLM。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 范围

该原型不是生产级参数认证工具。独立 DFR 计算器尚未绑定到具体的加密/签名编码或
纠错码，也不会计算拒绝采样时间、smoothing 参数条件或完整的规约损失核算。

`matzov` 代价选项表示经典 ADPS16 Matzov-style dual-hybrid 估计；`adps16` 选项
报告 ADPS16 CoreSVP/uSVP 估计。启用 Sage 验证时，easyLattice 会调用
`lattice-estimator` 并向下取整安全比特，避免高估下界。

## 计划扩展点

- `agent`：将用户意图转换为约束并解释权衡；LLM 辅助为 opt-in；
- `estimators`：排队和缓存长时间运行的 lattice-estimator 任务；
- `schemes/encryption`：面向具体 PKE/KEM 方案的解密错误脚本；
- `schemes/signature`：hash-and-sign 的 smoothing 与拒绝采样检查；
- `providers`：OpenAI-compatible、本地 Ollama/vLLM 或其他用户自有模型
  endpoint；provider 不得使用维护者拥有的 token。

方案设计参考见 [docs/references.md](docs/references.md)。确定性核心和可选 LLM
分层说明见 [docs/architecture.md](docs/architecture.md)。
