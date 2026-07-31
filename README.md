﻿# doc-structure-mcp

扫描型 PDF → 结构化 DOCX。

> 主方案见 [docs/技术方案.md](docs/技术方案.md)
> 实验日志见 [docs/模型测试日志.md](docs/模型测试日志.md)

## 快速启动

```bash
scripts\start_server.bat    # 8768
```

## 开发流程（Colab 优先）

1. 本地写测试脚本 → `tests/`
2. 复制到 Google Colab 用 GPU 验证
3. 下载 Colab 输出 zip 到 `tests/testContent/`
4. 运行 `scripts/generate_layout_previews.py` 生成 HTML bbox 预览和 plain DOCX
5. 验证通过后部署到正式服务器

## 核心依赖

- PaddleOCR 3.6.0（PP-OCRv5_mobile）
- python-docx、PyMuPDF、Flask

## 项目结构

```
doc-structure-mcp/
├── src/
│   ├── doc_service.py       # Web 服务 (8768)
│   └── docx_builder.py      # XY-Cut 排版引擎
├── tests/                   # Colab 测试脚本
├── scripts/
│   ├── generate_layout_previews.py # 从 OCR JSON 生成 HTML/DOCX 预览
│   └── start_server.bat     # 启动 8768
├── docs/
│   ├── 技术方案.md           # 主方案
│   ├── 模型测试日志.md       # 实验记录
│   └── 项目设计文档.md       # 归并说明
└── requirements.txt
```



