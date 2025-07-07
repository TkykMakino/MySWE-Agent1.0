# FEA-Bench フィールド追加更新

## 概要

`run_fea_bench.py`を修正して、FEA-BenchのJSONLファイルに含まれる全てのフィールドを`extra_fields`に格納するように改善しました。

## 修正前の状況

修正前は以下のフィールドのみが格納されていました：

✅ **既存格納フィールド**:
- `instance_id` → `ps.id`
- `repo` → リポジトリパス
- `base_commit` → `ps.base_commit`
- `problem_info.pr_title` + `problem_info.pr_body` → `problem_statement`
- `patch` → `extra_fields["patch"]`
- `test_patch` → `extra_fields["test_patch"]`
- `pull_number` → `extra_fields["pull_number"]`

## 修正後の追加フィールド

以下の11個のフィールドを新たに`extra_fields`に追加しました：

🆕 **新規追加フィールド**:
1. `url` - GitHub上のPRのURL
2. `issue_numbers` - 関連issue番号のリスト
3. `first_commit_time` - 最初のコミット時刻
4. `created_at` - データ生成日時
5. `readmes` - READMEファイルの内容
6. `files` - 変更前の全ファイル内容
7. `non_py_patch` - Python以外のファイルへの変更
8. `new_components` - 新規追加されたコード部品情報
9. `FAIL_TO_PASS` - 失敗→成功に転じたテストリスト
10. `PASS_TO_PASS` - 継続して成功しているテストリスト
11. `environment_setup_commit` - テスト環境構築用コミットID

## 修正内容

### 1. `FEABenchInstances.get_instance_configs()`メソッドの改善

```python
# 全ての追加フィールドをextra_fieldsに格納
extra_fields = {
    "patch": data.get("patch"),
    "test_patch": data.get("test_patch"),
    "pull_number": data.get("pull_number"),
    "url": data.get("url"),
    "issue_numbers": data.get("issue_numbers", []),
    "first_commit_time": data.get("first_commit_time"),
    "created_at": data.get("created_at"),
    "readmes": data.get("readmes"),
    "files": data.get("files"),
    "non_py_patch": data.get("non_py_patch"),
    "new_components": data.get("new_components"),
    "FAIL_TO_PASS": data.get("FAIL_TO_PASS", []),
    "PASS_TO_PASS": data.get("PASS_TO_PASS", []),
    "environment_setup_commit": data.get("environment_setup_commit"),
}
```

### 2. 実装方式の改善

- `SimpleBatchInstance`と`DockerDeploymentConfig`を使用する正しい実装に変更
- エラーハンドリングの改善
- ログ出力の詳細化

## テスト結果

作成したテストスクリプト`test_simple_loading.py`で検証した結果：

```
Present fields: 14/14
Missing fields: []
🎉 All tests passed! All FEA-Bench fields are present in the JSONL.
```

## プロンプトでの利用可能性

これらの追加フィールドは、SWE-Agentのテンプレートシステムで以下のように利用できます：

```yaml
# config/default.yamlなどで利用例
instance_template: |-
  Repository: {{repo}}
  Pull Request: #{{pull_number}} ({{url}})
  
  <pr_description>
  {{problem_statement}}
  </pr_description>
  
  Related Issues: {{issue_numbers}}
  Test Information:
  - Tests that should pass: {{PASS_TO_PASS}}
  - Tests that should start passing: {{FAIL_TO_PASS}}
  
  {% if readmes %}
  Repository Documentation:
  {% for filename, content in readmes.items() %}
  ## {{filename}}
  {{content}}
  {% endfor %}
  {% endif %}
```

## 影響範囲

- ✅ 既存の機能には影響なし（後方互換性を維持）
- ✅ 新しいフィールドはオプショナルで、存在しない場合は`None`または空リストが設定される
- ✅ LLMがより豊富なコンテキスト情報を利用可能になる

## 今後の活用可能性

追加されたフィールドにより、以下のような高度な機能が実装可能になります：

1. **テスト結果の分析**: `FAIL_TO_PASS`、`PASS_TO_PASS`を使用
2. **リポジトリ理解の向上**: `readmes`、`files`を使用
3. **変更履歴の追跡**: `first_commit_time`、`created_at`を使用
4. **関連情報の参照**: `url`、`issue_numbers`を使用
5. **環境固有の処理**: `environment_setup_commit`を使用
