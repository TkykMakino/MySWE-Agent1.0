import os
import re
import json
import anthropic

# --- 設定項目 ---

# 1. Claude APIキーの設定
# 環境変数 `ANTHROPIC_API_KEY` を読み込みます。
try:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise KeyError
except KeyError:
    print("エラー: 環境変数 `ANTHROPIC_API_KEY` が設定されていません。")
    exit(1)

# 2. 生成AIに与えるプロンプト
# AIに出力してほしい情報の形式を、JSONスキーマで厳密に指定します。
PROMPT_TEMPLATE = """
あなたは、Pythonコードのリファクタリングとテストケース生成のエキスパートです。
既存のPythonコードに対してアサーションとデバッグプリントを挿入する際に参考になるような例を作成するのが仕事です。

以下の指示と注意に従って、指定された形式のJSONオブジェクトを5個生成してください。

### 指示
- 多様なPython関数を題材としてください。
- 各関数に、適切なアサーションとデバッグプリントを追加してください。
- アサーションは、関数の事前条件（引数の型、値の範囲など）を厳密にチェックするものとします。
- デバッグプリントは、処理の開始、主要な変数の状態、処理の終了など、プログラムの動作が追跡しやすくなるようにf-stringを使って挿入してください。
- **`success`テストケースは、正常系の動作パターンをすべて網羅するように、最低1つ以上、必要な数だけ生成してください。**
- **`failure`テストケースは、想定される各アサーションにそれぞれ引っかかるように、挿入したアサーションの数に合わせて、必要な数だけ生成してください。** これにより、全てのアサーションがテスト可能になります。

{additional_instructions}

### 注意
- 生成する各JSONオブジェクトは、**必ず**以下のスキーマに従ってください。
- `input`や`expected_output`の値は、Pythonの構文として正しい文字列にしてください (例: 文字列なら`"'hello'"`、リストなら`"[1, 2]"`など)。
- `description`に記載する説明は関数それ自体の説明ではなく、アサーションとデバッグプリントの挿入意図と、その挿入が有効と判断できる条件の説明を中心としてください。
- 対象とする関数を局所的すぎるもの、複雑すぎるものにせず、例としてある程度の汎用性を維持してください。
- 関数の意図を考えずとも、単に構文の形のみを見て挿入できるものなど、単純な例を含めて構いません。その場合、アサーションしか挿入しないケース、あるいはデバッグプリントしか挿入しないケースがあって構いません。
- アサーション及びデバッグプリントの挿入はできる限り多く行ってください。変数の中身の確認などは特に漏らさないよう注意してください。

### 出力JSONスキーマ
```json
{
  "title": "この関数の役割を示す簡単なタイトルです (例: 'Function to Calculate Average')。",
  "description": "このアサーションとデバッグプリントのセットが有効な状況についての短い説明です。どのような構文や条件の際にこのパターンを適用すべきか記述してください。",
  "keywords": [
    "このパターンを検討するきっかけとなるPythonのキーワードや関数名のリストです。（例: 'if', 'for', 'dict.get'）"
  ],
  "code_before": "リファクタリング前のPythonコード文字列です。",
  "code_after": "アサーションとデバッグプリントを追加した後のPythonコード文字列です。コード内のインデントや改行はJSON文字列として正しくエスケープしてください。",
  "test_cases": {
    "success": [
      {
        "input": ["（正常系テストの引数1）", "（引数2）"],
        "expected_output": "（正常に実行された場合の返り値）"
      },
      {
        "input": ["（別の正常系テストの引数1）", "（引数2）"],
        "expected_output": "（別の正常な返り値）"
      }
    ],
    "failure": [
      {
        "input": ["（アサーション1に引っかかる引数）"],
        "expected_exception": "AssertionError",
        "expected_message_part": "（アサーション1のエラーメッセージの一部）"
      },
      {
        "input": ["（アサーション2に引っかかる引数）"],
        "expected_exception": "AssertionError",
        "expected_message_part": "（アサーション2のエラーメッセージの一部）"
      }
    ]
  }
}
```

### 生成開始
各JSONオブジェクトの間は、必ず `---` で区切ってください。
```
---
```
"""

# 3. 出力ファイル名
OUTPUT_FILENAME = "rich_assertion_dataset.jsonl"

# --- 関数定義 ---

def read_existing_titles(filename: str) -> list[str]:
    """
    既存のデータセットファイルからタイトルのリストを読み込む。
    """
    titles = []
    if not os.path.exists(filename):
        return titles

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if 'title' in data and data['title']:
                        titles.append(data['title'])
                except json.JSONDecodeError:
                    # 不正な行は無視
                    continue
        print(f"📖 既存のデータセットから{len(titles)}件のタイトルを読み込みました。")
    except Exception as e:
        print(f"⚠️ 既存データセットの読み込み中にエラーが発生しました: {e}")
    
    return titles

def generate_text_from_ai(prompt: str) -> str | None:
    """指定されたプロンプトを使って、Claude APIからテキストを生成する。"""
    print("🤖 Claudeにデータ生成を問い合わせ中...")
    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-3-7-sonnet-latest",
            max_tokens=4096,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        print("✅ Claudeからの応答を取得しました。")
        # 応答の先頭と末尾にある可能性のある区切り文字を削除
        return message.content[0].text.strip().removeprefix("---").strip()
    except Exception as e:
        print(f"❌ API呼び出し中にエラーが発生しました: {e}")
        return None

def parse_refactoring_json(generated_text: str) -> list[dict]:
    """AIが生成したテキストから、リファクタリング情報のJSONオブジェクトを抽出する。"""
    print("🔍 応答テキストを解析中...")
    
    # --- で各JSONブロックの可能性のある塊に分割する
    potential_blocks = generated_text.strip().split('---')
    
    dataset = []
    for i, block in enumerate(potential_blocks):
        if not block.strip():
            continue
        
        try:
            # ブロックの中からJSONの部分だけを探し出す
            # ```json ... ``` というマークダウン形式を優先して探す
            match = re.search(r'```json\s*({.*?})\s*```', block, re.DOTALL)
            # 見つからなければ、単体の {...} を探す
            if not match:
                match = re.search(r'({.*?})', block, re.DOTALL)

            if match:
                json_string = match.group(1)
                data_item = json.loads(json_string.strip())
                dataset.append(data_item)
            else:
                # ブロック内にJSONが見つからなかった場合
                print(f"ブロック {i+1} で有効なJSONオブジェクトが見つかりませんでした。スキップします。")

        except json.JSONDecodeError as e:
            print(f"ブロック {i+1} のJSON解析中にエラーが発生しました: {e}")
            print(f"問題のブロック:\n{block.strip()}")
        except Exception as e:
            print(f"ブロック {i+1} の処理中に予期せぬエラーが発生しました: {e}")

    print(f"👍 {len(dataset)}個のデータ項目を抽出しました。")
    return dataset

def save_to_jsonl(data: list[dict], filename: str):
    """抽出したデータをJSON Lines形式でファイルに追記保存する。"""
    print(f"💾 データセットを `{filename}` に追記保存中...")
    try:
        with open(filename, 'a', encoding='utf-8') as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        print("🎉 追記保存が完了しました！")
    except IOError as e:
        print(f"❌ ファイルの書き込み中にエラーが発生しました: {e}")

# --- メイン処理 ---

if __name__ == "__main__":
    # 1. 既存のデータセットからタイトルリストを読み込む
    existing_titles = read_existing_titles(OUTPUT_FILENAME)
    
    # 2. 既存タイトルリストに基づいて追加の指示を作成
    additional_instructions = ""
    if existing_titles:
        # set()で重複を除外してからリストアップする
        titles_str = "\n".join(f"- {title}" for title in set(existing_titles))
        additional_instructions = f"""
### 既存のテーマリスト
これらはすでにデータが作られている関数のテーマです。できる限りこれらと被らない関数についてデータを生成してください。
{titles_str}
"""
    
    # 3. プロンプトをフォーマット
    final_prompt = PROMPT_TEMPLATE.format(additional_instructions=additional_instructions)
    
    # 4. AIからテキストを生成
    generated_text = generate_text_from_ai(final_prompt)

    if generated_text:
        # 5. テキストをパースしてコードペアを抽出
        dataset = parse_refactoring_json(generated_text)
        
        if dataset:
            # 6. 抽出したデータをファイルに追記保存
            save_to_jsonl(dataset, OUTPUT_FILENAME)
        else:
            print("抽出できる有効なデータ項目が見つかりませんでした。")
