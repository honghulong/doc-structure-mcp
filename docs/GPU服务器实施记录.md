# GPU 服务器实施记录

## 目标

将 Windows GPU 服务器 `SERVER58` 作为 `doc-structure-mcp` 的远程 OCR 验证与服务运行环境，用于快速同步本地代码、安装 GPU OCR 依赖、运行测试，并在验证稳定后部署 GPU 端 Web OCR 服务。

## 当前状态

- 远程访问：已通过 Windows OpenSSH Server 打通。
- 端口映射：公网 `120.234.136.122:22258` 映射到服务器 SSH。
- SSH 免密：本机已配置 `doc-gpu` alias，可直接执行 `ssh doc-gpu` 登录。
- 服务器目录：`D:\projects\doc-structure-mcp`。
- 代码同步：本机通过 `scripts\sync_to_doc_gpu.cmd` 同步到服务器。
- 同步范围：同步 `src/`、`tests/`、`scripts/`、`config/`、`requirements.txt`、`README.md`。
- 排除范围：暂不上传 `docs/`、`.git/`、`.venv/`、模型缓存和临时输出。

## 服务器环境

服务器基础信息：

- 主机名：`SERVER58`
- Python：`3.10.11`
- pip：`26.2`
- Git：`2.55.0.windows.3`
- GPU：NVIDIA Tesla T4
- 显存：15360 MiB
- Driver Version：`539.64`
- NVIDIA-SMI CUDA Version：`12.2`

Python 虚拟环境：

```cmd
cd /d D:\projects\doc-structure-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
```

GPU Paddle 安装：

```cmd
.venv\Scripts\pip.exe uninstall -y paddlepaddle paddlepaddle-gpu
.venv\Scripts\pip.exe install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
```

GPU Paddle 验证结果：

```text
paddle 3.3.0
cuda True
GPU Compute Capability: 7.5
Driver API Version: 12.2
Runtime API Version: 11.8
PaddlePaddle works well on 1 GPU.
PaddlePaddle is installed successfully.
```

OCR 依赖安装：

```cmd
.venv\Scripts\pip.exe install paddleocr==3.6.0 Flask==3.1.3 Flask-Cors==6.0.5 PyMuPDF python-docx requests -i https://pypi.tuna.tsinghua.edu.cn/simple
```

PaddleOCR 导入验证：

```text
ok 3.3.0 True
```

## 当前问题

`tests\test_ocr_service.py` 是历史脚本，不是合适的当前 GPU 验证入口：

- 文件内写死了本机路径：`D:\hongmengProjects\doc-structure-mcp\...`
- 文件内写死了旧服务地址：`http://127.0.0.1:8767/ocr`
- 当前服务器代码目录为：`D:\projects\doc-structure-mcp`
- 当前服务端口设计仍需重新确认，不应直接沿用旧的 `8767`
- 该文件更像手动脚本，pytest 执行时显示 `no tests ran`

因此下一步不应继续修补旧测试脚本，而应新增一个面向 GPU 服务器的最小 OCR 验证脚本。

## GPU OCR 对标验证

已新增服务器本地 OCR 验证脚本：

```cmd
.venv\Scripts\python.exe scripts\run_gpu_ocr_sample.py --input tests\fixtures\test_page2.pdf --case test_page2_gpu_server_v1
```

服务器输出：

```text
Output directory: D:\projects\doc-structure-mcp\logs\gpu_ocr\test_page2_gpu_server_v1
Result JSON: D:\projects\doc-structure-mcp\logs\gpu_ocr\test_page2_gpu_server_v1\ocr_result.json
CUDA enabled: True
```

评估命令：

```cmd
.venv\Scripts\python.exe scripts\evaluate_ocr_lab_result.py --result logs\gpu_ocr\test_page2_gpu_server_v1\ocr_result.json --out logs\gpu_ocr\test_page2_gpu_server_v1_eval
```

评估结果：

```text
Grade: A
Lines: 49
Rows: 16
Avg Conf: 0.9832
Low Conf: 1
Tall Boxes: 3
Warning: dense_row_maybe_table_or_bad_line_merge
```

与已有 Google/Colab server 模型结果对比：

```text
GPU server lines: 49
Colab server lines: 49
GPU avg confidence: 0.9832
Colab avg confidence: 0.9825
Average confidence diff: 0.0053
Text differences: 2 adjacent lines swapped around the total amount area
```

结论：GPU 服务器 OCR 输出与 Google/Colab server 模型结果高度接近，已足够进入后续文档结构重构验证。当前差异主要是局部行排序差异，应在布局恢复/排序阶段处理，不阻塞 OCR 服务器化。

## 下一步目标

第一阶段：建立 GPU OCR 单样本验证。

验收标准：

- 使用服务器本地 fixture，例如 `tests\fixtures\test_scanned_doc.pdf`。
- 直接调用 PaddleOCR 或项目 OCR 封装，不依赖旧的 `8767` 服务。
- 输出结构化 JSON 到服务器本地日志目录。
- 运行时确认 `paddle.is_compiled_with_cuda() == True`。
- 能观察到 GPU 被使用，至少通过 `nvidia-smi` 看到 Python 进程占用显存。

第二阶段：建立 GPU 端 Web OCR 服务。

验收标准：

- 在服务器启动 Flask OCR 服务。
- 默认服务端口为 `8769`，可通过 `GPU_OCR_PORT` 环境变量覆盖。
- 提供 `/health` 和 `/ocr` 接口。
- 使用 GPU PaddleOCR 懒加载模型。
- 用服务器本地 PDF 调通 OCR 请求。
- 后续再与本机 CPU/本地 OCR 服务做结果对比。

## GPU Web OCR 服务边界

新增服务文件：

```text
src/gpu_ocr_service.py
```

新增启动脚本：

```cmd
scripts\start_gpu_ocr_service.bat
```

服务定位：通用 PaddleOCR GPU OCR 服务。

只负责：

- `GET /health`
- `POST /ocr`
- 返回通用 OCR JSON，包括页面、文本行、置信度、bbox、页面纯文本和耗时。

明确不负责：

- DOCX 重建。
- 发票字段抽取。
- 发票验真。
- 表格业务语义解析。
- admin3 入库。
- `ocr-invoice-mcp` 的发票个性化处理。

默认启动：

```cmd
cd /d D:\projects\doc-structure-mcp
scripts\start_gpu_ocr_service.bat
```

后台启动：

```cmd
cd /d D:\projects\doc-structure-mcp
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_gpu_ocr_service_hidden.ps1
```

后台日志：

```text
logs\gpu_ocr_service.out.log
logs\gpu_ocr_service.err.log
```

健康检查：

```cmd
curl http://127.0.0.1:8769/health
```

OCR 调用示例：

```cmd
curl -F "file=@tests\fixtures\test_page2.pdf" http://127.0.0.1:8769/ocr
```

调用端职责：

- admin3 或其他系统负责消费 `/ocr` 返回的通用 JSON。
- 表单结构化、字段抽取、文档重建、业务入库均由调用端完成。
- GPU OCR 服务只保证 OCR JSON 质量接近 Google/Colab server 模型输出。

## GPU Web OCR 服务验证

`/ocr` 已使用 `tests\fixtures\test_page2.pdf` 在服务器本机验证通过。

验证请求：

```cmd
curl -F "file=@D:\projects\doc-structure-mcp\tests\fixtures\test_page2.pdf" http://127.0.0.1:8769/ocr -o D:\projects\doc-structure-mcp\logs\gpu_ocr_service_test_page2.json
```

服务日志显示：

```text
POST /ocr HTTP/1.1 200
```

返回 JSON 包含：

```text
engine: paddleocr
model.det: PP-OCRv5_server_det
model.rec: PP-OCRv5_server_rec
input.dpi: 220
pages[0].duration_ms: 4500
pages[0].lines: 49
runtime.cuda_enabled: true
```

耗时判断：

- `test_page2.pdf` 单页 OCR 页面处理约 `4.5s`。
- 单样本脚本此前同模型同 DPI 结果约 `4.7s` 页面处理、`5.3s` 总耗时。
- 因此当前服务耗时正常，和直接脚本运行基本一致。
- 首次请求可能包含模型懒加载，实际耗时会明显更长；模型加载完成后重复请求才代表稳定服务耗时。

## 公网访问验证

服务器端口状态：

- GPU OCR 服务监听内网端口：`8769`
- Windows 防火墙已放行入站 TCP：`8769`
- 管理员已做公网端口映射，外部通过公网端口访问 GPU OCR 服务。

防火墙放行命令：

```cmd
netsh advfirewall firewall add rule name="GPU OCR Service 8769" dir=in action=allow protocol=TCP localport=8769
```

公网 `/ocr` 已使用本机样本 `test_page2.pdf` 验证通过，结果保存到：

```text
tests\testContent\public_gpu_ocr_test_page2.json
```

公网返回结果摘要：

```text
status: ok
engine: paddleocr
model.det: PP-OCRv5_server_det
model.rec: PP-OCRv5_server_rec
runtime.cuda_enabled: true
runtime.paddle_version: 3.3.0
input.filename: test_page2.pdf
input.dpi: 220
pages: 1
lines: 49
avg_confidence: 0.9832
timing.total_ms: 2612
page.duration_ms: 2288
```

与服务器本地 OCR 输出对比：

```text
公网 OCR 行数: 49
服务器本地 OCR 行数: 49
文本序列: 完全一致
```

结论：公网访问链路已打通，公网 `/ocr` 返回 JSON 正常，OCR 质量与服务器本地验证结果一致。

安全提醒：

- 当前 `/ocr` 没有认证，公网开放存在资源滥用和文件上传风险。
- 测试阶段建议限制来源 IP 或临时开放。
- 后续若长期供 admin3 调用，应增加 API Key 或改为内网/VPN 调用。

## 本机与 GPU 服务器的关系

当前阶段，GPU 服务器先作为独立验证环境，不强求和本机 OCR 服务实时对比。

原因：

- 本机与服务器路径、端口、依赖版本不同。
- GPU 环境首要目标是确认模型和 OCR 流程可稳定运行。
- 实时对比会引入额外同步、样本、端口和服务一致性问题，容易干扰 GPU 验证主线。

后续在 GPU OCR 服务稳定后，再补充对比流程：

- 固定同一批 PDF/PNG 样本。
- 分别调用本机服务和 GPU 服务。
- 输出 OCR JSON、耗时、置信度、文本差异。
- 再决定是否将 GPU 服务作为正式 OCR 后端。
