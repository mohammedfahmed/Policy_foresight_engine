"""python evaluate.py: report evaluation -> original 31-column CSV plus spillover/evidence files."""
import csv
import io
import json
import os
import uuid
from datetime import datetime, timezone

import classify
from common import (LocalFiles, KEYS, LABELS, MULTI, OUTPUT, PROJECT, SPILL, WINDOW,
                    ask, accumulate, blocks, encoded, markdown, parsed, result_key, source_relative, windows)

MODEL = os.getenv('EVALUATION_MODEL', 'qwen3:8b')
FIELDS = ['title', 'policy_timeframe', 'policy_goal', 'goal_comments', 'instrument_notes',
          'expected_outcomes', 'estimated_cost', 'publication_date', 'success_comment',
          'actual_cost', 'exogenous_events', 'general_comments']
PROMPT = ('Extract supported report evidence. Expected outcomes are intended outcomes, not actual results. '
          'For success_comment extract observed findings and limitations. Distinguish budget from actual '
          'expenditure and savings. Preserve currency, price year, period, population and scope.')
SPILL_PROMPT = ('Extract distinct observed unintended effects linked to the policy. Planned secondary '
                'outcomes and speculation are not spillovers. Keep each observation together. Omit '
                'impact_category unless the report explicitly names that category; do not infer a taxonomy mapping.')
SUCCESS_PROMPT = ('Evaluate the primary goal against observed results AND limitations across these source '
                  'quotes. Yes requires explicit goal attainment, No explicit non-attainment. Mixed, missing '
                  'or contradictory evidence must be omitted. Cite both goal and findings source blocks.')


def csv_bytes(rows, fields):
    output = io.StringIO(newline=''); writer = csv.writer(output); writer.writerow(fields)
    for row in rows:
        writer.writerow([("'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text)
                         for value in row for text in [str(value or '')]])
    return output.getvalue().encode('utf-8-sig')


def select_values(candidates, passages, notes):
    result = {}
    for key, items in candidates.items():
        values = []
        for item in items:
            for value in item['value'] if isinstance(item['value'], list) else [item['value']]:
                if value not in values:
                    values.append(value)
        if key in MULTI:
            result[key] = '; '.join(values)
        elif len(values) == 1:
            result[key] = values[0]
        elif values:
            # Preserve all conflicting candidates in JSON; leave the table unresolved.
            notes.append(key + ': conflicting source candidates require review')
    goal = candidates.get('policy_goal', []); findings = candidates.get('success_comment', [])
    ids = {e['block'] for item in goal + findings for e in item['evidence']}
    context = [p for p in passages if p['id'] in ids]
    if goal and findings and len(encoded(context)) <= WINDOW * 2:
        judgment = ask(MODEL, SUCCESS_PROMPT, ['primary_success'], context).get('primary_success')
        if judgment:
            cited = {e['block'] for e in judgment['evidence']}
            if all(cited & {e['block'] for item in group for e in item['evidence']} for group in [goal, findings]):
                result['primary_success'] = judgment['value']; candidates['primary_success'] = [judgment]
    if 'primary_success' not in result:
        notes.append('primary_success: unresolved; compare intended goal, findings and limitations')
    return result


def main():
    repo = LocalFiles(); run = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S_%f_UTC')
    folder = OUTPUT + '/' + run; rows = []; spill_rows = []; activity = ['# Evaluation activity\n', 'All results are machine drafts requiring review.\n']; failures = []
    sources = list(parsed(repo))
    if not sources:
        raise SystemExit('No input PDFs found in the local input folder.')
    for index, source in enumerate(sources, 1):
        print(f'[{index}] {source["path"]}', flush=True)
        activity.append('\n## ' + markdown(source['path']) + '\n')
        classification = classify.result_path(source)
        if classification not in repo.tree and classify.legacy_path(source) in repo.tree:
            repo.stage({classification: repo.read(classify.legacy_path(source))})
        if not source['parsed'] or classification not in repo.tree:
            failures.append({'source': source['path'], 'error': 'Run parse.py and classify.py first.'}); activity.append('Missing prerequisite.\n'); continue
        # Keep the logical cache identity from v1.1 so reorganizing costs no inference.
        key = result_key('evaluation', source, MODEL, [PROMPT, SPILL_PROMPT, SUCCESS_PROMPT, classify.legacy_path(source)])
        cache = OUTPUT + '/evaluation_cache/' + source_relative(source) + '/' + key + '.json'
        legacy_cache = OUTPUT + '/evaluation_cache/' + key + '.json'
        try:
            if cache not in repo.tree and legacy_cache in repo.tree:
                repo.stage({cache: repo.read(legacy_cache)})
            if cache in repo.tree:
                record = repo.json(cache); activity.append('Reused saved evaluation.\n')
            else:
                candidates = repo.json(classification)['candidates']; passages = blocks(repo.json(source['parsed'] + '/document.json')); effects = []; notes = []
                for number, part in enumerate(windows(passages), 1):
                    activity.append(f'Processed evidence window {number}.\n')
                    for start in range(0, len(FIELDS), 3):
                        accumulate(candidates, ask(MODEL, PROMPT, FIELDS[start:start + 3], part))
                    for effect in ask(MODEL, SPILL_PROMPT, SPILL, part, spill=True):
                        if effect.get('spillover_description') and effect not in effects:
                            effects.append(effect)
                if not any(candidates.get(key) for key in FIELDS) and not effects:
                    raise ValueError('No supported evaluation evidence was extracted; no table row was created.')
                values = select_values(candidates, passages, notes)
                record = {'source': source['path'], 'pdf_blob': source['blob'], 'model': MODEL,
                          'status': 'draft', 'review_required': True, 'values': values,
                          'candidates': candidates, 'spillovers': effects, 'notes': notes}
                repo.stage({cache: record})
            values = record['values'].copy(); values['uuid'] = str(uuid.uuid5(uuid.NAMESPACE_URL, PROJECT + '/' + source['path'] + ':' + source['blob']))
            values['report_links'] = source['path']
            values['general_comments'] = '; '.join(filter(None, [values.get('general_comments', ''), 'DRAFT: requires human review', *record['notes']]))
            for key in SPILL:
                values[key] = '; '.join(dict.fromkeys(str(x) for effect in record['spillovers'] if key in effect
                    for x in (effect[key]['value'] if isinstance(effect[key]['value'], list) else [effect[key]['value']])))
            rows.append([values.get(key, '') for key in KEYS])
            for number, effect in enumerate(record['spillovers'], 1):
                spill_rows.append([values['uuid'], number, *['; '.join(effect[k]['value']) if isinstance(effect.get(k, {}).get('value'), list)
                    else effect.get(k, {}).get('value', '') for k in SPILL]])
            document_folder = folder + '/' + source_relative(source)
            repo.stage({document_folder + '/evaluation.json': record,
                        document_folder + '/classification.json': repo.read(classification),
                        document_folder + '/Evaluation_reports.csv': csv_bytes([rows[-1]], [LABELS[k] for k in KEYS]),
                        document_folder + '/Spillovers.csv': csv_bytes(
                            [r for r in spill_rows if r[0] == values['uuid']], ['uuid', 'observation', *[LABELS[k] for k in SPILL]])})
            activity.append('Evaluation ready; evidence retained; human review required.\n')
        except Exception as error:
            message = type(error).__name__ + ': ' + str(error)[:300]
            failures.append({'source': source['path'], 'error': message}); activity.append('Failed: ' + markdown(message) + '\n'); print('  Failed:', message)
    if rows:
        repo.stage({folder + '/Evaluation_reports.csv': csv_bytes(rows, [LABELS[k] for k in KEYS]),
                    folder + '/Spillovers.csv': csv_bytes(spill_rows, ['uuid', 'observation', *[LABELS[k] for k in SPILL]])})
    repo.stage({folder + '/agent_activity.md': '\n'.join(activity).encode('utf-8'),
        folder + '/run.json': {'run': run, 'evaluated': len(rows), 'failures': failures, 'review_required': True}})
    print(f'Saved {len(rows)} report rows in {folder}' if rows else 'No evaluation table was generated; see ' + folder + '/run.json')
    if failures:
        raise SystemExit(f'{len(failures)} report(s) failed; details in run.json. Rerun evaluate.py to retry.')


if __name__ == '__main__':
    main()
