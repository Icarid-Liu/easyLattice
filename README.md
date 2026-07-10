# easyLattice

English / 中文

Local-first, open-source prototype for lattice-crypto parameter selection.

面向格密码参数选择的本地优先、开源原型工具。

The public GitHub Pages site is a static example. It does not run a backend,
call an LLM, call Sage, or call `lattice-estimator`. Dynamic estimation is
available only when you run the local service or deploy your own backend.

公开的 GitHub Pages 页面只是静态示例。它不会运行后端、调用 LLM、调用
Sage，也不会调用 `lattice-estimator`。动态估计只在你运行本地服务或部署自己
的后端时可用。

## Overview

easyLattice is layered so the default path does not require any LLM token:

1. deterministic core: fixed RLWE search policy and local security screening;
2. estimator adapter: optional user-provided Sage/lattice-estimator validation;
3. agent layer: deterministic by default, with an optional LLM intent parser;
4. provider layer: user-owned OpenAI-compatible endpoint and authentication.

easyLattice 采用分层设计，因此默认路径不需要任何 LLM token：

1. 确定性核心：固定的 RLWE 搜索策略和本地安全筛选；
2. estimator 适配层：可选的用户自备 Sage/lattice-estimator 验证；
3. agent 层：默认确定性运行，可选 LLM 意图解析；
4. provider 层：用户自有的 OpenAI-compatible endpoint 和认证信息。

This first version focuses on a basic RLWE instance selector:

- power-of-two cyclotomic ring `Z_q[x] / (x^n + 1)`;
- ternary cyclotomic ring `Z_q[x] / (x^n - x^(n/2) + 1)` for even `n` whose
  prime factors are only `2` and `3`;
- NTT-friendly prime modulus with `n | q - 1`; full splitting
  `2n | q - 1` is preferred, but leaving one NTT layer unresolved is treated as
  nearly as good;
- centered binomial and iid sparse ternary secret distributions;
- independently searched centered binomial or iid sparse ternary error
  distributions for LWE/RLWE/MLWE, with fixed-weight estimator approximation
  for sparse ternary;
- compression-noise error distributions for LWR/RLWR/MLWR, generated from the
  selected `q -> p` compression modulus;
- fast local screening plus optional user-provided Sage/lattice-estimator validation;
- a small web UI for interactive parameter search.

当前版本主要提供基础 RLWE 实例选择器：

- 二次幂分圆环 `Z_q[x] / (x^n + 1)`；
- 三元分圆环 `Z_q[x] / (x^n - x^(n/2) + 1)`，其中 `n` 为偶数且素因子只含
  `2` 和 `3`；
- 适合 NTT 的素数模数，并满足 `n | q - 1`；优先完全分裂的 `2n | q - 1`，
  但剩余一层 NTT 未分解也会被视为接近可接受；
- secret 分布支持中心二项分布和 iid 稀疏三元分布；
- LWE/RLWE/MLWE 的 error 分布独立搜索中心二项分布或 iid 稀疏三元分布，
  稀疏三元分布会用固定权重近似传给 estimator；
- LWR/RLWR/MLWR 的 error 分布使用由所选 `q -> p` 压缩模数生成的压缩噪声；
- 本地快速筛选，以及可选的用户自备 Sage/lattice-estimator 验证；
- 一个用于交互式参数搜索的小型 Web UI。

There is also an initial NTRU selector behind the same agent API. It currently
supports:

- power-of-two cyclotomic NTRU over `Z_q[x] / (x^n + 1)`, matching the ring
  family used by designs such as NEV/BAT/DAWN-style NTRU variants;
- the same relaxed NTT default used by the RLWE prototype for power-of-two
  rings, namely `n/2 | q - 1`;
- two-stage distribution selection: first calibrate the minimum standard
  deviation with a discrete-Gaussian proxy, then choose the closest
  fast-sampling distribution whose standard deviation is above that lower
  bound. Fast distributions may be single blocks or short sums of sparse
  ternary, symmetric uniform, and centered-binomial blocks; summed
  distributions are estimator moment approximations and are capped by the
  Gaussian proxy calibration to avoid overstating security;
- HPS-like and HRSS-like comparison candidates;
- local `lattice-estimator` NTRU rough validation when `useEstimator=true`.

同一个 agent API 后面也有一个初始版 NTRU 选择器，目前支持：

- `Z_q[x] / (x^n + 1)` 上的二次幂分圆 NTRU，对应 NEV/BAT/DAWN 风格 NTRU
  变体常用的环族；
- 与 RLWE 原型在二次幂环上相同的宽松 NTT 默认条件，即 `n/2 | q - 1`；
- 两阶段分布选择：先用离散高斯代理校准最小标准差，再选择标准差不低于该
  下界且最接近的快速采样分布。快速分布可以是单个块，也可以是稀疏三元、
  对称均匀和中心二项块的短和；求和分布会作为 estimator 的矩近似，并由
  高斯代理校准结果截断，避免高估安全性；
- HPS-like 和 HRSS-like 对比候选；
- 当 `useEstimator=true` 时进行本地 `lattice-estimator` NTRU rough 验证。

## Search Model

The selector treats the user's requested security as a lower bound. It first
chooses the polynomial/ring family, then degree `n`, then the smallest modulus
satisfying the chosen NTT scale, and only then chooses the secret/error
distribution for that modulus. Within a fixed modulus it still avoids
unnecessary security margin.

选择器把用户请求的安全比特视为下界。它先选择多项式/环族，再选择维度 `n`，
然后选择满足所选 NTT 规模的最小模数，最后才为该模数选择 secret/error 分布。
在固定模数内，它仍会避免不必要的安全余量。

The JSON output separates `secret` and `error` distribution fields. For
LWE/RLWE/MLWE the current prototype searches `Xs` and `Xe` independently. For
LWR/RLWR/MLWR, the secret selector still controls `Xs`, while the error control
is a compression modulus `p`. The error distribution is the centered
compression-noise law induced by compressing `vi in {0, ..., q-1}` from `q` to
`p` and lifting back to `q`.

JSON 输出会分开给出 `secret` 和 `error` 分布字段。对 LWE/RLWE/MLWE，当前原型
会独立搜索 `Xs` 和 `Xe`。对 LWR/RLWR/MLWR，secret 选择器仍控制 `Xs`，而 error
控件是压缩模数 `p`。error 分布是把 `vi in {0, ..., q-1}` 从 `q` 压缩到 `p`、
再 lift 回 `q` 所诱导的中心化压缩噪声分布。

The basic monotonicity heuristic remembered by the selector is: smaller `q`
usually increases LWE/RLWE hardness, larger dimension increases hardness, and
larger error standard deviation increases hardness. Correctness and scheme
encoding may push in the opposite direction, so those checks belong in
scheme-specific modules.

选择器使用的基本单调性启发式是：更小的 `q` 通常提高 LWE/RLWE 难度，更大的
维度提高难度，更大的 error 标准差也提高难度。正确性和方案编码可能会产生
相反约束，因此这些检查应放到具体方案模块中。

For sparse ternary candidates, easyLattice includes distributions with
`Pr[+1] = Pr[-1] = (2^l0 - 1) / 2^(2*l0 + l1)` and all remaining probability on
`0`. These are cheap to sample with bit operations. Since `lattice-estimator`
models sparse ternary vectors by fixed Hamming weight, easyLattice passes the
expected `+1` and `-1` counts as a fixed-weight approximation and reports that
approximation in the JSON output.

对稀疏三元候选，easyLattice 包含满足
`Pr[+1] = Pr[-1] = (2^l0 - 1) / 2^(2*l0 + l1)` 且剩余概率都在 `0` 上的分布。
这些分布可以用 bit 操作低成本采样。由于 `lattice-estimator` 用固定汉明重量
建模稀疏三元向量，easyLattice 会把期望的 `+1` 和 `-1` 个数作为固定权重近似
传入，并在 JSON 输出中报告该近似。

easyLattice is designed as a local tool, not a hosted service. Users bring their
own estimator installation, optional model endpoint/API key, and their own
scheme-specific scripts for error correction, rejection sampling, or smoothing
parameters.

easyLattice 被设计为本地工具，而不是托管服务。用户自备 estimator 安装、可选
模型 endpoint/API key，以及用于具体方案的纠错、拒绝采样或 smoothing 参数脚本。

No API key is required for the default RLWE workflow. The LLM layer is disabled
unless `llm.enabled=true` is set locally. When enabled, the model only converts
free-form user intent into deterministic search constraints; final parameters
still come from the fixed local search logic and optional estimator validation.

默认 RLWE 工作流不需要 API key。除非本地设置 `llm.enabled=true`，否则 LLM 层
默认关闭。启用后，模型只把自由文本意图转换为确定性搜索约束；最终参数仍由
固定的本地搜索逻辑和可选 estimator 验证产生。

## Public Static Example

The hosted GitHub Pages version is intentionally static. It demonstrates the UI
and fixed example outputs for this prototype, but all values should be treated
as examples rather than live parameter certification.

托管的 GitHub Pages 版本是有意做成静态页面的。它展示 UI 和固定示例输出，
但所有数值都应视为示例，而不是实时参数认证。

The table below fixes the controls to:

下表固定使用以下控件设置：

- target security / 目标安全比特: `128`;
- security metric / 安全度量: `Classical`;
- reduction cost model / 规约代价模型: `MATZOV`;
- distribution / 分布: `Auto`;
- ring family / 环族: `x^n + 1`;
- NTT scale / NTT 规模: `n/2 | q - 1`;
- estimator validation / estimator 验证: off.

| Public UI option / 公开 UI 选项 | n | q | NTT condition / NTT 条件 | Secret distribution / Secret 分布 | Error distribution / Error 分布 | LWR p | Classical bits / 经典比特 | Status / 状态 |
| --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- |
| NTRU / matrix | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | same / 相同 | - | 128.0 | example / 示例 |
| NTRU / ring | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | same / 相同 | - | 128.0 | example / 示例 |
| LWE / LWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | example / 示例 |
| LWE / RLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | example / 示例 |
| LWE / LWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | example / 示例 |
| LWE / RLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | example / 示例 |
| LWE / MLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | example / 示例 |
| LWE / MLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | example / 示例 |
| SIS / SIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | taxonomy placeholder / 分类占位 |
| SIS / MSIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | taxonomy placeholder / 分类占位 |

`SIS / SIS` and `SIS / MSIS` are shown in the current UI taxonomy, but a real
SIS/MSIS selector is not implemented yet. Their rows reuse the current
LWE/RLWE fast-screen scaffold and should not be read as SIS hardness estimates.

`SIS / SIS` 和 `SIS / MSIS` 目前只出现在 UI 分类中，真正的 SIS/MSIS 选择器尚未
实现。表中这些行复用了当前 LWE/RLWE 快速筛选框架，不应解读为 SIS 难度估计。

## Decryption Failure Rate

The local UI also provides a standalone finite-distribution DFR calculator. It
implements the following pre-error-correction coefficient models:

- NTRU: `p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e`;
- LWE: `((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2`.

The success boundary is `|E| <= Delta`. DFR is reported as `log2(DFR)` for the
single coefficient and vector union bound. Explicit raw probability fields
remain in the API and copied JSON for external ECC calculations. The default
working precision is 512 bits and discrete Gaussians use a configurable 128-bit
tail bound.

Inputs support the common `lattice-estimator` distribution families, LWR floor
compression, Kyber nearest-integer compression, and a custom finite PMF JSON
object. Estimator `NoiseDistribution` instances expose moments rather than a
sampling law, so they must be supplied as a custom PMF for DFR calculation.
Fixed-weight sparse ternary inputs use their coefficient marginal and report
the resulting correlation approximation.

The calculator deliberately does not model error correction. LAC, DAWN, and
other coded schemes should pass its pre-correction outputs to a
scheme-specific correction-probability script.

本地 UI 还提供独立的有限分布 DFR 计算器，计算以下纠错前系数模型：

- NTRU：`p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e`；
- LWE：`((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2`。

成功边界为 `|E| <= Delta`。计算器会以 `log2(DFR)` 给出单系数和向量 union bound
DFR；原始概率通过 API 和复制 JSON 中的显式字段保留，供外部 ECC 计算使用。默认
工作精度为 512 比特；离散高斯使用可配置的 128 比特尾界。

输入支持常见的 `lattice-estimator` 分布族、LWR 向下取整压缩、Kyber 最近整数
压缩和自定义有限 PMF JSON 对象。estimator 的 `NoiseDistribution` 只公开矩而不
给出唯一的采样律，DFR 计算时必须提供自定义 PMF。固定权重稀疏三元分布会使用其
单系数边缘分布，并报告由此产生的相关性近似。

该计算器刻意不建模纠错码。LAC、DAWN 等带编码的方案应把纠错前输出交给具体方案
的纠错概率脚本。

## Run

For a fresh local checkout, the simplest path is:

对新的本地 checkout，最简单的启动方式是：

```bash
./scripts/setup-local.sh --start
```

The setup script creates `config.local.json`, keeps LLM disabled, detects
optional Sage/lattice-estimator paths when available, runs a small smoke test,
and then starts the web service at:

该脚本会创建 `config.local.json`，保持 LLM 关闭，尽量检测可用的
Sage/lattice-estimator 路径，运行一个小型 smoke test，然后在以下地址启动 Web
服务：

```text
http://127.0.0.1:8000
```

If you only want to generate local config without starting the server:

如果只想生成本地配置而不启动服务：

```bash
./scripts/setup-local.sh
```

Optional estimator setup:

可选的 estimator 设置：

```bash
./scripts/setup-local.sh --with-estimator
```

This clones `malb/lattice-estimator` into `.external/lattice-estimator` if no
local estimator path is detected. Sage is still optional for fast-screen mode
and required only when `useEstimator=true`.

如果未检测到本地 estimator 路径，这会把 `malb/lattice-estimator` clone 到
`.external/lattice-estimator`。快速筛选模式仍不强制需要 Sage；只有
`useEstimator=true` 时才需要 Sage。

Manual start still works:

也可以手动启动：

```bash
python3 -m app.server
```

Then open:

然后打开：

```text
http://127.0.0.1:8000
```

Use another port if needed:

如需换端口：

```bash
PORT=8010 python3 -m app.server
```

## Local Configuration

The setup script above is preferred. For manual configuration, copy the example
file and edit local paths:

推荐使用上面的 setup 脚本。手动配置时，复制示例文件并编辑本地路径：

```bash
cp config.local.example.json config.local.json
```

`config.local.json` is ignored by git. It can contain:

`config.local.json` 已被 git 忽略，可以包含：

- `estimator.sage_binary`: `sage` or an absolute path to Sage.
  `sage` 或 Sage 的绝对路径。
- `estimator.lattice_estimator_path`: absolute path to `malb/lattice-estimator`
  if Sage cannot already import `estimator`.
  如果 Sage 不能直接 import `estimator`，这里填 `malb/lattice-estimator` 的绝对路径。
- `estimator.default_timeout_seconds`: request-level timeout for optional
  estimator validation.
  可选 estimator 验证的请求级超时。
- `estimator.remote_url`: optional Hugging Face estimator worker URL. When set,
  `useEstimator=true` calls this remote worker instead of local Sage.
  可选 Hugging Face estimator worker URL。设置后，`useEstimator=true` 会调用远程
  worker，而不是本地 Sage。
- `estimator.remote_timeout_seconds`: remote worker timeout, intended for
  180-300 second live estimator runs.
  远程 worker 超时，面向 180-300 秒的在线 estimator 运行。
- `estimator.remote_poll_interval_seconds`: polling interval for remote jobs.
  远程任务轮询间隔。
- `llm.enabled`: disabled by default. Set to `true` only when you want LLM
  intent parsing.
  默认关闭。只有需要 LLM 意图解析时才设为 `true`。
- `llm.base_url`, `llm.model`, `llm.api_key_env`, `llm.auth_header`,
  `llm.auth_prefix`: bring-your-own OpenAI-compatible model settings.
  用户自备 OpenAI-compatible 模型设置。
- `scripts.decrypt_error`, `scripts.signature_smoothing`: future local script
  hooks for scheme-specific checks.
  未来用于具体方案检查的本地脚本 hook。

Equivalent environment variables:

等价环境变量：

```bash
SAGE_BINARY=/path/to/sage \
LATTICE_ESTIMATOR_PATH=/path/to/lattice-estimator \
python3 -m app.server
```

Remote estimator worker:

远程 estimator worker：

```bash
EASYLATTICE_ESTIMATOR_REMOTE_URL=https://your-estimator-space.hf.space \
EASYLATTICE_ESTIMATOR_REMOTE_TIMEOUT=240 \
python3 -m app.server
```

Optional LLM enhancement:

可选 LLM 增强：

```bash
export EASYLATTICE_LLM_ENABLED=true
export EASYLATTICE_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
export EASYLATTICE_LLM_MODEL=your-model
export EASYLATTICE_LLM_API_KEY=your-token
python3 -m app.server
```

For local endpoints that do not require authentication, set
`"auth_header": ""` in `config.local.json`.

如果本地 endpoint 不需要认证，在 `config.local.json` 中设置 `"auth_header": ""`。

The API exposes only non-secret public config at `/api/config/public`.

API 只会在 `/api/config/public` 暴露不含密钥的公开配置。

## API

The main recommendation endpoint is:

主要推荐接口是：

```text
POST /api/agent/recommend
```

With `useLLM=false` or omitted, it runs only the deterministic core. With
`useLLM=true`, it requires local LLM configuration and an `intent` string.
The legacy-compatible `/api/rlwe/recommend` route is still available and uses
the same agent layer.

当 `useLLM=false` 或省略时，只运行确定性核心。当 `useLLM=true` 时，需要本地
LLM 配置和 `intent` 字符串。兼容旧版的 `/api/rlwe/recommend` 路由仍然可用，
并使用同一个 agent 层。

For long estimator runs, the live API also exposes async recommendation jobs:

对较长的 estimator 运行，在线 API 也提供异步推荐任务：

```text
POST /api/agent/jobs
GET /api/agent/jobs/{job_id}
```

The browser UI uses these job endpoints when `useEstimator=true`, so 3-5 minute
Sage/lattice-estimator runs do not depend on a single long HTTP request.

浏览器 UI 在 `useEstimator=true` 时会使用这些任务接口，因此 3-5 分钟的
Sage/lattice-estimator 运行不依赖单个长 HTTP 请求。

The synchronous decryption-failure endpoint is:

解密错误率同步接口是：

```text
POST /api/decryption-failure/calculate
```

It accepts `type: "ntru" | "lwe"`, dimensions, coefficients, distribution
objects, and optional `precisionBits` / `tailBits`. It returns pre-correction
`log2(DFR)` values, explicit raw-probability fields for ECC scripts, support
summaries, tail bounds, and approximation warnings.

它接受 `type: "ntru" | "lwe"`、维度、系数、分布对象，以及可选的
`precisionBits` / `tailBits`，并返回纠错前的 `log2(DFR)`、供 ECC 脚本使用的
显式原始概率字段、支持集摘要、尾界和近似警告。

Use `"problem": "ntru"` to call the NTRU selector:

使用 `"problem": "ntru"` 调用 NTRU 选择器：

```json
{
  "problem": "ntru",
  "targetSecurity": 128,
  "ringFamily": "power2",
  "useEstimator": true
}
```

## Optional Live Backend

The public GitHub Pages site does not use a live backend. If you want to
self-host dynamic estimation later, the Docker template in
[`deploy/huggingface-live`](deploy/huggingface-live) runs the deterministic
selector and optional Sage/lattice-estimator validation behind the same API as
the local server. Hugging Face may require a paid PRO account for Docker Spaces.

公开的 GitHub Pages 站点不使用实时后端。如果之后想自托管动态估计，
[`deploy/huggingface-live`](deploy/huggingface-live) 中的 Docker 模板会在与
本地服务相同的 API 后面运行确定性选择器和可选 Sage/lattice-estimator 验证。
Hugging Face 的 Docker Spaces 可能需要付费 PRO 账号。

For a smaller estimator-only worker, the template in
[`deploy/huggingface-estimator`](deploy/huggingface-estimator) exposes:

如果只需要更小的 estimator-only worker，
[`deploy/huggingface-estimator`](deploy/huggingface-estimator) 模板提供：

- `POST /jobs` for async estimator jobs / 异步 estimator 任务；
- `GET /jobs/{job_id}` for polling / 轮询；
- `POST /estimate` for synchronous debugging only / 仅用于同步调试；
- a default 240 second timeout, clamped to a 300 second maximum /
  默认 240 秒超时，最大限制为 300 秒。

The estimator-only worker accepts only validated estimator payloads and forwards
them to `app/estimator_runner.py`; it does not run arbitrary user code or any
LLM.

estimator-only worker 只接受通过校验的 estimator payload，并转发到
`app/estimator_runner.py`；它不会运行任意用户代码，也不会运行任何 LLM。

## Tests

```bash
python3 -m unittest discover -s tests
```

## Scope

This prototype is not a production parameter certification tool. Its standalone
DFR calculator is not bound to a concrete encryption/signature encoding or an
error-correction code, and it does not compute rejection-sampling times,
smoothing-parameter conditions, or complete reduction-loss accounting.

该原型不是生产级参数认证工具。独立 DFR 计算器尚未绑定到具体的加密/签名编码或
纠错码，也不会计算拒绝采样时间、smoothing 参数条件或完整的规约损失。

The `matzov` red-cost option means the classical ADPS16 Matzov-style
dual-hybrid estimate. The `adps16` option reports the ADPS16 CoreSVP/uSVP
estimate. With Sage validation enabled, easyLattice calls `lattice-estimator` and
rounds bit counts downward to avoid overstating a lower bound.

`matzov` 规约代价选项表示经典 ADPS16 Matzov-style dual-hybrid 估计。
`adps16` 选项报告 ADPS16 CoreSVP/uSVP 估计。启用 Sage 验证时，easyLattice 会
调用 `lattice-estimator`，并向下取整比特数，避免高估下界。

## Planned Extension Points

- `agent`: convert user intent into constraints and explain tradeoffs. The
  default implementation is deterministic; LLM assistance is opt-in.
  将用户意图转换为约束并解释权衡。默认实现是确定性的，LLM 辅助为 opt-in。
- `estimators`: queue/cache long-running lattice-estimator jobs.
  排队/缓存长时间运行的 lattice-estimator 任务。
- `schemes/encryption`: decryption-error scripts for concrete PKE/KEM schemes.
  面向具体 PKE/KEM 方案的解密错误脚本。
- `schemes/signature`: hash-and-sign smoothing and rejection checks.
  hash-and-sign 的 smoothing 与拒绝采样检查。
- `providers`: OpenAI-compatible, local Ollama/vLLM, or other user-owned model
  endpoints. Providers must never use maintainer-owned tokens.
  OpenAI-compatible、本地 Ollama/vLLM 或其他用户自有模型 endpoint。Provider
  绝不能使用维护者拥有的 token。

See [docs/references.md](docs/references.md) for scheme-design references used
to guide future extension work. See [docs/architecture.md](docs/architecture.md)
for the deterministic-core and optional-LLM layering.

方案设计参考见 [docs/references.md](docs/references.md)。确定性核心和可选 LLM
分层说明见 [docs/architecture.md](docs/architecture.md)。
