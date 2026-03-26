# vgmstream-cli-build

[![GitHub release](https://img.shields.io/github/v/release/Virace/vgmstream-cli-build?display_name=tag&logo=github)](https://github.com/Virace/vgmstream-cli-build/releases/latest)

这是一个用于编译魔改版 `vgmstream-cli` 的项目。

当前仓库已经从“直接维护 patch 输入”切换为“维护 overlay + Python injector + audit 快照”的工作流：

- `overlay/` 保存我们自己的源事实
- `src/` 保存 `uv` 管理的 Python 注入器
- `audit/cli-overlay.audit.patch` 保存当前注入结果的审计快照

也就是说，`audit` 里的 patch 只用于审计和溯源，不再作为主输入物直接维护。

您可以从上面的徽章或 [这里](https://github.com/Virace/vgmstream-cli-build/releases/latest) 下载最新编译的版本。

## 修改内容

1.  **为输出路径添加通配符**:
    *   `?p`: 代表源文件的完整路径 (包含最后的路径分隔符)。
    *   `?b`: 代表源文件的基础名称 (不含扩展名)。
2.  **支持目录输入**: 允许将文件夹作为输入，程序会自动递归扫描并转码其中所有的 `.wem` 文件。
3.  **增加 `-Y` 选项**: 在转码成功后删除源文件。**这是一个危险操作，请务必谨慎使用！**

## 使用示例

以下是使用新增功能的一个实例：

```bash
.\vgmstream-cli.exe -o "?p?b.wav" "E:\audios\Champions\2·olaf·狂战士\2000·基础皮肤" -Y
```

这个命令的含义：
- 将 `E:\audios\Champions\2·olaf·狂战士\2000·基础皮肤` 目录中的所有音频文件（包括子目录）转换为 WAV 格式
- 输出文件使用 `?p?b.wav` 模式命名，即保持原始文件的路径和文件名，仅将扩展名改为 `.wav`
- `-Y` 参数表示转换完成后删除原始文件

例如，对于输入文件 `E:\audios\Champions\2·olaf·狂战士\2000·基础皮肤\11111111.wem`，输出文件将是 `E:\audios\Champions\2·olaf·狂战士\2000·基础皮肤\11111111.wav`。

## 仓库结构

- `overlay/cli/virace_cli_ext.h`: 我们自己的 C 扩展逻辑源文件
- `src/vgmstream_cli_build/`: Python 注入器实现
- `audit/cli-overlay.audit.patch`: 当前 overlay 注入到 upstream 后导出的审计快照
- `.github/workflows/build.yml`: CI 构建与审计快照导出

## 手动构建

由于上游代码和依赖可能存在不确定性，本项目的构建流程设置为手动触发。

1.  访问本仓库的 [Actions](https://github.com/Virace/vgmstream-cli-build/actions) 页面。
2.  在左侧选择 "Build and Release vgmstream-cli" 工作流。
3.  点击 "Run workflow" 按钮，即可开始构建和发布流程。

## 本地注入

当前仓库使用 `uv` 管理 Python 环境。本地对上游目录执行注入时，可先初始化依赖：

```bash
uv sync
```

然后执行：

```bash
uv run vgmstream-cli-build sync --repo-root "H:\Programming\C++\vgmstream-upstream" --workspace-root .
```

这个命令会：

- 将 `overlay/cli/virace_cli_ext.h` 复制到 upstream 的 `cli/` 目录
- 对 `cli/vgmstream_cli.c`、`cli/vgmstream_cli.h`、`cli/vgmstream_cli_utils.c` 做结构化注入
- 导出 `audit/cli-overlay.audit.patch` 审计快照

## 致谢

*   **灵感来源**: [@DoTheBetter/aria2_build](https://github.com/DoTheBetter/aria2_build)
*   **上游项目**: [@vgmstream/vgmstream](https://github.com/vgmstream/vgmstream)
