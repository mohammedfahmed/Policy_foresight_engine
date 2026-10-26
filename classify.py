"""python classify.py: parsed reports -> evidence-backed category proposals."""
import os

from common import LocalFiles, OUTPUT, ask, accumulate, blocks, parsed, result_key, source_relative, windows

MODEL = os.getenv('CLASSIFICATION_MODEL', 'qwen3:4b')
FIELDS = ['departments', 'methods', 'sponsoring_department', 'policy_domain', 'co_sponsors',
          'policy_instrument', 'outcome_domains', 'ai_enabled']
PROMPT = ('Categorize only explicitly supported report content using the allowed labels. Methods mean '
          'methods actually used, not methods mentioned in background. Outcome domains refer to intended '
          'outcomes. AI-enabled Yes needs explicit use of AI in the evaluated policy, No explicit contrary '
          'evidence. Omit missing/uncertain labels. Do not infer policy success from this window.')


def result_path(source):
    return OUTPUT + '/classification/' + source_relative(source) + '/' + result_key('classification', source, MODEL, PROMPT) + '.json'


def legacy_path(source):
    return OUTPUT + '/classification/' + result_key('classification', source, MODEL, PROMPT) + '.json'


def main():
    repo = LocalFiles(); failures = []
    sources = list(parsed(repo))
    if not sources:
        raise SystemExit('No input PDFs found in the local input folder.')
    for index, source in enumerate(sources, 1):
        print(f'[{index}] {source["path"]}', flush=True)
        path = result_path(source)
        if path in repo.tree:
            print('  Already classified: reuse.'); continue
        if not source['parsed']:
            failures.append(source['path']); print('  Run parse.py first.'); continue
        try:
            if legacy_path(source) in repo.tree:
                repo.stage({path: repo.read(legacy_path(source))})
                print('  Reused existing classification in mirrored folder.'); continue
            candidates = {}; parts = list(windows(blocks(repo.json(source['parsed'] + '/document.json'))))
            if not parts:
                raise ValueError('Parsed document has no usable text/table blocks.')
            for number, passages in enumerate(parts, 1):
                print(f'  Window {number}/{len(parts)}', flush=True)
                for start in range(0, len(FIELDS), 3):
                    accumulate(candidates, ask(MODEL, PROMPT, FIELDS[start:start + 3], passages))
            repo.stage({path: {'status': 'draft', 'source': source['path'], 'pdf_blob': source['blob'],
                              'model': MODEL, 'review_required': True, 'windows': len(parts),
                              'candidates': candidates}})
        except Exception as error:
            failures.append(source['path']); print('  Failed:', type(error).__name__, str(error)[:300])
    if failures:
        raise SystemExit(f'{len(failures)} report(s) need attention. Rerun classify.py to retry.')
    print('Classification saved to the local output folder. Next: python evaluate.py')


if __name__ == '__main__':
    main()
