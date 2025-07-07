import json
import ast
import traceback
import os
import sys
import shutil
import numpy
import bcrypt
import requests
from unittest.mock import patch

# --- 設定項目 ---
# テスト対象のデータセットファイル
DATASET_FILENAME = "rich_assertion_dataset.jsonl"


def safe_literal_eval(val):
    """
    ast.literal_evalの安全なラッパー。数値やNoneを直接受け取れるようにする。
    """
    if isinstance(val, str):
        try:
            return ast.literal_eval(val)
        except (ValueError, SyntaxError, MemoryError, TypeError):
            # 評価できない文字列はそのまま返す
            return val
    # 文字列以外はそのまま返す
    return val


def setup_test_env_for_process_file():
    """process_fileのテスト環境を準備する"""
    print("  [環境準備] process_file用のテストファイルを作成します...")
    # ディレクトリ作成
    os.makedirs('./existing_directory', exist_ok=True)
    # テキストファイル作成
    with open('./test_file.txt', 'w') as f:
        f.write('This is a test file content')
    # バイナリファイル作成
    with open('./binary_file.bin', 'wb') as f:
        f.write(b'binary content')
    # 読み取り専用ファイル作成（権限変更はUnix系のみ有効）
    with open('./readonly_file.txt', 'w') as f:
        f.write('readonly')
    try:
        os.chmod('./readonly_file.txt', 0o444)
    except OSError:
        print("    - 注意: ファイル権限の変更はスキップされました（Windows環境の可能性）。")


def cleanup_test_env_for_process_file():
    """process_fileのテスト環境をクリーンアップする"""
    print("  [環境クリーンアップ] 作成したテストファイルを削除します...")
    files_to_remove = ['./test_file.txt', './binary_file.bin', './readonly_file.txt']
    for f in files_to_remove:
        try:
            # 読み取り専用でも削除できるように権限を戻す
            os.chmod(f, 0o666)
            os.remove(f)
        except OSError:
            pass # ファイルが存在しない場合は何もしない
    try:
        shutil.rmtree('./existing_directory')
    except OSError:
        pass


def run_tests(filename: str):
    """
    指定されたデータセットファイルを読み込み、すべてのテストケースを自動実行する。
    """
    passed_sets = []
    failed_sets = []

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for i, line in enumerate(lines):
                has_failure = False
                func_name_for_summary = f"関数セット {i+1}"
                test_env_setup = None

                try:
                    data = json.loads(line)
                    print(f"\n--- テスト開始: 関数セット {i+1} ---")
                    
                    # --- 1. テスト対象の関数を準備 ---
                    code_after = data['code_after']
                    func_name = code_after.split('def ')[1].split('(')[0]
                    func_name_for_summary = f"関数セット {i+1} (`{func_name}`)"
                    
                    exec_scope = {'numpy': numpy, 'np': numpy} # numpyを使えるようにスコープに追加
                    exec(code_after, exec_scope)
                    test_function = exec_scope[func_name]
                    
                    print(f"関数 `{func_name}` をテストします。")

                    # 関数名に応じた環境準備
                    if func_name == 'process_file':
                        setup_test_env_for_process_file()
                        test_env_setup = 'process_file'

                    # --- 2. 成功（Success）ケースのテスト ---
                    print("\n[成功ケースのテスト]")
                    success_cases = data['test_cases'].get('success', [])
                    if not success_cases:
                        print("  (テストケースなし)")
                    
                    for j, case in enumerate(success_cases):
                        try:
                            # 'np.'で始まるコードはevalで評価し、他は安全な評価を試みる
                            args = [eval(arg, exec_scope) if isinstance(arg, str) and arg.startswith('np.') else safe_literal_eval(arg) for arg in case['input']]
                            actual_output = test_function(*args)
                            
                            # イテレータはリストに変換
                            if hasattr(actual_output, '__iter__') and not isinstance(actual_output, (str, bytes, dict, list)):
                                actual_output = list(actual_output)

                            # --- 関数ごとの特別な比較ロジック ---
                            passed = False
                            if func_name == 'validate_and_encrypt_password':
                                hashed, error = actual_output
                                original_password = args[1].encode('utf-8')
                                if hashed and bcrypt.checkpw(original_password, hashed):
                                    passed = True
                                    actual_output = ('(正しいハッシュ値)', None) # 表示用に整形
                            else:
                                expected_output_str = case['expected_output']
                                # 期待値が特殊な形式の場合の処理
                                if 'array with shape' in expected_output_str or 'of zeros' in expected_output_str or 'of blurred ones' in expected_output_str:
                                    # apply_gaussian_blur のようなケースは内容の一致までは見ない（今回は形状と型で判断）
                                    expected_shape = ast.literal_eval(expected_output_str.split('shape ')[1].split(')')[0] + ')')
                                    if actual_output.shape == expected_shape:
                                        passed = True
                                else:
                                    expected_output = safe_literal_eval(expected_output_str)
                                    if func_name == 'make_api_request' and case['input'][1].upper() == "'POST'":
                                        # POSTリクエストは返り値が不安定なのでキーの存在でチェック
                                        if isinstance(actual_output, dict) and 'id' in actual_output and actual_output['id'] == expected_output['id']:
                                            passed = True
                                    elif actual_output == expected_output:
                                        passed = True

                            if passed:
                                print(f"  ✅ PASS: ケース {j+1}")
                            else:
                                print(f"  ❌ FAIL: ケース {j+1}")
                                print(f"    - 入力: {case['input']}")
                                print(f"    - 期待値: {case['expected_output']}")
                                print(f"    - 実行結果: {actual_output}")
                                has_failure = True
                        except Exception as e:
                            print(f"  🔥 ERROR: ケース {j+1} の実行中にエラーが発生しました。")
                            print(f"    - 入力: {case['input']}")
                            print(f"    - エラー: {type(e).__name__} - {e}")
                            has_failure = True

                    # --- 3. 失敗（Failure）ケースのテスト ---
                    print("\n[失敗ケースのテスト]")
                    failure_cases = data['test_cases'].get('failure', [])
                    if not failure_cases:
                        print("  (テストケースなし)")

                    for k, case in enumerate(failure_cases):
                        try:
                            args = [eval(arg, exec_scope) if isinstance(arg, str) and arg.startswith('np.') else safe_literal_eval(arg) for arg in case['input']]
                            
                            # デバッグ出力を抑制
                            with open(os.devnull, 'w') as f_null:
                                old_stdout = sys.stdout
                                sys.stdout = f_null
                                try:
                                    # apply_gaussian_blurのscipyがないエラーをモックでシミュレート
                                    if func_name == 'apply_gaussian_blur' and 'scipy' not in sys.modules:
                                         with patch.dict('sys.modules', {'scipy.ndimage': None}):
                                            test_function(*args)
                                    else:
                                        test_function(*args)
                                    
                                    # 例外が出なかったら失敗
                                    sys.stdout = old_stdout
                                    print(f"  ❌ FAIL: ケース {k+1} - 例外が発生しませんでした。")
                                    has_failure = True
                                finally:
                                    sys.stdout = old_stdout
                        
                        except Exception as e:
                            expected_exception_name = case.get('expected_exception', 'AssertionError')
                            expected_message_part = case.get('expected_message_part', '')
                            
                            actual_exception_name = type(e).__name__
                            actual_message = str(e)

                            if actual_exception_name == expected_exception_name and expected_message_part in actual_message:
                                print(f"  ✅ PASS: ケース {k+1} - 期待通りの例外({actual_exception_name})が発生しました。")
                            else:
                                print(f"  ❌ FAIL: ケース {k+1} - 予期せぬ例外/メッセージが発生しました。")
                                print(f"    - 入力: {case['input']}")
                                print(f"    - 期待した例外: {expected_exception_name} (メッセージに'{expected_message_part}'を含む)")
                                print(f"    - 発生した例外: {actual_exception_name} (メッセージ: '{actual_message}')")
                                has_failure = True

                except (json.JSONDecodeError, KeyError, Exception) as e:
                    print(f"\n--- エラー: 行 {i+1} の処理中に致命的なエラーが発生しました。 ---")
                    print(f"  理由: {type(e).__name__} - {e}")
                    # traceback.print_exc() # 詳細なデバッグ用
                    has_failure = True
                
                finally:
                    # 環境のクリーンアップ
                    if test_env_setup == 'process_file':
                        cleanup_test_env_for_process_file()

                # --- 4. 結果をリストに追加 ---
                if has_failure:
                    failed_sets.append(func_name_for_summary)
                else:
                    passed_sets.append(func_name_for_summary)

    except FileNotFoundError:
        print(f"エラー: データセットファイル `{filename}` が見つかりません。")
        return

    # --- 5. 最終サマリーの表示 ---
    print("\n\n" + "="*50)
    print(" 全テストサマリー")
    print("="*50)

    print("\n✅ 全てのテストに合格した関数セット:")
    if passed_sets:
        for item in passed_sets:
            print(f"  - {item}")
    else:
        print("  (なし)")

    print("\n❌ 失敗またはエラーがあった関数セット:")
    if failed_sets:
        for item in failed_sets:
            print(f"  - {item}")
    else:
        print("  (なし)")
    print("\n" + "="*50)


# --- メイン処理 ---
if __name__ == "__main__":
    # 必要なライブラリの存在チェック
    try:
        import scipy
    except ImportError:
        print("注意: `scipy`ライブラリが見つかりません。`apply_gaussian_blur`のテストは一部スキップされます。")
        print("インストール推奨: pip install scipy")

    run_tests(DATASET_FILENAME)

