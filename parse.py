"""python parse.py: local input PDFs -> reusable Docling results in parse/."""
import hashlib
import importlib.metadata
import tempfile
from pathlib import Path

from common import LocalFiles, PARSE, encoded, parsed, source_relative


def main():
    repo = LocalFiles(); converter = None; failed = []
    sources = list(parsed(repo))
    if not sources:
        raise SystemExit('No input PDFs found in the local input folder.')
    for index, source in enumerate(sources, 1):
        print(f'[{index}/{len(sources)}] {source["path"]}', flush=True)
        try:
            folder = PARSE + '/' + source_relative(source)
            if source['parsed']:
                old = source['parsed']
                if old != folder:
                    manifest = repo.json(old + '/manifest.json')
                    files = {name: repo.read(old + '/' + name) for name in manifest['artifacts']
                             if old + '/' + name in repo.tree}
                    manifest.update(original_source=manifest['source'], source=source['path'],
                                    pdf_blob=source['blob'], reused_from=old)
                    manifest['artifacts'] = {k: v for k, v in manifest['artifacts'].items() if k in files}
                    repo.stage({**{folder + '/' + name: data for name, data in files.items()},
                                folder + '/manifest.json': manifest})
                print('  Already parsed: reuse.'); continue
            if converter is None:
                from docling.document_converter import DocumentConverter
                converter = DocumentConverter()
            with tempfile.TemporaryDirectory() as temp:
                pdf = Path(temp) / 'report.pdf'; pdf.write_bytes(repo.read(source['path']))
                result = converter.convert(pdf)
                if str(getattr(result.status, 'value', result.status)) != 'success':
                    raise ValueError('Docling conversion was not fully successful.')
                files = {'document.json': encoded(result.document.export_to_dict()),
                         'document.md': result.document.export_to_markdown().encode('utf-8')}
            manifest = {'status': 'success', 'pdf_blob': source['blob'], 'source': source['path'],
                'docling_version': importlib.metadata.version('docling'), 'artifacts': {
                name: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
                for name, data in files.items()}}
            repo.stage({**{folder + '/' + name: data for name, data in files.items()},
                        folder + '/manifest.json': manifest})
            # Also reuse duplicates with different filenames in the same input run.
            for other in sources[index:]:
                if other['blob'] == source['blob']:
                    other['parsed'] = folder
        except Exception as error:
            failed.append(source['path']); print('  Failed:', type(error).__name__, str(error)[:300])
    if failed:
        raise SystemExit(f'{len(failed)} PDF(s) failed. Rerun parse.py to retry; successful PDFs are reused.')
    print('Parsing saved to the local parse folder.')


if __name__ == '__main__':
    main()
