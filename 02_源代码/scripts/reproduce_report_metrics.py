"""Recompute report metrics from frozen labels; no model calls or news-corpus access."""
import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def norm(value):
    return re.sub(r'\s+', '', str(value or ''))


def code(value):
    return str(value or '').split('.')[0].zfill(6) if value else ''


def target(prediction, reference):
    kind = 'company' if reference['sheet'] == '个股新闻' else 'industry'
    if prediction.get('target_type') != kind:
        return False
    expected = code(reference.get('trading_code') if kind == 'company' else reference.get('industry_code'))
    name = reference.get('secu_abbr') if kind == 'company' else reference.get('industry_name')
    return bool(expected and code(prediction.get('target_code')) == expected
                or name and norm(prediction.get('target_name')) == norm(name))


def calculate(records):
    rows = []
    for article in records:
        # Match the original scorer: duplicate reference labels count once.
        references = list({(r['sheet'], r.get('trading_code'), r['industry_code'], r['event_name'], r['event_emotion']): r
                           for r in article['reference']}.values())
        if not references:
            raise ValueError('Each article must have reference labels')
        decisions = []
        for reference in references:
            matched = [p for p in article['prediction'] if target(p, reference)]
            decisions.append((bool(matched),
                bool(matched) and {p['emotion'] for p in matched} == {reference['event_emotion']},
                bool(matched) and norm(reference['event_name']) in {norm(p['event_name']) for p in matched}))
        rows.append(dict(id=article['id'], sheet=references[0]['sheet'],
            object_correct=all(d[0] for d in decisions), emotion_correct=all(d[1] for d in decisions),
            event_exact=all(d[2] for d in decisions), all_three_match=all(all(d) for d in decisions)))
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate article ids')
    result = dict(tested=len(rows), **{name: sum(r[name] for r in rows)
                  for name in ('object_correct', 'emotion_correct', 'event_exact', 'all_three_match')})
    result['by_reference_sheet'] = {sheet: dict(total=len(group),
        **{name: sum(r[name] for r in group) for name in ('object_correct', 'emotion_correct', 'event_exact')})
        for sheet in ('个股新闻', '行业新闻')
        for group in [[r for r in rows if r['sheet'] == sheet]]}
    return result


def main():
    data = json.loads((ROOT / 'reproducibility/report-metrics.json').read_text('utf8'))
    actual = calculate(data['records'])
    if actual != data['expected_conversation']:
        raise SystemExit('Historical label metrics differ from the frozen report')
    execution = data['execution']
    runtime = dict(tested=len(execution), archives_available=sum(r['archives'] > 0 for r in execution),
        calls=sum(r['calls'] for r in execution), max_calls=max(r['calls'] for r in execution),
        mean_seconds=round(statistics.mean(r['seconds'] for r in execution), 2),
        median_seconds=round(statistics.median(r['seconds'] for r in execution), 2),
        max_seconds=max(r['seconds'] for r in execution))
    if runtime != data['expected_runtime']:
        raise SystemExit('Historical runtime metrics differ from the frozen report')
    print(json.dumps(dict(conversation_label_metrics=actual, historical_program_runtime=runtime,
                         paid_model_calls_this_run=0, verified=True), ensure_ascii=False, indent=2))
    print('历史记录统计复算通过；对话识别成绩不代表程序自动准确率，历史耗时不是本次运行耗时。')


if __name__ == '__main__':
    main()
