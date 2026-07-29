# DocGuard Agent 结果契约

Agent 必须向 `DOCGUARD_RESULT_FILE` 原子交付 JSON。顶层字段必须且只能为：

```text
schema_version, task_id, attempt_id, input_sha256, profile_id, profile_version,
prompt_versions, review_type_id, review_type_version, core_contract_version, findings
```

- `schema_version` 固定为 `docguard-agent-result-v1`。
- 所有任务、输入、Profile 与审核类型字段必须逐项匹配 `DOCGUARD_AUDIT_MANIFEST`。
- `core_contract_version` 必须匹配 manifest 中冻结的审核类型定义。
- `findings` 中的每一项必须遵循 `finding-contract.md`，无发现时使用空数组。

结果写入临时文件后，必须运行平台提供的 `validate_findings.py`；预检成功后才能原子重命名为 `findings.json`。
