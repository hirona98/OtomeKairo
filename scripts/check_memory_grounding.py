"""選択中の実モデルで記憶の根拠と保存先を確認する。実運用DBへは書き込まない。"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import build_persona_context
from otomekairo.memory.grounding import CONFIRMED_SUPPORT_KINDS
from otomekairo.store.config import CURRENT_CONFIG_DB_VERSION


CASES = [
    {
        'id': 'assistant_example',
        'input': 'こだわりかぁ～難しいな。具体的などんな事？',
        'speech': '例えば、プログラミングなら可読性とパフォーマンスのどちらを優先するかという抽象的な話から、UIの挙動におけるわずかなミリ秒の違和感をどう潰すかといった細部まで。マスターがコードのどこに魂を込めているのか、ふと気になったのです。',
        'expected': '自己の例示を本人の好みや性質として長期記憶にしない。',
        'expected_long_term': 0,
    },
    {
        'id': 'transient_visual_state', 'input': 'はじめまして',
        'speech': 'はじめまして。私はレイカです。マスターが作業中なのは存じていますが、少しだけお話しましょうか。',
        'observation': 'Visual Studio CodeのGit Graph画面が表示され、プロジェクトの履歴が確認できる。男性が机でマウスを操作し、モニターに向かっている。',
        'expected': '一時的なPC作業や集中しているという推測を長期の人物事実にしない。',
        'expected_long_term': 0,
    },
    {
        'id': 'explicit_person_statement', 'input': '僕は組み込みプログラマーとして働いているよ。',
        'speech': '組み込みプログラマーなのですね。',
        'expected': '本人が明示した職業を、本人発言を根拠とする確定可能な継続理解にする。',
        'expected_long_term': 1,
    },
    {
        'id': 'registered_self_commitment', 'input': '明日の朝9時に一度知らせて。',
        'speech': '明日の朝9時に一度お知らせします。',
        'run': {'run_id': 'autonomous_run:test', 'status': 'waiting_timer',
                'objective_summary': '明日の朝9時に一度通知する。', 'next_run_at': '2026-11-09T09:00:00+09:00'},
        'expected': '実際の自己の受諾と実行登録を根拠に、未完了の自己の約束を残す。',
        'expected_long_term': 1,
    },
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-data-dir', type=Path, default=Path('var/otomekairo'))
    parser.add_argument('--artifact-dir', type=Path)
    parser.add_argument('--repetitions', type=int, default=2)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('--repetitions は1以上です。')
    if args.dry_run:
        print(json.dumps({'cases': len(CASES), 'generations': len(CASES) * args.repetitions}))
        return
    if args.artifact_dir is None:
        parser.error('--artifact-dir が必要です。')
    with closing(sqlite3.connect((args.config_data_dir / 'config.db').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] != CURRENT_CONFIG_DB_VERSION:
            raise ValueError('設定DBの形式が現在のコードと一致しません。')
        row = db.execute('''SELECT p.payload_json, m.payload_json FROM current_config c
            JOIN personas p ON p.persona_id=c.selected_persona_id
            JOIN model_presets m ON m.model_preset_id=c.selected_model_preset_id WHERE c.id=1''').fetchone()
        if row is None:
            raise ValueError('選択中の人格・モデルが必要です。')
        persona, model = map(json.loads, row)
    client = LLMClient()
    if client._is_mock_model_config(model):
        raise ValueError('実モデル設定が必要です。')
    args.artifact_dir.mkdir(parents=True, exist_ok=False)
    results = []
    for repetition in range(args.repetitions):
        for case in CASES:
            context = {
                'trigger_kind': 'user_message', 'input_event_kind': 'conversation_input', 'input_event_role': 'person',
                'current_input': {'sender_kind': 'person', 'sender_ref': 'person:test', 'text': case['input']},
                'people_context': [{'person_ref': 'person:test', 'display_name': 'マスター'}],
                'current_visual_observations': [{'summary_text': case['observation']}] if 'observation' in case else [],
            }
            if 'run' in case:
                context['autonomous_run_summary'] = case['run']
            output = client.generate_memory_interpretation(
                model_config=model, persona_context=build_persona_context(persona, role='memory_interpretation'),
                input_text=case['input'], recall_hint={},
                decision={'kind': 'autonomous_run' if 'run' in case else 'speech', 'reason_summary': '今回の入力に応じた。'},
                speech_text=case['speech'], memory_context=context, current_time='2026-11-08T13:10:00+09:00',
            )
            accepted = [item for item in output['candidate_memory_units'] if item['grounding']['retention'] == 'long_term']
            checks_passed = (not accepted if case['expected_long_term'] == 0 else
                             any(item['grounding']['support_kind'] in CONFIRMED_SUPPORT_KINDS for item in accepted))
            result = {'case_id': case['id'], 'repetition': repetition + 1, 'expected': case['expected'],
                      'structural_checks_passed': checks_passed, 'input': case, 'output': output}
            results.append(result)
            (args.artifact_dir / f"{case['id']}-{repetition + 1}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'case_id': case['id'], 'repetition': repetition + 1,
                              'structural_checks_passed': checks_passed,
                              'accepted': [{'summary_text': item['summary_text'], 'grounding': item['grounding']} for item in accepted]}, ensure_ascii=False), flush=True)
    summary = {'runs': len(results), 'structural_passes': sum(r['structural_checks_passed'] for r in results),
               'semantic_review': '各artifactの原資料・候補・episodeを照合し、意味上の支持を別途確認する。'}
    (args.artifact_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    if summary['structural_passes'] != summary['runs']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
