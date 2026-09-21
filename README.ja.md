# Graph Engineering Kit

作業の依存関係、並行処理、確認の合流、承認と再開を管理するツールキットです。

[English](README.md) · [操作手順](docs/USAGE.md) · [設計と制約](docs/ARCHITECTURE.md) · [Codex・Claude Code](docs/INTEGRATIONS.md)

## クローン後の起動

必要なものはGitと **Python 3.10以上** です。追加パッケージのインストールやAPIキーは、付属の利用例には不要です。Pythonは制御ツールの実行用で、開発するアプリの言語を指定するものではありません。

```sh
# 付属の利用例とツール自体の検査を実行します
git clone <repository-url> graph-engineering-kit
cd graph-engineering-kit
python3 kit.py doctor
python3 kit.py demo
python3 kit.py check
```

Windowsでは `py -3 kit.py ...` または `dev.cmd ...`、PowerShellでは `./dev.ps1 ...` を使用できます。macOS・Linuxでは `./dev ...` も使用できます。

`demo` は一時的なプロジェクトで、成功する経路と拒否すべき経路を実際に実行します。`check` はツール自体の回帰検査です。実際のアプリを任せる際は、そのアプリの仕様・対象ファイル・検証コマンドを設定します。実際の成果物は、設定した検証コマンドの結果で確認してください。

## 自分のプロジェクトへの導入

`python3 kit.py new ../my-workspace` で、すぐ試せる作業場所を空のディレクトリへ作成できます。既存プロジェクトには `python3 kit.py install --target ../existing-project` で追加するファイルを確認し、`--apply` で導入します。既存のAGENTS.md、CLAUDE.md、設定やフックは上書きしません。

検証コマンドは引数の配列で指定します。特定のアプリ言語やテストフレームワークを必須にはしていません。

## コンテキストとハーネス

`git push`を禁止する文章も、AIへ渡す指示であるためコンテキストに含まれます。ハーネスは、その指示を利用する実行環境、ツール、権限、検証処理などを提供します。禁止事項の有無だけでは両者を区別できません。

同じアカウントで編集できるローカル設定やフックには回避手段があります。権限を強制する必要がある場合は、制限された認証情報や隔離された実行環境も設計します。具体的な対応範囲は [設計と制約](docs/ARCHITECTURE.md) を参照してください。

ライセンスはMITです。第三者のコードは [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。
