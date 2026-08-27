# 应用版本号更新

## 版本规则

本项目采用 `MAJOR.MINOR.PATCH` 格式的语义化版本：

- 向后兼容的缺陷修复增加 `PATCH`，例如 `1.16.0 -> 1.16.1`。
- 向后兼容的新功能增加 `MINOR`，例如 `1.16.0 -> 1.17.0`。
- 不兼容或破坏性变更增加 `MAJOR`，例如 `1.16.0 -> 2.0.0`。
- 用户只说“版本 +1”时，应根据本次改动判断级别；只有无法判断时才询问。

## 版本来源

`version.py` 中的 `APP_VERSION` 是应用发布版本的唯一来源。应用、UI 与 `build.sh` 都从这里读取版本。

更新版本时：

1. 只修改 `version.py` 的 `APP_VERSION`。
2. 不要为了版本号修改 `build.sh`；脚本会自动读取 `version.py`。
3. `Dockerfile` 当前不维护版本默认值，不需要同步修改。
4. README 中的示例版本仅在示例与实际流程不一致时再更新。

## 验证

版本修改后运行：

```bash
rg -n '^APP_VERSION|version.py' version.py app.py ui_routes.py build.sh
python3 -m py_compile version.py app.py ui_routes.py
./build.sh --help
git diff --check
```

发布包含功能改动时，还应运行对应测试；不要仅因版本修改跳过已有失败测试的排查。
