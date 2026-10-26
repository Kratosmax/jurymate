# JuryMate 0.1.4

## 修复：启动时不再弹出控制台黑窗

后端是 PyInstaller 打包的 sidecar，之前用了 `--console` 模式——**每次启动
JuryMate 都会跟着弹出一个黑色命令行窗口**，关掉它后端起就没了。这个窗口
对普通用户毫无意义，也容易让人误以为程序出了问题。

改为 `--windowed` 打包后，窗口消失，功能不受影响。

代价是没有了标准输出，所以后端日志一律写
`%LOCALAPPDATA%\JuryMate\logs\backend.log`（滚动 5×2MB）。
遇到问题时请提供这个文件。

## 修复：`JURYMATE_DEBUG` 环境变量不起作用

`logging.basicConfig` 只在 root logger 尚无 handler 时生效，原代码先配了
文件 handler 再调 `basicConfig(level=DEBUG)`，第二次调用是空操作，
所以想开详细日志时级别并没有变。现在级别在首次配置时一次定好。

## 修复：禁止 insightface / pyiqa 自行联网下载模型

扫描前的模型检查通过后，库自身仍会在权重缺失或损坏时联网抓取：

- `FaceAnalysis(name="buffalo_l")` → `ensure_available()`
- `pyiqa.create_metric("musiq")` → `load_file_from_url()`

这两条路径**只认环境变量代理**，不认应用内「网络设置」的镜像与代理。
用户配好镜像却发现下载慢或失败，且没有任何进度反馈。

现在会在调用库之前确认模型已落盘，缺失时明确报错并引导走应用内下载器
（支持镜像、代理、进度）。

## 升级说明

0.1.2 及以后的版本可应用内直接升级。
**0.1.0 / 0.1.1 必须手动安装一次**（旧版的自动更新会卡在文件占用）。

## 下载哪个

| 文件 | 适用 |
|---|---|
| `JuryMate_0.1.4_x64-setup.exe` | 大多数用户，支持自动更新 |
| `JuryMate_0.1.4_x64.zip` | 免安装便携版 |

不提供 Lite 包：后端是 Python sidecar，依赖约 2GB。

## 已知限制

- 只支持 Windows x64
- MUSIQ 审美评分固定走 CPU
- 闭眼阈值 0.18/0.25 为小样本校准
- `%TEMP%\_MEI*` 会累积（onefile 每次启动解压约 1.1GB，被强杀时不清理）。
  若系统盘异常变满，先退出 JuryMate，再删除 `%TEMP%\_MEI*` 目录。
  该问题尚未根治，已记录在开发待办中。

## 许可

- 本项目代码：MIT
- buffalo_l 权重为 insightface **非商业许可**，安装包不包含任何模型权重
