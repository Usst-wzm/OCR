# 电路图 OCR 元器件名称识别

这个项目用于从扫描版电路图、线束图、仪表图 PDF 中识别文字，并提取疑似元器件/电气对象名称。当前推荐流程是：

```text
PDF -> 渲染图片 -> 阿里云 OCR统一识别 -> 本地规则提取候选名称 -> 可选大模型清洗候选 -> 导出结果
```

支持两种使用方式：

- Streamlit 网页界面：上传 PDF、填写配置、查看过程日志、下载结果。
- 命令行：适合批处理或调试。

## 功能

- 上传或指定 PDF 文件。
- 支持按页识别，例如 `1`、`2`、`1,3-5`。
- 支持 `grid`、`tiles`、`page` 三种切图模式。
- 使用阿里云 OCR统一识别接口做文字识别。
- 使用本地规则从 OCR 文本中快速筛选元器件候选。
- 可选接入 Qwen/OpenAI 兼容大模型，只清洗本地候选名称，减少 token 消耗。
- 实时显示处理过程和耗时：渲染、切图、每个 tile OCR、本地筛选、大模型、导出。
- 输出 JSON、CSV、Excel、TXT。

## 安装

需要本机可调用 `pdfinfo` 和 `pdftoppm`。Windows 上可以安装 Poppler，并把 `bin` 目录加入 PATH。

安装 Python 依赖：

```powershell
python -m pip install -e .
```

## Streamlit 网页界面

启动：

```powershell
streamlit run streamlit_app.py
```

网页里可以：

- 上传 PDF。
- 选择页码、DPI、切图模式、OCR 并发数。
- 填写并保存阿里云 OCR 配置。
- 填写并保存 Qwen/OpenAI 兼容大模型配置。
- 查看完整运行日志和耗时明细。
- 下载识别结果。

第一次填写配置后，点击右侧的 **保存本地配置**。配置会保存到：

```text
local_config.json
```

这个文件包含 API Key，已经被 `.gitignore` 忽略，不要上传。

## 阿里云 OCR 配置

先开通阿里云 **OCR统一识别** 服务，然后配置 RAM 用户的 AccessKey。

PowerShell 临时环境变量：

```powershell
$env:ALIBABA_CLOUD_ACCESS_KEY_ID="你的 AccessKey ID"
$env:ALIBABA_CLOUD_ACCESS_KEY_SECRET="你的 AccessKey Secret"
```

项目调用的是：

```text
RecognizeAllText
Type=General
Endpoint: ocr-api.cn-hangzhou.aliyuncs.com
```

如果使用 Streamlit，直接在网页右侧填写并保存即可。

## Qwen 大模型配置

大模型不是必须的。默认流程只用：

```text
阿里云 OCR + 本地规则
```

速度最快，也不消耗大模型 token。

如果需要大模型清洗候选名称，推荐使用 DashScope / Qwen 的 OpenAI 兼容接口：

```powershell
$env:OPENAI_API_KEY="你的 DashScope API Key"
$env:OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:OPENAI_MODEL="qwen-turbo"
```

可选模型：

```text
qwen-turbo    速度优先
qwen-plus     效果更好但可能更慢
qwen-max      更强但更慢、更贵
```

Streamlit 中勾选 **用大模型清洗本地候选名称** 后，程序不会把整页 OCR 全量对象发给模型，而是：

```text
本地规则候选名称 -> 用 | 拼成一串 -> 大模型返回 {"names":"name1,name2"} -> 程序拆分
```

如果大模型超时或返回无效结果，程序会保留本地规则候选结果。

## 推荐使用方式

先用第 1 页测试：

```text
页码: 1
模式: grid
DPI: 240
OCR 并发数: 6
大模型清洗: 关闭
```

如果小字漏识别，再切换到：

```text
模式: tiles
```

`tiles` 精度通常更好，但调用次数更多、费用更高、耗时更长。

## 命令行使用

渲染并切图，不调用 OCR：

```powershell
python main.py --pdf "C:\path\to\file.pdf" --out outputs --pages 1 --render-only
```

用阿里云 OCR 跑第 1 页，推荐先用 `grid`：

```powershell
python main.py --pdf "C:\path\to\file.pdf" --out outputs_aliyun --pages 1 --ocr-provider aliyun --mode grid --ocr-workers 6
```

小字较多时使用 `tiles`：

```powershell
python main.py --pdf "C:\path\to\file.pdf" --out outputs_aliyun_tiles --pages 1 --ocr-provider aliyun --mode tiles --ocr-workers 6 --dpi 240 --tile-size 1800 --overlap 220
```

跑全文：

```powershell
python main.py --pdf "C:\path\to\file.pdf" --out outputs_full --ocr-provider aliyun --mode grid --ocr-workers 6
```

## 输出文件

输出目录中常见文件：

```text
all_ocr_texts.txt          OCR 全量文本，便于人工排查
all_ocr_texts.json
all_ocr_texts.csv
components.json            元器件候选结果
components.csv
components.xlsx
component_names.txt        去重名称
component_names.csv
component_names_clean.txt  更干净的名称清单
component_names_clean.csv
debug/                     渲染页和切图
.cache/                    OCR 和模型缓存
```

## 速度和费用建议

- `grid`：默认一页 6 次 OCR，请求少、速度快、费用低，推荐先用。
- `tiles`：一页可能几十次 OCR，小字更准，但更慢、更贵。
- `page`：一页 1 次 OCR，最省调用次数，但小字可能漏。
- `--ocr-workers 6`：并发请求阿里云 OCR，通常能明显提速。
- 大模型清洗只用于候选名称，仍可能受模型接口速度影响。Qwen 接口慢时可以换 `qwen-turbo` 或关闭大模型。

阿里云 OCR 通常按调用次数计费。重复跑同一个输出目录会优先读 `.cache`，一般不会重复请求已经完成的 tile。换输出目录、删缓存或改变切图参数会重新调用。

## 隐私和上传前检查

不要上传这些文件：

```text
local_config.json
outputs/
outputs_aliyun*/
*.log
```

原因：

- `local_config.json` 包含 AccessKey/API Key。
- `outputs*` 里有 OCR 文本、PDF 渲染图片、缓存结果，可能包含业务资料。
- 日志可能包含路径、错误信息或部分识别文本。

上传前可以检查是否泄露 key：

```powershell
rg "sk-|ALIBABA|ACCESS_KEY|DashScope|dashscope|api_key|secret|OPENAI_API_KEY" .
```

正常可以上传的核心文件：

```text
circuit_ocr/
tests/
main.py
streamlit_app.py
pyproject.toml
uv.lock
README.md
component_terms.txt
.gitignore
```

## 常见问题

### ocrServiceNotOpen

说明阿里云 OCR 服务没有开通，或当前 AccessKey 所属账号没有开通对应服务。去阿里云控制台开通 **OCR统一识别**，并给 RAM 用户授权。

### 大模型返回很慢

OCR 和本地规则通常很快。如果日志显示慢在“大模型清洗候选”，说明 Qwen/OpenAI 兼容接口响应慢。可以：

- 换 `qwen-turbo`。
- 检查 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY` 是否匹配。
- 关闭大模型清洗，只用本地规则。

### 大模型结果为 0

如果大模型超时或返回格式不对，程序会保留本地规则候选。查看过程日志里的大模型错误信息。
