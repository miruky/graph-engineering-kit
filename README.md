# グラフエンジニアリングの導入テンプレート

作業の依存関係を定義し、順番に実行する処理、並行して進める検査、承認待ちを管理します。実行結果を保存し、各処理の状態をノード図で確認できます。

[英語版](README.en.md) · [操作手順](docs/USAGE.md) · [設計と制約](docs/ARCHITECTURE.md)

## クローン後に試す

必要なものはGitと **Python 3.10以上** です。Pythonはこのツールを動かすために使い、対象アプリの開発言語は限定していません。付属の利用例には、追加パッケージやAPIキーは不要です。

クローンしたディレクトリで実行してください。

```sh
# 実行環境と付属の利用例を確認します
python3 kit.py doctor
python3 kit.py demo
```

Windowsでは `python3` を `py -3` へ置き換えてください。`dev.cmd` やPowerShellの `./dev.ps1` も使えます。macOS・Linuxでは `./dev` が短い呼び出し方です。

`demo` は一時的な作業場所で動きます。クローンしたテンプレートの設定や成果物を、確認済みの状態へ変更しません。

## 処理の順序を設定する

`.agentkit/graph.json` に各処理の入力・出力、依存する処理、再試行の条件を指定します。`inputs` は参照のみのファイル、`outputs` はその処理で変更するファイルです。通常のコマンド、独立した検証を伴うAIの作業、承認待ちを組み合わせられます。Codex・Claude Codeの設定例も付属しています。

```sh
# 付属の処理を実行し、承認待ちになった状態を確認します
python3 kit.py run
python3 kit.py status
```

付属例は承認待ちで停止し、`run` が終了コード3を返します。結果を確認して承認し、再開する手順は [操作手順](docs/USAGE.md) に記載しています。

```sh
# ノード図と処理結果を、手元のブラウザーで確認します
python3 kit.py view --serve
```

この実装は、循環のない有向グラフと、回数を制限した再試行が対象です。実行中に依存関係を変更する機能はありません。承認は対象の実行とファイルの状態に対応し、古い検証記録や外部から変更された成果物をそのまま再利用しないように確認します。

## 自分のプロジェクトへ導入する

新しく始める場合は、空の作業場所を作成できます。

```sh
# テンプレートと実行ツールを新しい作業場所へコピーします
python3 kit.py new ../my-project
```

既存のプロジェクトへ追加する場合は、追加予定のファイルを確認してから適用します。

```sh
# 追加内容を確認してから適用します
python3 kit.py install --target ../existing-project
python3 kit.py install --target ../existing-project --apply
```

既存のAGENTS.md、CLAUDE.md、設定、フックは上書きしません。`.agentkit/graph.example.json` のパスやコマンドを実際のプロジェクトへ合わせ、`.agentkit/graph.json` として保存してください。導入先のディレクトリでは `python3 .agentkit/tools/graph/kit.py --root . inspect` で設定を確認できます。

## 設計上の範囲

このテンプレートは手元で使う開発用ツールです。ローカルの設定や記録は、利用者自身が変更できます。実行権限を制限する場合は、認証情報や実行環境側でも制御してください。詳しい対応範囲は [設計と制約](docs/ARCHITECTURE.md) に記載しています。

ツール自体を変更したときの検査は `python3 kit.py check` で実行できます。ライセンスは [MIT](LICENSE) です。第三者のコードの出典は [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。
