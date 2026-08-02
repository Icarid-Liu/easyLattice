# easyLattice

本地优先的格密码参数搜索原型。GitHub Pages 只是静态预览；实时搜索在
本机运行。

## 启动

```bash
git clone https://github.com/Icarid-Liu/easyLattice.git
cd easyLattice
./start.sh
```

`./start.sh` 就是正常入口：创建或补全 `config.local.json`，运行确定性烟测，
启动 `http://127.0.0.1:8000`，并在系统支持时打开浏览器。快速筛选不要求 Sage
或 `lattice-estimator`；需要实测验证时，在网页中填写路径即可。

常用选项：

```bash
./start.sh --with-estimator       # clone 缺失的 Standard/Enhanced estimator
./start.sh --no-open              # 不自动打开浏览器
./start.sh --host 127.0.0.1 --port 8003
```

`--with-estimator` 不是正常启动的必要条件，只用于将两个 estimator clone 到
`.external/`。本地任务和本地单个 Sage 攻击都没有自动时间上限，困难的稀疏分布
可能需要数分钟；远程 worker 仍使用配置的整任务及单攻击超时。

## 搜索逻辑

候选严格按

\[
n\;\longrightarrow\;q\;\longrightarrow\;(X_s,X_e)
\]

枚举：先增大环维度 `n`，再选择满足 NTT 条件的最小素模数 `q`，最后枚举 Secret
和 Error 分布。启用 estimator 后，每个候选保留四组比较：

- MATZOV 经典、量子；
- ADPS16 经典、量子。

第一个满足所选安全模式和规约模型的实测候选立即返回。全部耗尽时返回
`no_feasible_candidate`，只显示最佳未达标参考候选。

支持 RLWE/MLWE/LWE/LWR 变体，以及二次幂、HPS、HRSS 和 NTRU-Prime 风格 NTRU。
RLWE 系列使用 Enhanced estimator；LWE/LWR/NTRU 使用 Standard estimator。

## 分布

Secret 和 Error 分开配置，默认都是 `pure`，也可独立切换为 `combination`。
组合模式自动枚举 CBD 和稀疏三元相加组合；`maxDistributionComponents` 默认 `3`，
允许范围 `1..6`。组合分布使用矩近似：

\[
\operatorname{Var}(X_1+\cdots+X_k)=\sum_i\operatorname{Var}(X_i)，
\]

并在 estimator 输入和结果中标记警告。LWR/RLWR/MLWR 的 Error 是由 `q -> p`
压缩诱导的噪声，因此界面禁用 Error 分布选择。

稀疏三元分布满足

\[
\Pr[X=+1]=\Pr[X=-1]=
\frac{2^{\ell_0}-1}{2^{2\ell_0+\ell_1}}。
\]

结果会显示 `P(+1)`、`P(-1)`、`P(0)`，support 为 `[-1, 0, 1]`。固定 `(n, q)` 后建立两张
独立的分布表：先按标准差二分定位达到安全目标的最小标准差（检测到非单调时回退为精确扫描），
再只在标准差不小于该阈值的行中选择 Secret+Error 采样比特总数最小的分布；采样比特相同时优先
标准差更小者。JSON 同时提供 `min_sampling_bits` 和 `min_stddev` 两个候选。

快速筛选不是方案证明；正确性、拒绝采样、smoothing 和纠错仍需具体方案分析。

## 规约模型

- **MATZOV（考虑多项式及亚指数部分复杂度，更激进）**
- **ADPS16（只考虑指数部分，更保守）**

选中的模型和经典/量子模式决定目标判断，但 JSON 始终保留四组估计结果。

## 配置与测试

网页只会把本地 Sage、Standard 和 Enhanced estimator 路径写入
`config.local.json`。LLM 默认关闭，确定性搜索不需要 LLM。

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
```

配置好 Sage 后可运行 live fixture：

```bash
EASYLATTICE_RUN_SAGE_TESTS=1 \
  python3 -m unittest discover -s tests -p 'test_live_estimator_search.py' -v
```
