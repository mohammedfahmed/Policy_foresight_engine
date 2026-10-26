"""Local-folder pipeline tests with simulated Docling/Ollama only."""
import contextlib,csv,hashlib,io,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import common,parse,classify,evaluate


def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def response(data,status=200):
    import requests
    r=requests.Response();r.status_code=status;r._content=common.encoded(data);r.url='http://fixture';return r

TEXT=['Programme evaluation. The programme aimed to reduce hospital admissions.',
      'Methods used individual interviews. The programme budget was £100000 for 2020; actual expenditure £90000.',
      'The evaluation found no reduction in hospital admissions. Unplanned reporting duties increased staff workload.',
      'No AI was used in the evaluated programme.']
DOC={'schema_name':'DoclingDocument','texts':[{'self_ref':f'#/texts/{i}','label':'text','text':t,'prov':[{'page_no':i+1}]} for i,t in enumerate(TEXT)],'tables':[]}


class Scripts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'input').mkdir();(self.root/'input/report.pdf').write_bytes(b'%PDF fixture')
        self.calls=0;self.parses=0
        for p in [patch.object(common,'ROOT',self.root),patch.object(common,'WORK',self.root/'.work'),
                  patch.dict(os.environ,{'GITHUB_TOKEN':''}),
                  patch.object(common.requests,'Session',side_effect=AssertionError('No GitHub/API session allowed')),
                  patch.object(common.requests,'post',side_effect=self.model),patch('importlib.metadata.version',return_value='2.fixture')]:
            p.start();self.addCleanup(p.stop)
        owner=self
        class Converter:
            def convert(self,path):
                owner.parses+=1
                assert path.resolve()!=(owner.root/'input/report.pdf').resolve()
                return types.SimpleNamespace(status=types.SimpleNamespace(value='success'),document=types.SimpleNamespace(export_to_dict=lambda:DOC,export_to_markdown=lambda:'\n'.join(TEXT)))
        module=types.ModuleType('docling.document_converter');module.DocumentConverter=Converter
        p=patch.dict(sys.modules,{'docling':types.ModuleType('docling'),'docling.document_converter':module});p.start();self.addCleanup(p.stop)
        quiet=contextlib.redirect_stdout(io.StringIO());quiet.__enter__();self.addCleanup(quiet.__exit__,None,None,None)
    def model(self,url,**kw):
        self.calls+=1;payload=kw['json'];data=json.loads(payload['messages'][-1]['content']);passages=data['passages'];self.assertFalse(payload['think'])
        def field(value,needle):
            p=next((p for p in passages if needle in p['text']),passages[0])
            return {'value':value,'evidence':[{'block':p['id'],'quote':p['text'][:400]}]}
        known={'title':('Programme evaluation','Programme'),'policy_goal':('Reduce hospital admissions','aimed'),
               'methods':(['Individual interviews'],'Methods'),'ai_enabled':('No','No AI'),
               'estimated_cost':('£100000 for 2020','budget'),'actual_cost':('£90000','budget'),
               'success_comment':('No reduction in hospital admissions','evaluation found')}
        if payload['format']['type']=='array':answer=[{'spillover_description':field('Increased staff workload','staff workload'),'spillover_type':field('Negative','staff workload')}]
        elif 'primary_success' in data['fields']:
            answer={'primary_success':field('No','evaluation found')};goal=next(p for p in passages if 'aimed' in p['text']);answer['primary_success']['evidence'].append({'block':goal['id'],'quote':goal['text']})
        else:answer={key:field(*known[key]) for key in data['fields'] if key in known}
        return response({'done':True,'done_reason':'stop','message':{'content':json.dumps(answer)}})
    def test_pipeline_local_outputs_columns_and_reuse_without_github_token(self):
        parse.main();classify.main();evaluate.main()
        tables=list((self.root/'output').glob('*/Evaluation_reports.csv'));self.assertEqual(len(tables),1)
        rows=list(csv.reader(io.StringIO(tables[0].read_text(encoding='utf-8-sig'))))
        self.assertEqual(rows[0],common.BOOK['headers']);self.assertEqual(len(rows[1]),31)
        self.assertEqual(rows[1][18],'No');self.assertEqual(rows[1][4],'input/report.pdf');self.assertIn('DRAFT',rows[1][-1])
        self.assertTrue(list((self.root/'output').glob('*/agent_activity.md')))
        self.assertEqual((self.root/'input/report.pdf').read_bytes(),b'%PDF fixture')
        calls=self.calls;parse.main();classify.main();evaluate.main();self.assertEqual(self.parses,1);self.assertEqual(self.calls,calls)
    def test_works_from_another_working_directory(self):
        old=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory);parse.main();classify.main();evaluate.main()
                self.assertFalse((Path(directory)/'output').exists())
                self.assertTrue(list((self.root/'output').glob('*/Evaluation_reports.csv')))
            finally:os.chdir(old)
    def test_changed_pdf_gets_new_parse(self):
        parse.main();(self.root/'input/report.pdf').write_bytes(b'%PDF changed');parse.main();self.assertEqual(self.parses,2)
    def test_legacy_parse_is_reused(self):
        raw=common.encoded(DOC);sha=blob((self.root/'input/report.pdf').read_bytes())
        meta={'status':'success','source':{'git_blob':sha},'artifacts':{'document.json':{'sha256':hashlib.sha256(raw).hexdigest()}}}
        folder=self.root/'parse/legacy';folder.mkdir(parents=True);(folder/'document.json').write_bytes(raw);(folder/'manifest.json').write_bytes(common.encoded(meta))
        parse.main();self.assertEqual(self.parses,0)
    def test_parsed_write_failure_blocks_next_stage(self):
        with patch.object(parse.LocalFiles,'stage',side_effect=OSError('Disk full')):
            with self.assertRaises(SystemExit):parse.main()
        with self.assertRaises(SystemExit):classify.main()
        self.assertEqual(self.calls,0);self.assertFalse(list((self.root/'parse').glob('*/manifest.json')))
    def test_input_and_traversal_writes_are_refused(self):
        files=common.LocalFiles()
        for path in ['input/report.pdf','parse/../input/report.pdf','parse/../../outside.txt']:
            with self.assertRaises(ValueError):files.stage({path:b'changed'})
        self.assertEqual((self.root/'input/report.pdf').read_bytes(),b'%PDF fixture')
    def test_changed_file_during_run_is_rejected(self):
        files=common.LocalFiles();files.sha('input/report.pdf');(self.root/'input/report.pdf').write_bytes(b'%PDF changed')
        with self.assertRaisesRegex(ValueError,'changed during'):files.read('input/report.pdf')
    def test_symlink_to_input_is_not_overwritten(self):
        files=common.LocalFiles();link=self.root/'output/source.pdf';link.symlink_to(self.root/'input/report.pdf')
        with self.assertRaises(ValueError):files.stage({'output/source.pdf':b'changed'})
        self.assertEqual((self.root/'input/report.pdf').read_bytes(),b'%PDF fixture')
    def test_bad_quote_is_not_cached(self):
        bad=response({'done':True,'message':{'content':json.dumps({'title':{'value':'X','evidence':[{'block':'b','quote':'invented'}]}})}})
        with patch.object(common.requests,'post',return_value=bad),self.assertRaises(ValueError):common.ask('qwen3:8b','test',['title'],[{'id':'b','pages':[1],'text':'Real source'}])
        self.assertFalse(list((self.root/'.work/model').glob('*.json')))
    def test_truncation_is_discarded_and_recovery_cached(self):
        calls=[];real=self.model
        def model(url,**kw):
            data=json.loads(kw['json']['messages'][-1]['content']);calls.append(data)
            if len(data['fields'])>1:return response({'done':True,'done_reason':'length','message':{'content':'{}'}})
            return real(url,**kw)
        passages=common.blocks(DOC)
        with patch.object(common.requests,'post',side_effect=model):
            answer=common.ask('qwen3:8b','test',['title','policy_goal'],passages);self.assertEqual(set(answer),{'title','policy_goal'});count=len(calls)
            self.assertEqual(common.ask('qwen3:8b','test',['title','policy_goal'],passages),answer);self.assertEqual(len(calls),count)
    def test_missing_prerequisites_only_save_diagnostics(self):
        with self.assertRaises(SystemExit):evaluate.main()
        self.assertTrue(list((self.root/'output').glob('*/run.json')));self.assertFalse(list((self.root/'output').glob('*/Evaluation_reports.csv')))
    def test_nested_folders_same_filenames_and_duplicate_content(self):
        additions={'Health/2024/report.pdf':b'%PDF fixture', 'Economy/report.pdf':b'%PDF other',
                   'Health/Space name/تقرير.PDF':b'%PDF Arabic'}
        for relative,raw in additions.items():
            path=self.root/'input'/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        originals={p:p.read_bytes() for p in (self.root/'input').rglob('*') if p.is_file()}
        parse.main();classify.main();evaluate.main()
        self.assertEqual(self.parses,3)
        run=next(p.parent for p in (self.root/'output').glob('*/Evaluation_reports.csv'))
        for relative in ['report.pdf',*additions]:
            parsed_folder=self.root/'parse'/relative
            self.assertTrue((parsed_folder/'document.json').is_file())
            self.assertTrue((parsed_folder/'document.md').is_file())
            self.assertEqual(json.loads((parsed_folder/'manifest.json').read_bytes())['source'],'input/'+relative)
            report=run/relative
            self.assertTrue((report/'evaluation.json').is_file())
            self.assertTrue((report/'classification.json').is_file())
            self.assertTrue((report/'Spillovers.csv').is_file())
            rows=list(csv.reader(io.StringIO((report/'Evaluation_reports.csv').read_text(encoding='utf-8-sig'))))
            self.assertEqual(len(rows),2);self.assertEqual(rows[1][4],'input/'+relative)
            self.assertTrue(list((self.root/'output/classification'/relative).glob('*.json')))
            self.assertTrue(list((self.root/'output/evaluation_cache'/relative).glob('*.json')))
        self.assertEqual(len(list(csv.reader(io.StringIO((run/'Evaluation_reports.csv').read_text(encoding='utf-8-sig'))))),5)
        for path,raw in originals.items():self.assertEqual(path.read_bytes(),raw)
        calls=self.calls;parse.main();classify.main();evaluate.main()
        self.assertEqual(self.parses,3);self.assertEqual(self.calls,calls)
    def test_flat_v11_caches_are_reorganized_without_inference(self):
        parse.main();classify.main();evaluate.main();calls=self.calls
        original=self.root/'parse/report.pdf';legacy=self.root/'parse'/blob(b'%PDF fixture')
        original.rename(legacy)
        for directory in ['classification','evaluation_cache']:
            parent=self.root/'output'/directory/'report.pdf'
            for cached in parent.glob('*.json'):cached.rename(parent.parent/cached.name)
            parent.rmdir()
        parse.main();classify.main();evaluate.main()
        self.assertEqual(self.parses,1);self.assertEqual(self.calls,calls)
        self.assertTrue((self.root/'parse/report.pdf/document.json').is_file())
        self.assertTrue(list((self.root/'output/classification/report.pdf').glob('*.json')))
        self.assertTrue(list((self.root/'output/evaluation_cache/report.pdf').glob('*.json')))
    def test_csv_safety_and_conflicting_values(self):
        rows=list(csv.reader(io.StringIO(evaluate.csv_bytes([['=1+1','@x','No']],['a','b','c']).decode('utf-8-sig'))));self.assertEqual(rows[1],["'=1+1","'@x",'No'])
        notes=[];self.assertNotIn('actual_cost',evaluate.select_values({'actual_cost':[{'value':'£1'},{'value':'£2'}]},[],notes));self.assertTrue(notes)


if __name__=='__main__':unittest.main()
