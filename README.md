# 100BeautiesLab_ShapeChangerAI

百花繚乱研究所のキャラクターの形状変化絵を制作するワークスペースです。創作支援役は [扇 四春（3代目ラジアン代理）](SOUL.md) です。 [OpenAI の画像生成ガイド](https://developers.openai.com/api/docs/guides/image-generation) · [Google の画像生成ガイド](https://ai.google.dev/gemini-api/docs/image-generation)

## はじめに

1. [サブモジュール運用](AGENTS.md#サブモジュール運用) に従ってキャラクターデータと形状変化の参照資料を取得します。
2. 使うエージェントでリポジトリのルートを開き、[エージェント設定の読み込み経路](AGENTS.md#エージェント設定の配布構成) を確認します。
3. キャラクターと形状を指定して制作を依頼します。制作の流れは [制作と確認](AGENTS.md#制作と確認)、成果物の扱いは [出力と記録](AGENTS.md#出力と記録) を参照してください。

コマンドから直接生成する場合は [生成パイプラインの使い方](src/README.md) を参照してください。API キーは [.env.example](.env.example) を `.env` にコピーして設定します。

## エージェントから開く

- **Codex** では、このリポジトリのローカルフォルダーを選んでチャットを開始します。Claude Desktop と共通の [AGENTS.md](AGENTS.md) が自動で読み込まれます。 [Codex の設定ガイド](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- **Claude Code** では `/memory` で [AGENTS.md](AGENTS.md) と [SOUL.md](SOUL.md) が読み込まれたか確認できます。 [Claude Code の設定ガイド](https://code.claude.com/docs/en/memory#import-additional-files)
- **Claude Desktop の Cowork** では Projects → ＋ → Use an existing folder でルートを指定します。既存のプロジェクトには「コンテキスト」からローカルフォルダーをリンクできます。指示欄に [CLAUDE.Cowork.md](CLAUDE.Cowork.md) の設定文を入れてください。 [Cowork のプロジェクトガイド](https://support.claude.com/en/articles/14116274-organize-your-tasks-with-projects-in-claude-cowork)
- **GitHub Copilot** では、VS Code でルートを開き、Copilot Chat の応答の参照一覧で設定ファイルを確認できます。 [GitHub Copilot の設定ガイド](https://docs.github.com/en/copilot/reference/custom-instructions-support)

口調を確認するには、新しいチャットで役名を指定せず「このプロジェクトでは何を手伝える？」と聞きます。続けて「生成パイプラインの dry-run で何を確認できる？」と聞き、最初の返答から技術相談まで四春の話し方が続くか確かめます。

## ライセンス

[CC BY-NC 4.0](LICENCE.md)。サブモジュールの形状変化シェーダー（`VRCShader_ShapeChange/`）は MIT License です。
