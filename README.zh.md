# easyLattice

**中文** | [English](README.md)

面向格密码参数选择的本地优先、开源原型工具。

公开的 GitHub Pages 页面是静态界面预览，不提供共享后端，也不会调用 LLM、Sage 或
`lattice-estimator`。如需实时计算，请 clone 本仓库并启动本地服务；Sage、estimator
和可选模型配置均保留在用户本机。

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
  模型评估。二次幂、HPS 和 HRSS 的快速/参考筛选只提供经典安全比特，因此这些环族
  需要 Sage 才能得到有限的量子估计。SNTRUP 预设同时包含经典和量子参考比特，所以在
  `useEstimator=false` 时也能对量子请求返回 `target_met`；在启用 estimator 验证前，
  其验证状态仍为 `not_requested`。

Streamlined NTRU Prime 提供以下六组固定预设。安全选择器使用表中的参考安全比特和
NIST 类别，estimator 验证仍遵循下文的验证契约。

| 预设 | n | q | 固定权重 | 经典比特 | 量子比特 | NIST 类别 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `sntrup653` | 653 | 4621 | 288 | 129 | 117 | 1 |
| `sntrup761` | 761 | 4591 | 286 | 153 | 139 | 2 |
| `sntrup857` | 857 | 5167 | 322 | 175 | 159 | 3 |
| `sntrup953` | 953 | 6343 | 396 | 196 | 178 | 4 |
| `sntrup1013` | 1013 | 7177 | 448 | 209 | 190 | 4 |
| `sntrup1277` | 1277 | 7879 | 492 | 270 | 245 | 5 |

对二次幂 NTRU 进行安全评估时，所选环类型会直接映射到 estimator：选择 `matrix`
时使用 `ntru_type="matrix"`，选择 `ring` 时使用 `ntru_type="circulant"`。HPS、
HRSS 和 NTRU Prime 则始终以 `circulant` NTRU 实例传给标准 estimator，即使请求了
`matrix` 也是如此。该安全 estimator 分类不会取代正确性计算所用的方案环：NTRU
Prime 的 DFR 乘积会按三项多项式 `x^n - x - 1` 取模约化。

## Estimator 验证

easyLattice 使用两个 estimator profile：

- LWE 和 LWR 使用标准
  [`malb/lattice-estimator`](https://github.com/malb/lattice-estimator) profile；
- RLWE、MLWE、RLWR 和 MLWR 使用
  [`identitymapping/enhanced_lattice-estimator`](https://github.com/identitymapping/enhanced_lattice-estimator)
  profile。三种攻击都会在该 profile 中运行。在固定的 estimator revision 中，只有
  `bdd_hybrid` 提供显式环结构修正，并会收到 `deg_ring`、
  `structure_leverage=true`，量子模式下还会收到 `Grover=true`。增强版
  `dual_hybrid` 仍会报告 fork 的计算结果，但它不含显式环结构修正，也不会被标记为
  已做该修正；
- NTRU 使用标准 estimator profile 及其中的 NTRU estimator。

每次 LWE 族验证都会评估 `usvp`、`dual_hybrid` 和 `bdd_hybrid`。两个 profile 都会
运行 MATZOV 与 ADPS16 的经典、量子模式，即 `MATZOV()`、
`MATZOV(nn="quantum")`、`ADPS16()` 和 `ADPS16(mode="quantum")`。增强 profile
也会运行 `usvp`，但不会为该攻击额外传入环结构参数。NTRU 验证会在同样四种规约
模型/模式组合下，调用标准 estimator 的完整 NTRU 攻击分派器。

每个 LWE 攻击结果都包含 JSON-safe 的 `structure_correction` 对象，并稳定提供
`requested`、`available`、`applied`、`code` 和 `message` 字段。对结构化增强请求，
`dual_hybrid` 会标记为已请求修正但不可用且未应用，`bdd_hybrid` 会标记为已应用，
`usvp` 则标记为不适用。未修正的 dual 结果会保留供检查，但不参与安全比特最小值。
由于一个已请求攻击缺少必需修正，即使所有攻击都返回有限代价，固定 revision 下的
结构化验证仍为 `partial`，且 `dual_hybrid` 不能单独认证安全目标。稳定代码分别为
`structure_correction_not_applicable`、`structure_correction_unavailable` 和
`structure_correction_applied`。

两个仓库都提供名为 `estimator` 的顶层 Python 包。easyLattice 因此为每个 profile
启动独立 Sage 子进程，设置隔离的 `PYTHONPATH`、禁用 user site，并在估计前验证
实际导入的包路径。不能把两个仓库同时放进一个 Python import path 后仍期望可靠选择
profile。

Estimator 请求和响应必须携带精确路由。允许的组合只有：`standard` 对应 LWE/LWR，
`enhanced` 对应 RLWE/MLWE/RLWR/MLWR，以及 `standard` 对应 NTRU 的
`matrix`/`ring`。缺失、未知或不匹配的 variant 会在攻击运行前以
`invalid_estimator_route` fail closed。远端响应元数据会经过有界递归清洗并转换为严格
JSON-safe 形式；非有限诊断值会变为 `null`，而非有限安全字段仍会导致验证失败。
NTRU 路由还要求 `matrix -> ntru_type=matrix`，所有已支持的 circulant 变体
（`ring`、HPS、HRSS、NTRU Prime）都使用 `ntru_type=circulant`。不可信元数据中的
孤立 Unicode surrogate 会在 UTF-8 序列化前转义。

推荐响应会区分参数选择状态与 estimator 验证状态：

| 状态 | 含义 |
| --- | --- |
| `validated` | 每个 eligible candidate 都成功覆盖，且所有必需攻击、模型和模式结果完整。 |
| `partial` | 至少一个估计成功，但 candidate 覆盖或必需攻击结果不完整。推荐排名只使用具有可用 estimator 结果的 candidate，不会把未验证 candidate 混回排名。 |
| `failed` | 已请求 estimator 验证，但没有 candidate 产生可用估计。推荐结果回退到确定性的快速筛选/参考排名，并由 `validation.status="failed"`、安全来源 `source_code` 以及验证警告/消息明确标识。 |
| `not_requested` | 未请求 estimator 验证；数值来自确定性筛选或参考数据。 |
| `target_unmet` | 这是选择状态而非验证状态：返回的最佳可用 candidate 低于请求的安全目标。 |

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

## 公开预览与本地运行

[GitHub Pages](https://icarid-liu.github.io/easyLattice/) 只用于静态展示界面，提供
安全评估、NTRU DFR 和 LWE DFR 的示例数据，不提供共享后端。

如需实时参数搜索和 DFR 计算，请 clone 本仓库并启动本地服务：

```bash
git clone https://github.com/Icarid-Liu/easyLattice.git
cd easyLattice
./start.sh
```

平台支持时会自动打开浏览器。首次运行表单会将 Sage 和 estimator 路径保存到本地
`config.local.json`；公开预览仍为只读。也可以使用 `python3 -m app.server` 手动
启动。

任何相关搜索或 DFR 输入发生变化时，对应 workspace 的 input revision 会递增，旧结果
会被标记为 stale，并禁用复制 JSON 等操作。使用完全相同且未变化的输入再次提交时，
revision 不会递增，因此替换请求运行期间，旧结果仍可能保持 current 且可以复制。每次
提交仍会获得新的 request token；只有当前 revision 下最新的 active token 可以更新
界面，因此旧响应不能覆盖它。

下表中的原型设置仅作示例。实时输出请使用本地服务，展示数值不构成参数认证。

表格固定使用以下控件：

- 目标安全比特：`128`；
- 安全度量：`Classical`；
- 规约代价模型：`MATZOV`；
- 分布：`Auto`；
- 环族：`x^n + 1`；
- NTT 规模：`n/2 | q - 1`；
- estimator 验证：关闭。

| 公开 UI 选项 | n | q | NTT 条件 | 秘密分布 | 误差分布 | LWR p | 经典比特 | Estimator commit | 状态 |
| --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- | --- |
| NTRU / 矩阵 | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | 相同 | - | 128.0 | 未使用 | 示例 |
| NTRU / 环 | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | 相同 | - | 128.0 | 未使用 | 示例 |
| LWE / LWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 未使用 | 示例 |
| LWE / RLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 未使用 | 示例 |
| LWE / LWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | 未使用 | 示例 |
| LWE / RLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | 未使用 | 示例 |
| LWE / MLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 未使用 | 示例 |
| LWE / MLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | 未使用 | 示例 |
| SIS / SIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 未使用 | 分类占位 |
| SIS / MSIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | 未使用 | 分类占位 |

这些行只使用确定性快速筛选，因此数值不依赖任何 estimator commit。启用可选验证时，
standard profile（LWE/LWR/NTRU）使用 `malb/lattice-estimator` commit
`3e48ef42`；enhanced profile（RLWE/MLWE/RLWR/MLWR）使用
`identitymapping/enhanced_lattice-estimator` commit `876b6617`。

`SIS / SIS` 和 `SIS / MSIS` 目前只出现在 UI 分类中，真正的 SIS/MSIS 选择器尚未
实现。表中这些行复用了当前 LWE/RLWE 快速筛选框架，不应解读为 SIS 难度估计。

## 解密错误率

本地 UI 提供独立的有限分布 DFR 计算器，使用以下纠错前系数模型：

- NTRU：`p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e`；
- LWE：`((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2`。

成功边界为 `|E| <= Delta`；只有 `|E| > Delta` 的概率质量会计入失败率。计算器以
`log2(DFR)` 报告结果。对于 ring-aware NTRU 模型，单系数值是所有系数边缘分布中的
最坏值；对于 LWE 模型，所有输出系数使用相同的单系数模型。向量值是系数失败概率之和
并截断到 1，即 union bound，不假定输出系数相互独立。显式原始概率字段保留在 API 和
复制的 JSON 中，供外部 ECC 计算使用。默认工作精度为 512 比特；离散高斯使用可配置
的 128 比特尾界。

NTRU 乘积支持按 `x^n - 1` 的循环约化、按 `x^n + 1` 的负循环约化，以及按
`x^n - x - 1` 的 NTRU Prime 三项式约化。NTRU Prime 结果会报告系数边缘近似警告；
它的向量聚合仍为 union bound，不对联合独立性作任何声明。

输入支持常见的 `lattice-estimator` 分布族、LWR 向下取整压缩、Kyber 最近整数压缩和
自定义有限 PMF JSON 对象。estimator 的 `NoiseDistribution` 只公开矩而不提供唯一
采样律，因此 DFR 计算时必须为它提供自定义 PMF。固定权重稀疏三元输入使用其单系数
边缘分布，并报告由此产生的相关性近似。

计算器刻意不建模纠错码。LAC、DAWN 等带编码的方案应将纠错前输出交给具体方案的
纠错概率脚本。

## 本地使用

对于新的本地 checkout，最简单的启动方式是：

```bash
./start.sh
```

该脚本会准备 `config.local.json`、运行一个小型 smoke test、在
`http://127.0.0.1:8000` 启动服务，并在平台支持时自动打开浏览器。常用变体为：

```bash
./start.sh --no-open
./start.sh --host 127.0.0.1 --port 8003
./start.sh --with-estimator
```

`--no-open` 表示不自动打开浏览器。`--with-estimator` 保留 setup helper 的可选
行为，将缺少的 Standard 和 Enhanced 仓库 clone 到 `.external/`。

第一次以 live 模式打开时，浏览器会显示 estimator profile 配置表单。Sage 默认值为
`sage`；Standard estimator 路径必填，Enhanced 路径可选。保存时，每个已配置仓库
都会在独立 Sage 子进程中验证，随后写入 `config.local.json`。配置完成后仍可使用
**修改配置（Modify configuration）** 按钮重新打开表单。

如果只想生成本地配置而不启动服务：

```bash
./scripts/setup-local.sh
```

若要检测两个 estimator profile，并将缺少的仓库分别 clone 到
`.external/lattice-estimator` 和 `.external/enhanced-lattice-estimator`：

```bash
./scripts/setup-local.sh --with-estimator
```

创建新的 `config.local.json` 时，脚本会将检测或 clone 得到的两个路径分别写入
`lattice_estimator_path` 和 `enhanced_lattice_estimator_path`。如果配置已存在，请加
`--force` 重新生成并写入这些路径。

快速筛选模式仍不需要 Sage。本地 estimator 使用固定路由：

```text
Standard: LWE, LWR, NTRU
Enhanced: RLWE, MLWE, RLWR, MLWR
```

当 `useEstimator=true` 且未配置 `estimator.remote_url` 时，Sage 和对应 profile 必须
可用。若要在本地支持全部变体，应同时配置两条源码路径。配置远程 worker 后，会绕过
本地 Sage 和 estimator 路径检查。也可手动启动：

```bash
python3 -m app.server
```

打开 `http://127.0.0.1:8000`。如需换端口：

```bash
PORT=8010 python3 -m app.server
```

## 本地配置

推荐在浏览器表单中配置本地 estimator profile。表单只持久化 Sage、Standard 和
Enhanced 三个路径字段；`config.local.json` 中已有的超时、远程 worker、LLM 和脚本
设置都会保留。可通过 **修改配置（Modify configuration）** 重新打开表单。若需手动
配置，复制示例文件：

```bash
cp config.local.example.json config.local.json
```

`config.local.json` 已被 git 忽略。主要设置包括：

```json
{
  "estimator": {
    "sage_binary": "sage",
    "lattice_estimator_path": "/path/to/malb/lattice-estimator",
    "enhanced_lattice_estimator_path": "/path/to/identitymapping/enhanced-lattice-estimator",
    "default_timeout_seconds": 16,
    "per_attack_timeout_seconds": 12,
    "remote_url": null,
    "remote_timeout_seconds": 240,
    "remote_poll_interval_seconds": 2
  }
}
```

Sage 和两个 estimator 源码目录可以位于任意位置，只要 easyLattice 所在的同一运行环境
能够访问。通过本文件或下方环境变量提供路径即可，不要求使用特定父目录。

路径必须使用启动 easyLattice 的运行环境能够识别的形式。例如服务运行在 WSL 内时，
应填写 `/usr/local/bin/sage` 和 `/home/user/lattice-estimator` 这类 Linux 路径，而
不是 `\\wsl.localhost\Ubuntu-22.04\usr\local\bin\sage` 这类 Windows UNC 路径。
Standard 与 Enhanced 都提供名为 `estimator` 的 Python package，因此 easyLattice
不会将两者导入同一进程；每个 profile 都在独立、隔离的 Sage 子进程中预检和执行。

- `estimator.sage_binary`：`sage` 或 Sage 可执行文件的绝对路径；仅本地 estimator
  模式需要；
- `estimator.lattice_estimator_path`：`malb/lattice-estimator` 的绝对路径；
  本地标准 profile 验证需要该路径；
- `estimator.enhanced_lattice_estimator_path`：
  `identitymapping/enhanced_lattice-estimator` 的绝对路径；本地结构化
  RLWE/MLWE/RLWR/MLWR 验证需要该路径；
- `estimator.default_timeout_seconds`：本地 estimator 请求超时；
- `estimator.per_attack_timeout_seconds`：每项 estimator 攻击的超时，受外层本地请求
  超时共同限制；
- `estimator.remote_url`：可选的 estimator worker URL；设置后会绕过本地 Sage 和
  两条本地源码路径；
- `estimator.remote_timeout_seconds`：远程 worker 超时，面向 180-300 秒运行；
- `estimator.remote_poll_interval_seconds`：远程任务轮询间隔；
- `llm.enabled`：默认关闭；只有需要 LLM 意图解析时设为 `true`；
- `llm.base_url`、`llm.model`、`llm.api_key_env`、`llm.auth_header` 和
  `llm.auth_prefix`：用户自备 OpenAI-compatible 模型设置；
- `scripts.decrypt_error` 与 `scripts.signature_smoothing`：未来用于具体方案检查
  的本地 hook。

等价环境变量：

```bash
EASYLATTICE_ESTIMATOR_TIMEOUT=240 \
EASYLATTICE_ESTIMATOR_PER_ATTACK_TIMEOUT=60 \
SAGE_BINARY=/path/to/sage \
LATTICE_ESTIMATOR_PATH=/path/to/lattice-estimator \
ENHANCED_LATTICE_ESTIMATOR_PATH=/path/to/enhanced-lattice-estimator \
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
lattice-estimator 运行不依赖单个长 HTTP 请求。任务 `status` 仍为 `queued`、
`running`、`succeeded` 或 `failed`；独立的 `stage` 字段显示
`candidate_search`、`estimator_running` 或 `finalizing`：

```json
{
  "status": "running",
  "stage": "estimator_running",
  "estimator_profile": "enhanced",
  "estimator_commit": "876b6617"
}
```

创建本地 estimator 任务前，服务会检查该请求所需的准确 profile。若缺少 profile，
服务返回 HTTP 409、错误码 `estimator_profile_not_configured` 和
`required_profile` 字段，不会静默退回快速筛选。已配置远程 worker 时会绕过该本地
预检。

浏览器管理本地 profile 使用以下 API：

```text
GET  /api/config/estimator-profile
POST /api/config/estimator-profile
```

GET 返回可编辑的 Sage 值，以及 Standard 和 Enhanced 各自的状态、路径、八位 commit、
dirty 状态和错误字段。POST 只接受三个 profile 字段，并将它们持久化到
`config.local.json`（或 `EASYLATTICE_CONFIG` 指定的文件）。只有服务绑定 loopback
地址且 JSON 请求为同源时才允许写入；该可写接口不会开放宽松 CORS。

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

运行完整的 Python 测试和独立浏览器模型测试：

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
```

单独运行 profile、进度和启动测试：

```bash
python3 -m unittest discover -s tests -p 'test_local_profile.py' -v
python3 -m unittest discover -s tests -p 'test_job_progress.py' -v
python3 -m unittest discover -s tests -p 'test_start_script.py' -v
```

运行编译与语法检查：

```bash
python3 -m py_compile app/*.py deploy/huggingface-estimator/space_app.py
bash -n start.sh scripts/setup-local.sh
node --check static/app-model.js
node --check static/app.js
node --check static/preview-data.js
```

固定 enhanced-estimator checkout smoke 默认关闭，因为它需要 Git 和网络访问。该测试
会验证固定源码版本，但不会运行 Sage 攻击：

```bash
EASYLATTICE_RUN_PINNED_ESTIMATOR_SMOKE=1 \
python3 -m unittest discover -s tests -p 'test_estimator_runner.py' \
  -k test_pinned_enhanced_estimator_checkout_has_expected_package_origin -v
```

## 范围

该原型不是生产级参数认证工具。独立 DFR 计算器尚未绑定到具体的加密/签名编码或
纠错码，也不会计算拒绝采样时间、smoothing 参数条件或完整的规约损失核算。

`matzov` 和 `adps16` 选项选择的是 reduction-cost model，而不是某个攻击的别名。
对 LWE 族验证，所选模型会分别应用到 `usvp`、`dual_hybrid` 和 `bdd_hybrid`；如前文
所述，estimator 会在经典和量子模式下评估 MATZOV 与 ADPS16。启用 estimator 验证时，
easyLattice 只使用已覆盖所请求结构修正的攻击参与排名，并向下取整所选安全比特，
避免高估下界。

## 计划扩展点

- `agent`：将用户意图转换为约束并解释权衡；LLM 辅助为 opt-in；
- `estimators`：排队和缓存长时间运行的 lattice-estimator 任务；
- `schemes/encryption`：面向具体 PKE/KEM 方案的解密错误脚本；
- `schemes/signature`：hash-and-sign 的 smoothing 与拒绝采样检查；
- `providers`：OpenAI-compatible、本地 Ollama/vLLM 或其他用户自有模型
  endpoint；provider 不得使用维护者拥有的 token。

方案设计参考见 [docs/references.md](docs/references.md)。确定性核心和可选 LLM
分层说明见 [docs/architecture.md](docs/architecture.md)。
