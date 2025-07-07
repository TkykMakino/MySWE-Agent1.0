import os
import re
import json
import anthropic

# --- 設定項目 ---

# 1. Claude APIキーの設定
# 環境変数 `ANTHROPIC_API_KEY` に設定しておくことを推奨します。
# このスクリプトは ANTHROPIC_API_KEY という名前の環境変数を読み込みます。
try:
    # 環境変数からAPIキーを読み込む（クライアント初期化時に自動で読み込まれる）
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise KeyError
except KeyError:
    print("エラー: 環境変数 `ANTHROPIC_API_KEY` が設定されていません。")
    print("APIキーを環境変数に設定してください。")
    exit(1)


# 2. 生成AIに与えるプロンプト
# ここの指示内容を調整することで、生成されるコードの質や多様性を変えられます。
PROMPT_TEMPLATE = """
あなたは、Pythonコードに適切なアサーションを挿入するエキスパートです。

以下の指示に従って、アサーション挿入前のコードと挿入後のコードのペアを10個生成してください。

# 指示
- 非常にシンプルな関数から、少しだけ複雑なものまで、様々なレベルのコードを生成してください。
- 挿入するアサーションは、変数の型チェック、値の範囲チェック、Noneでないことの確認、リストや辞書が空でないことの確認など、多様なパターンを含めてください。
- 各ペアは、必ず以下の形式で出力してください。後処理で機械的に抽出します。ペアとペアの間は必ず `---` で区切ってください。

[BEFORE]
```python
# (ここにアサーション挿入前のコード)
```

[AFTER]
```python
# (ここにアサーション挿入後のコード)
```
---
"""

# 3. 出力ファイル名
OUTPUT_FILENAME = "assertion_dataset.jsonl"

# --- 関数定義 ---

def generate_text_from_ai(prompt: str) -> str | None:
    """
    指定されたプロンプトを使って、Claude APIからテキストを生成する。
    """
    print("🤖 Claudeにデータ生成を問い合わせ中...")
    try:
        # クライアントは環境変数 `ANTHROPIC_API_KEY` を自動で参照します
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-3-7-sonnet-latest", # Claude 3.7 Sonnet を使用
            max_tokens=4096,  # 生成するテキストの最大長を十分に確保
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        print("✅ Claudeからの応答を取得しました。")
        return message.content[0].text
    except Exception as e:
        print(f"❌ API呼び出し中にエラーが発生しました: {e}")
        return None

def parse_assertion_pairs(generated_text: str) -> list[dict[str, str]]:
    """
    AIが生成したテキストから、アサーション挿入前後のコードペアを抽出する。
    """
    print("🔍 応答テキストを解析中...")
    
    # --- で各ペアに分割する
    pairs_text = generated_text.strip().split('---')
    
    dataset = []
    for i, pair_block in enumerate(pairs_text):
        if not pair_block.strip():
            continue

        try:
            # [BEFORE] と [AFTER] の間のコードを正規表現で探す
            before_match = re.search(r"\[BEFORE\]\s*```python\n(.*?)\n```", pair_block, re.DOTALL)
            after_match = re.search(r"\[AFTER\]\s*```python\n(.*?)\n```", pair_block, re.DOTALL)

            if before_match and after_match:
                before_code = before_match.group(1).strip()
                after_code = after_match.group(1).strip()
                
                dataset.append({
                    "before": before_code,
                    "after": after_code
                })
        except Exception as e:
            print(f"ブロック {i+1} の解析中にエラーが発生しました: {e}\nブロック内容:\n{pair_block}")
            
    print(f"👍 {len(dataset)}個のコードペアを抽出しました。")
    return dataset

def save_to_jsonl(data: list[dict], filename: str):
    """
    抽出したデータをJSON Lines形式でファイルに追記保存する。
    """
    print(f"💾 データセットを `{filename}` に追記保存中...")
    try:
        # ファイルオープンモードを 'a' (append) に変更
        with open(filename, 'a', encoding='utf-8') as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        print("🎉 追記保存が完了しました！")
    except IOError as e:
        print(f"❌ ファイルの書き込み中にエラーが発生しました: {e}")

# --- メイン処理 ---

if __name__ == "__main__":
    # 1. AIからテキストを生成
    generated_text = generate_text_from_ai(PROMPT_TEMPLATE)

    if generated_text:
        # 2. テキストをパースしてコードペアを抽出
        dataset = parse_assertion_pairs(generated_text)
        
        if dataset:
            # 3. 抽出したデータをファイルに追記保存
            save_to_jsonl(dataset, OUTPUT_FILENAME)
        else:
            print("抽出できる有効なコードペアが見つかりませんでした。")
