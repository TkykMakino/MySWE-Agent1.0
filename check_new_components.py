#!/usr/bin/env python3
"""
FEA-Benchトラジェクトリの新コンポーネント実装チェッカー

使用方法:
python check_new_components.py /path/to/trajectory/directory
"""

import json
import re
import ast
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass


@dataclass
class ComponentInfo:
    """コンポーネント情報"""
    name: str
    type: str
    file_path: str


@dataclass
class CheckResult:
    """チェック結果"""
    instance_id: str
    required_components: List[ComponentInfo]
    found_components: List[ComponentInfo]
    missing_components: List[ComponentInfo]
    patch_exists: bool
    patch_path: str
    success_rate: float


class NewComponentChecker:
    """新コンポーネント実装チェッカー"""
    
    def __init__(self):
        self.results = []
    
    def extract_new_components_from_prompt(self, content: str) -> List[ComponentInfo]:
        """インスタンスプロンプトから新コンポーネント情報を抽出"""
        components = []
        
        # "This PR introduces the following new components:" から始まる部分を探す
        start_pattern = r"This PR introduces the following new components:"
        end_pattern = r"Please implement these new components"
        
        match = re.search(f"{start_pattern}(.*?){end_pattern}", content, re.DOTALL)
        if not match:
            return components
        
        components_text = match.group(1)
        
        # ファイルパスとコンポーネントを抽出
        current_file = None
        for line in components_text.split('\n'):
            line = line.strip()
            
            # ファイルパスの行を検出
            if line.startswith('In file ') and line.endswith(':'):
                current_file = line[8:-1]  # "In file " を除去し、末尾の ":" を除去
                continue
            
            # コンポーネントの行を検出 (- で始まる)
            if line.startswith('- ') and current_file:
                # "- component_name (type: component_type)" の形式を解析
                component_match = re.match(r'- (.+?) \(type: (.+?)\)', line)
                if component_match:
                    name = component_match.group(1).strip()
                    comp_type = component_match.group(2).strip()
                    components.append(ComponentInfo(
                        name=name,
                        type=comp_type,
                        file_path=current_file
                    ))
        
        return components
    
    def extract_components_from_patch(self, patch_content: str) -> List[ComponentInfo]:
        """パッチファイルから実装されたコンポーネントを抽出"""
        components = []
        
        # パッチを行ごとに解析
        lines = patch_content.split('\n')
        current_file = None
        current_class = None
        
        # ファイル内の既存クラスを推測するためのマップ
        file_class_map = self._extract_class_context_from_patch(patch_content)
        
        for line in lines:
            # ファイルパスを検出 (+++ で始まる行)
            if line.startswith('+++'):
                # "+++ b/path/to/file.py" の形式からファイルパスを抽出
                match = re.match(r'\+\+\+ b/(.+)', line)
                if match:
                    current_file = match.group(1)
                    current_class = None  # 新しいファイルではクラスコンテキストをリセット
                continue
            
            # 追加された行を検出 (+ で始まる行)
            if line.startswith('+') and current_file:
                line_content = line[1:]  # + を除去（インデントを保持）
                stripped_content = line_content.strip()
                
                # クラス定義を検出
                class_match = re.match(r'class\s+(\w+)', stripped_content)
                if class_match:
                    class_name = class_match.group(1)
                    current_class = class_name
                    components.append(ComponentInfo(
                        name=class_name,
                        type='class',
                        file_path=current_file
                    ))
                
                # 関数定義を検出
                func_match = re.match(r'def\s+(\w+)', stripped_content)
                if func_match:
                    func_name = func_match.group(1)
                    
                    # インデントレベルを計算（先頭の空白文字数）
                    indent_level = len(line_content) - len(line_content.lstrip())
                    
                    # クラス内のメソッドかどうかを判定
                    if indent_level >= 4:  # 4スペース以上のインデント
                        # 現在のクラスコンテキストを使用、なければファイルから推測
                        class_name = current_class or file_class_map.get(current_file)
                        if class_name:
                            # クラスメソッドとして記録
                            full_name = f"{class_name}.{func_name}"
                            components.append(ComponentInfo(
                                name=full_name,
                                type='function',
                                file_path=current_file
                            ))
                        else:
                            # クラス名が不明な場合は通常の関数として記録
                            components.append(ComponentInfo(
                                name=func_name,
                                type='function',
                                file_path=current_file
                            ))
                    else:
                        # トップレベル関数として記録
                        components.append(ComponentInfo(
                            name=func_name,
                            type='function',
                            file_path=current_file
                        ))
                        # トップレベル関数が見つかったらクラスコンテキストをリセット
                        if indent_level == 0:
                            current_class = None
                
                # クラスの終了を検出（インデントレベルが戻った場合）
                elif current_class and stripped_content:
                    indent_level = len(line_content) - len(line_content.lstrip())
                    # インデントレベルが0に戻り、新しいクラスや関数定義でない場合はクラス終了
                    if (indent_level == 0 and 
                        not stripped_content.startswith('class ') and 
                        not stripped_content.startswith('def ') and
                        not stripped_content.startswith('#') and 
                        not stripped_content.startswith('"""') and
                        not stripped_content.startswith('\'\'\'') and
                        stripped_content not in ['', ')']):
                        current_class = None
        
        return components
    
    def _extract_class_context_from_patch(self, patch_content: str) -> Dict[str, str]:
        """パッチファイルからファイル別の主要クラス名を推測"""
        file_class_map = {}
        
        lines = patch_content.split('\n')
        current_file = None
        
        for line in lines:
            # ファイルパスを検出
            if line.startswith('+++'):
                match = re.match(r'\+\+\+ b/(.+)', line)
                if match:
                    current_file = match.group(1)
                continue
            
            # 既存のクラス定義を検出（変更されていない行から）
            if current_file and (line.startswith(' ') or line.startswith('-')):
                content = line[1:].strip() if line.startswith((' ', '-')) else line.strip()
                class_match = re.match(r'class\s+(\w+)', content)
                if class_match:
                    file_class_map[current_file] = class_match.group(1)
            
            # ファイル名からクラス名を推測（最後の手段）
            if current_file and current_file not in file_class_map:
                # request/__init__.py -> Request, user.py -> UserManager など
                if 'request' in current_file.lower():
                    file_class_map[current_file] = 'Request'
                elif 'user' in current_file.lower():
                    file_class_map[current_file] = 'UserManager'
        
        return file_class_map
    
    def check_instance(self, instance_dir: Path) -> CheckResult:
        """単一インスタンスをチェック"""
        instance_id = instance_dir.name
        traj_file = instance_dir / f"{instance_id}.traj"
        patch_file = instance_dir / f"{instance_id}.patch"
        
        # トラジェクトリファイルから要求されたコンポーネントを抽出
        required_components = []
        if traj_file.exists():
            try:
                with open(traj_file, 'r', encoding='utf-8') as f:
                    traj_data = json.load(f)
                
                # historyから最初のユーザーメッセージ（インスタンスプロンプト）を探す
                for entry in traj_data.get('history', []):
                    if entry.get('role') == 'user':
                        content = entry.get('content', '')
                        
                        # contentがリストの場合は処理
                        if isinstance(content, list):
                            # リスト内の各要素を処理
                            full_text = ""
                            for item in content:
                                if isinstance(item, dict) and 'text' in item:
                                    full_text += item['text'] + '\n'
                                elif isinstance(item, str):
                                    full_text += item + '\n'
                                else:
                                    full_text += str(item) + '\n'
                            content = full_text
                        elif not isinstance(content, str):
                            content = str(content)
                        
                        if 'This PR introduces' in content:
                            required_components = self.extract_new_components_from_prompt(content)
                            break
            except Exception as e:
                print(f"Error reading trajectory file {traj_file}: {e}")
        
        # パッチファイルから実装されたコンポーネントを抽出
        found_components = []
        patch_exists = patch_file.exists()
        if patch_exists:
            try:
                with open(patch_file, 'r', encoding='utf-8') as f:
                    patch_content = f.read()
                found_components = self.extract_components_from_patch(patch_content)
            except Exception as e:
                print(f"Error reading patch file {patch_file}: {e}")
        
        # 不足しているコンポーネントを特定
        missing_components = []
        for required in required_components:
            found = False
            for found_comp in found_components:
                # 名前とファイルパスが一致するかチェック
                if (required.name == found_comp.name and 
                    required.file_path == found_comp.file_path):
                    found = True
                    break
            if not found:
                missing_components.append(required)
        
        # 成功率を計算
        success_rate = 0.0
        if required_components:
            implemented_count = len(required_components) - len(missing_components)
            success_rate = implemented_count / len(required_components)
        
        return CheckResult(
            instance_id=instance_id,
            required_components=required_components,
            found_components=found_components,
            missing_components=missing_components,
            patch_exists=patch_exists,
            patch_path=str(patch_file),
            success_rate=success_rate
        )
    
    def check_directory(self, trajectory_dir: Path) -> List[CheckResult]:
        """トラジェクトリディレクトリ内の全インスタンスをチェック"""
        results = []
        
        # ディレクトリ内の各サブディレクトリ（インスタンス）をチェック
        for item in trajectory_dir.iterdir():
            if item.is_dir():
                result = self.check_instance(item)
                results.append(result)
                self.results.append(result)
        
        return results
    
    def print_summary(self, results: List[CheckResult]):
        """結果のサマリーを出力"""
        print("=" * 80)
        print("新コンポーネント実装チェック結果サマリー")
        print("=" * 80)
        
        total_instances = len(results)
        total_required = sum(len(r.required_components) for r in results)
        total_implemented = sum(len(r.required_components) - len(r.missing_components) for r in results)
        
        print(f"総インスタンス数: {total_instances}")
        print(f"総要求コンポーネント数: {total_required}")
        print(f"総実装コンポーネント数: {total_implemented}")
        print(f"全体成功率: {total_implemented/total_required*100:.1f}%" if total_required > 0 else "全体成功率: N/A")
        print()
        
        # インスタンス別詳細
        for result in results:
            print(f"インスタンス: {result.instance_id}")
            print(f"  パッチファイル: {'存在' if result.patch_exists else '不存在'}")
            print(f"  要求コンポーネント数: {len(result.required_components)}")
            print(f"  実装コンポーネント数: {len(result.required_components) - len(result.missing_components)}")
            print(f"  成功率: {result.success_rate*100:.1f}%")
            
            if result.missing_components:
                print("  不足コンポーネント:")
                for missing in result.missing_components:
                    print(f"    - {missing.name} ({missing.type}) in {missing.file_path}")
            else:
                print("  ✅ 全コンポーネント実装済み")
            print()
    
    def save_detailed_report(self, results: List[CheckResult], output_file: str):
        """詳細レポートをJSONファイルに保存"""
        report_data = []
        
        for result in results:
            report_data.append({
                'instance_id': result.instance_id,
                'patch_exists': result.patch_exists,
                'patch_path': result.patch_path,
                'success_rate': result.success_rate,
                'required_components': [
                    {
                        'name': comp.name,
                        'type': comp.type,
                        'file_path': comp.file_path
                    } for comp in result.required_components
                ],
                'found_components': [
                    {
                        'name': comp.name,
                        'type': comp.type,
                        'file_path': comp.file_path
                    } for comp in result.found_components
                ],
                'missing_components': [
                    {
                        'name': comp.name,
                        'type': comp.type,
                        'file_path': comp.file_path
                    } for comp in result.missing_components
                ]
            })
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        print(f"詳細レポートを {output_file} に保存しました。")


def main():
    if len(sys.argv) != 2:
        print("使用方法: python check_new_components.py <trajectory_directory>")
        print("例: python check_new_components.py /home/makino/MySWE-Agent1.0/trajectories/makino/default__claude-3-7-sonnet-latest__t-0.00__p-1.00__c-3.00___FEA-Bench-v1.0-medium")
        sys.exit(1)
    
    trajectory_dir = Path(sys.argv[1])
    
    if not trajectory_dir.exists():
        print(f"エラー: ディレクトリ {trajectory_dir} が存在しません。")
        sys.exit(1)
    
    if not trajectory_dir.is_dir():
        print(f"エラー: {trajectory_dir} はディレクトリではありません。")
        sys.exit(1)
    
    checker = NewComponentChecker()
    results = checker.check_directory(trajectory_dir)
    
    if not results:
        print("チェック対象のインスタンスが見つかりませんでした。")
        sys.exit(1)
    
    checker.print_summary(results)
    
    # 詳細レポートを保存
    report_file = trajectory_dir / "new_components_check_report.json"
    checker.save_detailed_report(results, str(report_file))


if __name__ == "__main__":
    main()
